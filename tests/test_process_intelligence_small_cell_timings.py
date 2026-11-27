"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_rework_and_activity_profile_withhold_single_case_timings():
    rows = ["case,activity,time,who"]
    for i in range(10):
        rows += [f"c{i},Receive,2024-03-{1 + i:02d}T08:00:00Z,clerk", f"c{i},Pay,2024-03-{1 + i:02d}T10:00:00Z,bot"]
    rows += ["c0,Fix,2024-03-01T09:00:00Z,clerk", "c0,Fix,2024-03-01T09:30:00Z,clerk"]
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="r", source_format="csv", mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    rework = sharing.rework(log_id)
    assert rework["comparison"]["cases_with_rework"] == 1
    assert "median_throughput_with_rework_seconds" not in rework["comparison"]
    assert rework["loop_patterns"] == []
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.activity_profile(log_id, "Fix")
    profile = sharing.activity_profile(log_id, "Pay")
    assert profile["case_hours"]["without_activity"] == {"median": None, "p90": None}
    assert all(r["median_hours"] is None for r in profile["comes_after"] if r["times"] < 5)


def test_agent_funnel_and_next_steps_withhold_rare_step_timings():
    rows = ["case,activity,time,who"]
    for i in range(10):
        rows += [f"c{i},Receive,2024-03-{1 + i:02d}T08:00:00Z,clerk", f"c{i},Pay,2024-03-{1 + i:02d}T10:00:00Z,bot"]
    rows += ["c0,Fix,2024-03-01T09:00:00Z,clerk"]
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="f", source_format="csv", mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    funnel = sharing.funnel(log_id, ["Receive", "Fix"])
    assert funnel["steps"][1]["cases"] == 1
    assert funnel["steps"][1]["hours_from_previous"]["median"] is None
    assert funnel["completed"]["hours_first_to_last"]["median"] is None
    steps = {s["activity"]: s for s in sharing.next_steps(log_id, ["Receive"])["next"]}
    assert steps["Fix"]["hours_from_previous"]["median"] is None
    assert steps["Pay"]["hours_from_previous"]["median"] is not None

    filtered = sharing.next_steps(log_id, ["Receive"],
                                  case_filter={"case_ids": [f"c{i}" for i in range(9)]})
    assert filtered["cases"] == 9
    filtered_steps = {s["activity"]: s for s in filtered["next"]}
    assert filtered_steps["Fix"]["cases"] == 1
    assert filtered_steps["Fix"]["hours_from_previous"]["median"] is None
    assert filtered_steps["Pay"]["hours_from_previous"]["median"] is not None
