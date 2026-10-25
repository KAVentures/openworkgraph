"""Branch and arrival-rate uncertainty in what-if simulation (#370 R14)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import random
import statistics

import pytest

from server import event_log_simulation as sim
from server import event_log_simulation_uncertainty as uncertainty
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _branching(cases: int, p_long: float = 0.3, seed: int = 1) -> str:
    """Cases arrive hourly; each goes A → B (10 minutes) or, with ``p_long``, A → C (5 hours)."""
    rng, start, rows = random.Random(seed), datetime(2024, 1, 1, tzinfo=timezone.utc), ["case,activity,time"]
    for i in range(cases):
        t = start + timedelta(hours=i)
        late = rng.random() < p_long
        rows.append(f"{i},A,{t.isoformat()}")
        rows.append(f"{i},{'C' if late else 'B'},{(t + timedelta(minutes=300 if late else 10)).isoformat()}")
    raw = "\n".join(rows).encode()
    return workspace.import_log(BytesIO(raw), name="branching", source_format="csv", mapping=MAPPING)["log_id"]


def _width(interval):
    return interval["high"] - interval["low"]


def test_drawn_routing_and_arrivals_follow_their_estimation_uncertainty():
    base = {"calibrated_cases": 100, "arrivals_per_hour": 2.0,
            "paths": [{"activities": ["A", "C"], "weight": 0.3}, {"activities": ["A", "B"], "weight": 0.7}]}
    rng = random.Random(7)
    draws = [uncertainty.draw(base, rng) for _ in range(4000)]
    share = [d["paths"][0]["weight"] for d in draws]
    rate = [d["arrivals_per_hour"] for d in draws]
    # Dirichlet(30, 70): the long branch's share is Beta(30, 70); Gamma(99, 2/99) for the rate.
    assert statistics.mean(share) == pytest.approx(0.3, abs=0.005)
    assert statistics.stdev(share) == pytest.approx((0.3 * 0.7 / 101) ** 0.5, rel=0.08)
    assert statistics.mean(rate) == pytest.approx(2.0, rel=0.01)
    assert statistics.stdev(rate) == pytest.approx(2.0 / 99 ** 0.5, rel=0.08)
    assert base["paths"][0]["weight"] == 0.3   # the calibration itself is never changed


def test_routing_intervals_cover_the_true_share_at_about_the_stated_level():
    rng, covered, trials = random.Random(11), 0, 300
    for _ in range(trials):
        n, p = 60, 0.3
        k = sum(rng.random() < p for _ in range(n))
        base = {"calibrated_cases": n, "arrivals_per_hour": None,
                "paths": [{"activities": ["A", "C"], "weight": max(k, 1) / n},
                          {"activities": ["A", "B"], "weight": max(n - k, 1) / n}]}
        drawn = sorted(uncertainty.draw(base, rng)["paths"][0]["weight"] for _ in range(400))
        covered += drawn[9] <= p <= drawn[390]
    assert 0.9 <= covered / trials <= 0.99


def test_few_cases_give_wider_intervals_and_point_results_are_unchanged():
    small, large = _branching(40), _branching(2000)
    spec = {"cases": 400, "seed": 3}
    plain = sim.what_if(small, spec)
    with_spread = sim.what_if(small, {**spec, "uncertainty": {"replications": 20}})
    assert "uncertainty" not in plain
    assert with_spread["baseline"] == plain["baseline"] and with_spread["result"] == plain["result"]
    u_small = with_spread["uncertainty"]
    u_large = sim.what_if(large, {**spec, "uncertainty": {"replications": 20}})["uncertainty"]
    assert u_small["replications"] == 20 and u_small["level"] == 0.95
    long_small = next(r for r in u_small["measured_routing"] if r["activities"] == ["A", "C"])
    long_large = next(r for r in u_large["measured_routing"] if r["activities"] == ["A", "C"])
    assert _width(long_small["drawn"]) > 3 * _width(long_large["drawn"])
    assert long_large["observed_cases"] == round(long_large["weight"] * 2000)
    p90 = lambda u: _width(u["cycle_time_seconds"]["baseline"]["p90"])
    assert p90(u_small) > p90(u_large)
    assert _width(u_small["baseline_arrivals_per_hour"]) > _width(u_large["baseline_arrivals_per_hour"])
    assert any("Dirichlet" in a for a in with_spread["assumptions"])


def test_an_unchanged_scenario_has_no_change_in_any_replication():
    u = sim.what_if(_branching(60), {"cases": 300, "uncertainty": {"replications": 10}})["uncertainty"]
    assert u["cycle_time_seconds"]["change"]["p50"] == {"low": 0, "median": 0, "high": 0}
    assert _width(u["cycle_time_seconds"]["baseline"]["p50"]) >= 0
    assert u["replications_where_scenario_median_is_faster"] == 0


def test_entered_values_stay_fixed_while_measured_routing_varies(monkeypatch):
    seen = []
    real = sim.simulate

    def record(model, samples=None):
        seen.append((model["label"], tuple(round(w, 9) for _, w in model["paths"]), model["arrivals_per_hour"]))
        return real(model, samples)
    monkeypatch.setattr(sim, "simulate", record)
    log = _branching(80)
    entered = [{"activities": ["A", "B"], "weight": 1}]
    result = sim.what_if(log, {"label": "fast", "cases": 200, "paths": entered, "arrivals_per_hour": 1.5,
                               "uncertainty": {"replications": 5}})
    replicated = seen[2:]
    baseline = {(w, a) for label, w, a in replicated if label == ""}
    scenario = {(w, a) for label, w, a in replicated if label == "fast"}
    assert len(replicated) == 10 and scenario == {((1.0,), 1.5)}
    assert len(baseline) == 5   # each replication draws its own measured routing and arrival rate
    assert result["uncertainty"]["replications_where_scenario_median_is_faster"] == 5


@pytest.mark.parametrize("spec, message", [
    ({"replications": 1}, "from 2 to 50"), ({"replications": 51}, "from 2 to 50"),
    ({"replications": True}, "from 2 to 50"), ({"replications": 2.5}, "from 2 to 50"),
    ({"replications": 10, "level": 0.9}, "takes replications"), ("many", "takes replications"),
])
def test_invalid_uncertainty_settings_are_refused(spec, message):
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        sim.what_if(_branching(30), {"cases": 100, "uncertainty": spec})


def test_the_simulation_work_budget_is_enforced():
    with pytest.raises(workspace.EventLogWorkspaceError, match="400,000"):
        sim.what_if(_branching(30), {"cases": 5000, "uncertainty": {"replications": 50}})
