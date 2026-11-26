"""R14 variability: queueing oracle, calibration, fallback and compatibility."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import random

import pytest

from server import event_log_simulation as sim
from server import event_log_simulation_variability as variability
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def local_data(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_moment_matching_and_fallbacks():
    assert variability.describe(9, 10, 5)["reason"] == "insufficient_gaps"
    assert variability.describe(10, 0, 0)["model"] == "fixed"
    assert variability.describe(10, 10, 0)["model"] == "fixed"
    assert variability.describe(10, 10, 200)["reason"] == "dispersion_outside_supported_range"
    model = variability.describe(100, 60, 30)
    assert model["shape"] == 4
    rng = random.Random(77)
    draws = [variability.service_time(60, model, rng) for _ in range(30000)]
    mean = sum(draws) / len(draws)
    variance = sum((x - mean)**2 for x in draws) / len(draws)
    assert mean == pytest.approx(60, rel=.02)
    assert variance == pytest.approx(900, rel=.06)


def test_gamma_queue_matches_pollaczek_khinchine():
    # M/G/1, arrival rate .5/h, mean 1h, CV .5: expected wait .625h.
    model = {"label": "", "arrivals_per_hour": .5, "paths": [(["A"], 1.0)],
             "activities": {"A": {"minutes": 60, "resources": 1,
                                   "service_time_model": variability.describe(100, 60, 30)}},
             "distribution": "measured_gamma", "cases": 20000, "seed": 7}
    result = sim.simulate(model)
    assert result == sim.simulate(model)
    assert result["activities"][0]["mean_wait_seconds"] / 3600 == pytest.approx(.625, rel=.15)
    assert not result["unstable"]


def log_with_gaps():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = ["case,activity,time,who"]
    for i in range(80):
        at = start + timedelta(hours=4*i)
        gap = (10 if i % 2 else 30) if i < 55 else (60 if i % 2 else 90)
        rows.extend([f"{i},Start,{at.isoformat()},r1",
                     f"{i},Finish,{(at+timedelta(minutes=gap)).isoformat()},r1"])
    return workspace.import_log(BytesIO(("\n".join(rows)+"\n").encode()), name="variability",
                                source_format="csv", mapping={"case_column":"case","activity_column":"activity",
                                "timestamp_column":"time","resource_column":"who"})["log_id"]


def test_calibrated_what_if_and_legacy_output():
    log = log_with_gaps()
    old = sim.calibrate(log)
    assert "service_time_variability" not in old
    cal = sim.calibrate(log, measure_variability=True)
    assert cal["activities"] == old["activities"]
    assert cal["service_time_variability"]["Finish"]["observed_gaps"] == 80
    result = sim.what_if(log, {"format": sim.FORMAT, "distribution":"measured_gamma",
                              "activity_time_basis":"mean", "cases":500})
    assert result["baseline"] == result["result"]
    assert result["scenario"]["activities"]["Finish"]["service_time_model"]["model"] == "gamma"
    assert result["baseline_service_time_models"]["Finish"] == result["scenario"]["activities"]["Finish"]["service_time_model"]
    assert not result["execution_authorized"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="uncertainty"):
        sim.what_if(log, {"distribution":"measured_gamma", "uncertainty":{"replications":2}})


def test_holdout_dispersion_is_measured_only_before_cutoff():
    result = sim.validate(log_with_gaps(), {"distribution":"measured_gamma","activity_time_basis":"mean",
                                "holdout_share":.4,"cases":500})
    support = result["service_time_variability"]["Finish"]
    assert support["observed_gaps"] == result["cases"]["calibration"]
    assert support["observed_gaps"] < 55
    assert support["coefficient_of_variation"] == pytest.approx(.5, abs=.03)
    assert not result["forecast_or_guarantee"]


def test_portfolio_shared_calibration_and_fallback_for_new_activity():
    from server import event_log_simulation_portfolio as portfolio
    result = portfolio.compare(log_with_gaps(), {"format":portfolio.FORMAT,
        "baseline":{"distribution":"measured_gamma","activity_time_basis":"mean","cases":300},
        "alternatives":[{"label":"More resources","activities":{"Finish":{"resources":2}}},
                        {"label":"New route","paths":[{"activities":["Start","New"],"weight":1}],
                         "activities":{"New":{"minutes":20,"resources":1}}}]})
    assert result["single_calibration_used"]
    models = result["service_time_models"]
    assert models["baseline"]["Finish"]["model"] == "gamma"
    assert models["alternatives"]["New route"]["New"]["reason"] == "insufficient_gaps"
    assert not result["execution_authorized"]
