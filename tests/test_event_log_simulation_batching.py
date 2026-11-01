"""Batch processing in what-if simulation (#370 R14)."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_simulation as sim
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _pay(batch, cases=40000, seed=3, minutes=0.01, paths=None):
    # Poisson arrivals at 1 case/h into a payment run that takes almost no time.
    activities = {"Pay": {"minutes": minutes, "resources": 1, "batch": batch}}
    if paths:
        activities["Check"] = {"minutes": 10, "resources": 1}
    return sim.simulate({"label": "", "arrivals_per_hour": 1.0, "paths": paths or [(["Pay"], 1.0)],
                         "activities": activities, "distribution": "fixed", "cases": cases, "seed": seed,
                         "pools": {}})


def test_filling_a_batch_costs_the_waiting_queueing_theory_predicts():
    row = _pay({"size": 5})["activities"][0]
    # The j-th case of a batch of K waits for K - j more Poisson arrivals: mean (K - 1) / (2 lambda) = 2 h.
    assert row["mean_wait_seconds"] / 3600 == pytest.approx(2.0, rel=0.05)
    assert row["batch"]["mean_batch_size"] == 5 and row["batch"]["batches"] == 8000
    assert row["batch"]["waiting_in_unfilled_batch"] == 0


def test_a_maximum_wait_releases_partial_batches_on_time():
    result = _pay({"size": 100, "max_wait_minutes": 60})
    row = result["activities"][0]
    # The first case of each run waits exactly 60 min; a Poisson(1) number of later ones wait 30 min on average:
    # (60 + 30) / 2 = 45 min.
    assert row["mean_wait_seconds"] / 60 == pytest.approx(45, rel=0.05)
    assert row["p90_wait_seconds"] <= 3600 + 1e-6
    assert 1.9 < row["batch"]["mean_batch_size"] < 2.1
    assert result["cases_waiting_in_unfilled_batches"] == 0


def test_a_batch_that_never_fills_holds_its_cases_and_counts_them():
    result = _pay({"size": 10}, cases=103)
    assert result["cases_waiting_in_unfilled_batches"] == 3
    assert result["activities"][0]["batch"]["waiting_in_unfilled_batch"] == 3
    assert result["cases_measured"] == 103 - int(103 * sim.WARM_UP_SHARE) - 3


def test_batch_members_move_on_together_and_one_service_covers_the_batch():
    result = _pay({"size": 4}, cases=4000, minutes=60, paths=[(["Pay", "Check"], 1.0)])
    pay = next(r for r in result["activities"] if r["activity"] == "Pay")
    # One 60-minute run per four cases: a quarter of the per-case work, so offered load 0.25.
    assert pay["offered_load"] == pytest.approx(0.25) and pay["utilization"] == pytest.approx(0.25, abs=0.02)
    check = next(r for r in result["activities"] if r["activity"] == "Check")
    # The four members reach a one-person Check at the same moment and wait 0, 10, 20 and 30 min: 15 min on average.
    assert "batch" not in check and check["mean_wait_seconds"] / 60 == pytest.approx(15, rel=0.02)


def _log():
    return workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who"})["log_id"]


def test_a_what_if_scenario_batches_only_the_scenario():
    log = _log()
    first = sim.calibrate(log)["paths"][0]["activities"][-1]
    result = sim.what_if(log, {"cases": 1500, "activities": {first: {"batch": {"size": 3, "max_wait_minutes": 120}}}})
    assert all("batch" not in r for r in result["baseline"]["activities"])
    row = next(r for r in result["result"]["activities"] if r["activity"] == first)
    assert row["batch"]["size"] == 3 and row["batch"]["max_wait_minutes"] == 120
    assert result["scenario"]["activities"][first]["batch"] == {"size": 3, "max_wait_minutes": 120}
    assert sim.BATCH_ASSUMPTIONS[0] in result["assumptions"]
    assert any("no calendars, breaks, priorities or abandonment" in line for line in result["assumptions"])
    both = sim.what_if(log, {"cases": 300, "activities": {first: {"batch": {"size": 3, "max_wait_minutes": 120}}},
                             "priorities": [{"name": "a", "share": 0.5, "rank": 0}, {"name": "b", "share": 0.5, "rank": 1}]})
    assert any("no calendars, breaks or abandonment are modeled" in line for line in both["assumptions"])


@pytest.mark.parametrize("batch, message", [
    ({"size": 1}, "batch size"),
    ({"size": 2.5}, "whole number"),
    ({"size": 2, "max_wait_minutes": 0}, "max_wait_minutes"),
    ({"max_wait_minutes": 60}, "takes a size"),
    ({"size": 2, "every": 1}, "takes a size"),
    ("weekly", "takes a size"),
])
def test_invalid_batches_are_refused(batch, message):
    log = _log()
    first = sim.calibrate(log)["paths"][0]["activities"][-1]
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        sim.what_if(log, {"cases": 100, "activities": {first: {"batch": batch}}})


def test_a_batched_activity_cannot_be_pooled():
    log = _log()
    names = sim.calibrate(log)["paths"][0]["activities"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="cannot also be in a pool"):
        sim.what_if(log, {"cases": 100, "activities": {names[0]: {"batch": {"size": 2}}},
                          "pools": {"Team": {"resources": 2, "activities": names[:2]}}})
