"""Agent model answers fit the log's activities into what a discovered model can hold."""
from __future__ import annotations

from io import BytesIO

from server import event_log_agent_sharing as sharing
from server import event_log_model_discovery as discovery
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_small_logs_keep_every_activity():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    assert sharing._fitting_activities(log_id, None) == {"share": 1.0, "activities_left_out": 0}
    assert sharing.footprints(log_id)["model_activities"]["activities_left_out"] == 0


def test_logs_with_too_many_activities_get_a_fitting_share():
    limit = discovery.MAX_MODEL_NODES - 2 - sharing.AGENT_MODEL_NODE_HEADROOM
    names = [f"Step {i:02d}" for i in range(limit + 6)]
    rows = ["case,activity,time,who"]
    for c in range(20):
        steps = names[:limit] + ([names[limit + c % 6]] if c < 6 else [])
        rows += [f"c{c},{a},2024-03-01T{8 + i // 60:02d}:{i % 60:02d}:00Z,x" for i, a in enumerate(steps)]
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="wide", source_format="csv",
                                  mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    fit = sharing._fitting_activities(log_id, None)
    assert fit["activities_left_out"] == 6 and 0 < fit["share"] < 1
    result = sharing.footprints(log_id)
    assert result["model_activities"] == fit


def test_model_change_reports_the_fitted_share():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    with __import__("pytest").raises(workspace.EventLogWorkspaceError):
        sharing.model_change(log_id)  # 12 cases cannot fill two periods of 10; the share is still computed first
    assert sharing._fitting_activities(log_id, None)["share"] == 1.0
