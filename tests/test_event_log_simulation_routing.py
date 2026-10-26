"""Step-by-step transition routing in what-if simulation (#370 R14)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import random

import pytest

from server import event_log_simulation as sim
from server import event_log_simulation_routing as routing
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
P_FIX = 0.3


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _rework(cases: int = 3000, seed: int = 1) -> str:
    """Receive → Check, then after each Check: Fix → Check again with probability 0.3, else Close."""
    rng, start, rows = random.Random(seed), datetime(2024, 1, 1, tzinfo=timezone.utc), ["case,activity,time"]
    for i in range(cases):
        t = start + timedelta(hours=i)
        steps = ["Receive", "Check"]
        while rng.random() < P_FIX:
            steps += ["Fix", "Check"]
        steps.append("Close")
        for k, a in enumerate(steps):
            rows.append(f"{i},{a},{(t + timedelta(minutes=10 * k)).isoformat()}")
    raw = "\n".join(rows).encode()
    return workspace.import_log(BytesIO(raw), name="rework", source_format="csv", mapping=MAPPING)["log_id"]


def test_transitions_are_measured_from_every_eligible_case():
    log = _rework()
    paths = sim.calibrate(log, max_paths=2)
    steps = sim.calibrate(log, max_paths=2, routing="transitions")
    assert paths["routing"] == "paths" and paths["calibrated_cases"] < 3000
    assert steps["calibrated_cases"] == 3000 and steps["excluded_case_reasons"]["variant_limit"] == 0
    t = steps["transitions"]
    assert t["cases"] == 3000 and t["start"] == [{"activity": "Receive", "cases": 3000, "weight": 1.0}]
    after_check = {r["to"]: r["weight"] for r in t["next"] if r["from"] == "Check"}
    assert after_check["Fix"] == pytest.approx(P_FIX, abs=0.02) and set(after_check) == {"Fix", "Close"}
    assert {r["to"] for r in t["next"] if r["from"] == "Close"} == {None}   # the case ends after Close
    assert sum(after_check.values()) == pytest.approx(1)


def test_walks_repeat_rework_as_often_as_the_log_and_keep_full_coverage():
    log = _rework()
    result = sim.what_if(log, {"routing": "transitions", "cases": 5000, "seed": 2})
    visits = result["baseline"]["visits_per_case"]
    # A geometric loop: Fix is visited p/(1-p) times per case and Check 1/(1-p) times.
    assert visits["Fix"] == pytest.approx(P_FIX / (1 - P_FIX), rel=0.08)
    assert visits["Check"] == pytest.approx(1 / (1 - P_FIX), rel=0.04)
    assert visits["Receive"] == visits["Close"] == 1
    assert result["baseline"]["routes_cut_at_path_limit"] == 0
    assert result["scenario"]["routing"] == "transitions" and "paths" not in result["scenario"]
    assert any("step by step" in a for a in result["assumptions"])
    # Path routing keeps its own output; it reports no per-case visits.
    assert "visits_per_case" not in sim.what_if(log, {"cases": 500, "seed": 2})["baseline"]


def test_an_unchanged_scenario_is_identical_and_changed_times_do_not_change_routes():
    log = _rework(600)
    same = sim.what_if(log, {"routing": "transitions", "cases": 800})
    assert same["baseline"] == same["result"]
    slower = sim.what_if(log, {"routing": "transitions", "cases": 800, "activities": {"Close": {"minutes": 90}}})
    assert slower["baseline"]["visits_per_case"] == slower["result"]["visits_per_case"]
    assert slower["result"]["cycle_time_seconds"]["p50"] > slower["baseline"]["cycle_time_seconds"]["p50"]


def test_a_scenario_can_replace_one_activitys_next_steps():
    log = _rework(600)
    less_rework = sim.what_if(log, {"routing": "transitions", "cases": 2000, "transitions": [
        {"from": "Check", "to": [{"activity": "Close", "weight": 0.95}, {"activity": "Fix", "weight": 0.05}]}]})
    before, after = less_rework["baseline"]["visits_per_case"], less_rework["result"]["visits_per_case"]
    assert after["Fix"] == pytest.approx(0.05 / 0.95, abs=0.03) and after["Fix"] < before["Fix"]
    assert less_rework["median_cycle_time_change_seconds"] <= 0
    row = [r for r in less_rework["scenario"]["transitions"]["next"] if r["from"] == "Check"]
    assert {r["to"]: r["weight"] for r in row} == {"Close": 0.95, "Fix": 0.05}
    new_step = sim.what_if(log, {"routing": "transitions", "cases": 500,
                                 "activities": {"Review": {"minutes": 30, "resources": 1}},
                                 "transitions": [{"from": "Fix", "to": [{"activity": "Review", "weight": 1}]},
                                                 {"from": "Review", "to": [{"activity": "Check", "weight": 1}]}]})
    assert new_step["result"]["visits_per_case"]["Review"] == pytest.approx(new_step["result"]["visits_per_case"]["Fix"])


def test_endless_loops_stop_at_the_path_limit_and_are_counted():
    model = {"start": [("A", 1.0)], "next": {"A": [("A", 1.0)]}}
    path, cut = routing.walk(model, random.Random(1), 100)
    assert len(path) == 100 and cut
    path, cut = routing.walk({"start": [("A", 1.0)], "next": {"A": [(None, 1.0)]}}, random.Random(1), 100)
    assert path == ["A"] and not cut
    log = _rework(200)
    result = sim.what_if(log, {"routing": "transitions", "cases": 100, "transitions": [
        {"from": "Check", "to": [{"activity": "Fix", "weight": 1}]}]})
    assert result["result"]["routes_cut_at_path_limit"] == 100


def test_held_out_validation_and_uncertainty_accept_transition_routing():
    log = _rework(800)
    checked = sim.validate(log, {"routing": "transitions"})
    assert checked["routing"] == "transitions" and checked["cases"]["calibration"] > 0
    spread = sim.what_if(log, {"routing": "transitions", "cases": 300, "uncertainty": {"replications": 4}})
    assert spread["uncertainty"]["replications"] == 4
    rng = random.Random(3)
    base = sim.calibrate(log, routing="transitions")["transitions"]
    drawn = routing.draw(base, rng)
    shares = routing.build({"transitions": drawn}, None)["next"]["Check"]
    assert sum(w for _, w in shares) == pytest.approx(1) and dict(shares) != dict(
        routing.build({"transitions": base}, None)["next"]["Check"])


def _single_activity_runs(cases: int = 80, events: int = 100) -> str:
    """Every case is exactly ``events`` events of A: each observed route fits the path limit."""
    start, rows = datetime(2024, 1, 1, tzinfo=timezone.utc), ["case,activity,time"]
    for i in range(cases):
        t = start + timedelta(hours=3 * i)
        rows += [f"{i},A,{(t + timedelta(minutes=k)).isoformat()}" for k in range(events)]
    raw = "\n".join(rows).encode()
    return workspace.import_log(BytesIO(raw), name="long", source_format="csv", mapping=MAPPING)["log_id"]


def test_held_out_validation_reports_routes_stopped_at_the_path_limit():
    # A follows A 99 times in 100, so many simulated walks run past 100 steps though no observed case does.
    checked = sim.validate(_single_activity_runs(), {"routing": "transitions", "cases": 1000, "seed": 1,
                                                     "distribution": "fixed"})
    routes = checked["simulated_routes"]
    assert routes["cases_simulated"] == 1000 and routes["path_event_limit"] == sim.MAX_PATH_LENGTH
    assert 100 < routes["stopped_at_path_limit"] < 1000 and routes["stopped_routes_in_comparison"]
    stopped = [a for a in checked["assumptions"] if "were stopped there" in a]
    assert stopped == [f"{routes['stopped_at_path_limit']} of 1000 simulated cases reached the 100-event path limit "
                       "and were stopped there. These stopped prefixes are compared as if they had finished, which "
                       "shortens the simulated cycle times; the fit metrics do not assess completed routes."]
    assert set(routing.ASSUMPTIONS) <= set(checked["assumptions"])
    clean = sim.validate(_rework(800), {"routing": "transitions"})
    assert clean["simulated_routes"]["stopped_at_path_limit"] == 0
    assert not clean["simulated_routes"]["stopped_routes_in_comparison"]
    assert set(routing.ASSUMPTIONS) <= set(clean["assumptions"])
    assert not any("were stopped there" in a for a in clean["assumptions"])
    paths = sim.validate(_rework(800), {})
    assert "simulated_routes" not in paths and not set(routing.ASSUMPTIONS) & set(paths["assumptions"])


@pytest.mark.parametrize("spec, message", [
    ({"routing": "graph"}, "paths or transitions"),
    ({"transitions": [{"from": "Check", "to": [{"activity": "Close", "weight": 1}]}]}, "only with routing transitions"),
    ({"routing": "transitions", "paths": [{"activities": ["Receive"], "weight": 1}]}, "change transitions instead"),
    ({"routing": "transitions", "transitions": [{"from": "Check", "to": [{"activity": "Close", "weight": 0}]}]},
     "must not all be zero"),
    ({"routing": "transitions", "transitions": [{"from": None, "to": [{"activity": None, "weight": 1}]}]},
     "cannot end before"),
    ({"routing": "transitions", "transitions": [{"from": "Check", "to": [{"activity": "Close"}]}]}, "and a weight"),
    ({"routing": "transitions", "transitions": [{"from": "Check", "to": [{"activity": "Close", "weight": 1}]},
                                                {"from": "Check", "to": [{"activity": "Fix", "weight": 1}]}]},
     "one transitions row"),
    ({"routing": "transitions", "transitions": [{"from": "Fix", "to": [{"activity": "Audit", "weight": 1}]}]},
     "enter minutes"),
])
def test_invalid_routing_settings_are_refused(spec, message):
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        sim.what_if(_rework(100), {"cases": 100, **spec})


def _optional_checks(cases: int, seed: int) -> str:
    """Receive, then each of six checks taken independently with probability 0.3, then Close: 64 variants."""
    rng, start, rows = random.Random(seed), datetime(2024, 1, 1, tzinfo=timezone.utc), ["case,activity,time"]
    for i in range(cases):
        t = start + timedelta(hours=i)
        steps = ["Receive", *[f"Check{k}" for k in range(6) if rng.random() < 0.3], "Close"]
        for k, a in enumerate(steps):
            rows.append(f"{i},{a},{(t + timedelta(minutes=20 * k)).isoformat()}")
    raw = "\n".join(rows).encode()
    return workspace.import_log(BytesIO(raw), name="optional", source_format="csv", mapping=MAPPING)["log_id"]


def test_transitions_match_later_cases_where_frequent_paths_are_biased_short():
    log = _optional_checks(3000, seed=1)
    paths = sim.validate(log, {"distribution": "fixed", "cases": 4000})
    steps = sim.validate(log, {"routing": "transitions", "distribution": "fixed", "cases": 4000})
    # The ten most frequent of 64 variants are the ones with few checks, so path routing runs short.
    assert paths["cases"]["calibration"] < steps["cases"]["calibration"]
    assert paths["cycle_time_seconds"]["relative_error"]["mean"] < -0.2
    assert steps["cycle_time_seconds"]["relative_error"]["p50"] == 0
    assert abs(steps["cycle_time_seconds"]["relative_error"]["mean"]) < 0.15
    ks = lambda v: v["distribution_distance"]["kolmogorov_smirnov"]
    assert ks(steps) < ks(paths) / 2
