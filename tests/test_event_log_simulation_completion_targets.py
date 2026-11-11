"""User-defined priority-class completion targets in R14 what-if simulations.

The missing-case accounting is deliberately based on all post-warm-up
arrivals. It never treats abandonment or an unfilled batch as an on-time
completion or silently removes them from the denominator.
"""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_simulation as sim
from server import event_log_simulation_priorities as priorities
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv


TARGETS = [
    {"name": "urgent", "share": 0.4, "rank": 0, "completion_target_minutes": 2},
    {"name": "standard", "share": 0.6, "rank": 1, "completion_target_minutes": 5},
]


def test_exact_all_arrivals_bounds_include_late_abandoned_and_unresolved():
    classes = priorities.parse(TARGETS)
    assigned = {0: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 1}
    started = dict.fromkeys(range(6), 0.0)
    finished = {0: 30.0, 1: 121.0, 3: 300.0, 4: 301.0}
    rows = priorities.completion_targets(classes, assigned, started, finished, {2: "Serve"}, 0)
    urgent, standard = rows
    assert urgent["post_warmup_arrivals"] == 3
    assert (urgent["completed_within_target"], urgent["completed_after_target"],
            urgent["abandoned"], urgent["unresolved"]) == (1, 1, 1, 0)
    assert urgent["attainment_lower_bound"] == urgent["attainment_upper_bound"] == 0.333333
    assert urgent["known_target_misses"] == 2
    assert (standard["completed_within_target"], standard["completed_after_target"],
            standard["abandoned"], standard["unresolved"]) == (1, 1, 0, 1)
    assert standard["attainment_lower_bound"] == 0.333333
    assert standard["attainment_upper_bound"] == 0.666667
    assert priorities.completion_targets(classes, assigned, started, finished, {2: "Serve"}, 1)[0]["post_warmup_arrivals"] == 2


def test_targets_are_optional_and_do_not_change_existing_summary_or_simulation():
    plain = [{"name": "urgent", "share": 0.4, "rank": 0},
             {"name": "standard", "share": 0.6, "rank": 1}]
    base = {"label": "", "arrivals_per_hour": 1, "paths": [(["Serve"], 1.0)],
            "activities": {"Serve": {"minutes": 3, "resources": 1}},
            "distribution": "fixed", "cases": 300, "seed": 7, "pools": {}}
    original = sim.simulate({**base, "priorities": priorities.parse(plain)})
    targets = sim.simulate({**base, "priorities": priorities.parse(TARGETS)})
    assert "priority_completion_targets" not in original
    unchanged = {key: value for key, value in targets.items() if key not in {
        "priority_completion_targets", "completion_target_basis",
        "completion_target_rates_are_bounds_not_certified_slas"}}
    # The optional user-entered target is echoed in the targeted class's
    # description; no legacy measurement or queue result may change.
    unchanged["priority_classes"] = [
        {key: value for key, value in item.items() if key != "completion_target_minutes"}
        for item in unchanged["priority_classes"]
    ]
    assert unchanged == original
    assert sum(row["post_warmup_arrivals"] for row in targets["priority_completion_targets"]) == 270
    assert targets["completion_target_rates_are_bounds_not_certified_slas"] is True


def test_optional_target_per_class_only_reports_declared_class():
    classes = priorities.parse([TARGETS[0], {"name": "standard", "share": 0.6, "rank": 1}])
    rows = priorities.completion_targets(classes, {0: 0, 1: 1}, {0: 0, 1: 0}, {0: 60, 1: 20}, {}, 0)
    assert [row["name"] for row in rows] == ["urgent"]


@pytest.mark.parametrize("value", [None, True, False, "5", 0, -1, 525601, float("nan"), float("inf"), float("-inf")])
def test_invalid_completion_targets_rejected(value):
    bad = [{**TARGETS[0], "completion_target_minutes": value}, TARGETS[1]]
    with pytest.raises(workspace.EventLogWorkspaceError, match="completion_target_minutes"):
        priorities.parse(bad)


def test_what_if_outputs_targets_without_changing_unprioritized_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    log = workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv",
                               mapping={"case_column": "case", "activity_column": "activity",
                                        "timestamp_column": "time", "resource_column": "who"})["log_id"]
    result = sim.what_if(log, {"cases": 100, "priorities": TARGETS, "seed": 3})
    assert "priority_completion_targets" not in result["baseline"]
    assert len(result["result"]["priority_completion_targets"]) == 2
    assert result["scenario"]["priorities"] == priorities.parse(TARGETS)
    assert any("Priority completion targets are user-entered" in line for line in result["assumptions"])
    assert result["forecast_or_guarantee"] is False
    assert result["execution_authorized"] is False
