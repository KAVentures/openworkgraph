"""Priority classes in what-if simulation (#370 R14)."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_simulation as sim
from server import event_log_simulation_priorities as priorities
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv

CLASSES = [{"name": "urgent", "share": 0.3, "rank": 0}, {"name": "standard", "share": 0.7, "rank": 1}]


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _queue(classes=None, cases=80000, seed=11):
    # One server, Poisson arrivals at 1 case/h, exponential service averaging 48 min: utilization 0.8.
    return sim.simulate({"label": "", "arrivals_per_hour": 1.0, "paths": [(["Serve"], 1.0)],
                         "activities": {"Serve": {"minutes": 48, "resources": 1}}, "distribution": "exponential",
                         "cases": cases, "seed": seed, "pools": {}, "priorities": classes})


def test_class_waits_match_cobhams_formulas_and_the_mean_wait_is_conserved():
    fcfs, ranked = _queue(), _queue(CLASSES)
    # Single-server M/M/1 at rho 0.8: Wq = 3.2 h first come, first served.
    assert fcfs["activities"][0]["mean_wait_seconds"] / 3600 == pytest.approx(3.2, rel=0.1)
    # Non-preemptive priority (Cobham): W0 = lambda E[S^2] / 2 = 0.64 h,
    # urgent W0 / (1 - 0.24) = 0.842 h, standard W0 / ((1 - 0.24)(1 - 0.8)) = 4.21 h.
    urgent, standard = ranked["priority_classes"]
    assert urgent["name"] == "urgent" and urgent["mean_wait_seconds_per_step"] / 3600 == pytest.approx(0.842, rel=0.1)
    assert standard["mean_wait_seconds_per_step"] / 3600 == pytest.approx(4.21, rel=0.1)
    # Work-conserving with equal service times: reordering the queue keeps the overall mean wait.
    assert ranked["activities"][0]["mean_wait_seconds"] / 3600 == pytest.approx(3.2, rel=0.1)
    shares = [c["cases_measured"] for c in ranked["priority_classes"]]
    assert sum(shares) == 72000 and shares[0] / sum(shares) == pytest.approx(0.3, abs=0.01)
    assert urgent["cycle_time_seconds"]["p90"] < standard["cycle_time_seconds"]["p50"] * 2


def test_classes_reorder_queues_but_never_change_arrivals_or_routes():
    plain = _queue(cases=2000)
    assert "priority_classes" not in plain
    # With no queueing at all, priorities change nothing.
    idle = sim.simulate({"label": "", "arrivals_per_hour": 0.01, "paths": [(["Serve"], 1.0)],
                         "activities": {"Serve": {"minutes": 1, "resources": 5}}, "distribution": "fixed",
                         "cases": 500, "seed": 2, "pools": {}, "priorities": CLASSES})
    calm = sim.simulate({"label": "", "arrivals_per_hour": 0.01, "paths": [(["Serve"], 1.0)],
                         "activities": {"Serve": {"minutes": 1, "resources": 5}}, "distribution": "fixed",
                         "cases": 500, "seed": 2, "pools": {}})
    assert idle["cycle_time_seconds"] == calm["cycle_time_seconds"]
    # A case's class depends only on the seed and its number.
    assert [priorities.draw(CLASSES, 5, c) for c in range(50)] == [priorities.draw(CLASSES, 5, c) for c in range(50)]


def _log():
    return workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who"})["log_id"]


def test_a_what_if_scenario_with_priorities_keeps_the_baseline_first_come_first_served():
    log = _log()
    result = sim.what_if(log, {"cases": 1500, "priorities": CLASSES})
    assert "priority_classes" not in result["baseline"] and len(result["result"]["priority_classes"]) == 2
    assert result["scenario"]["priorities"] == CLASSES
    assert priorities.ASSUMPTIONS[0] in result["assumptions"]
    assert not any("priorities or abandonment" in line for line in result["assumptions"])
    assert sim.what_if(log, {"cases": 500})["scenario"]["priorities"] is None
    spread = sim.what_if(log, {"cases": 300, "priorities": CLASSES, "uncertainty": {"replications": 3}})
    assert spread["uncertainty"]["replications"] == 3


@pytest.mark.parametrize("spec, message", [
    ([CLASSES[0]], "2 to 5 classes"),
    ([{"name": "a", "share": 0.5, "rank": 0}, {"name": "b", "share": 0.4, "rank": 1}], "add up to 1"),
    ([{"name": "a", "share": 0.5, "rank": 1}, {"name": "b", "share": 0.5, "rank": 1}], "two different ranks"),
    ([{"name": "a", "share": 0.5, "rank": 0}, {"name": "a", "share": 0.5, "rank": 1}], "distinct"),
    ([{"name": "a", "share": 0.5, "rank": 10}, {"name": "b", "share": 0.5, "rank": 1}], "0 \\(served first\\) to 9"),
    ([{"name": "a", "share": 0, "rank": 0}, {"name": "b", "share": 1, "rank": 1}], "above 0"),
    ([{"name": "a", "share": 0.5, "rank": True}, {"name": "b", "share": 0.5, "rank": 1}], "whole numbers"),
    ([{"name": "a", "share": 0.5}, {"name": "b", "share": 0.5, "rank": 1}], "a name, a share and a rank"),
    ("urgent", "2 to 5 classes"),
])
def test_invalid_priority_classes_are_refused(spec, message):
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        priorities.parse(spec)
