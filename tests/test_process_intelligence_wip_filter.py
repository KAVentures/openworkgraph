"""Agents can narrow work in progress to a case filter, e.g. when stray dates stretch a log past 400 buckets."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_stray_dates_are_narrowed_with_a_case_filter():
    rows = ["case,activity,time,who", "old,A,1948-01-05T08:00:00Z,x", "old,B,1948-01-06T08:00:00Z,x"]
    for i in range(10):
        rows += [f"c{i},A,2024-03-{1 + i:02d}T08:00:00Z,x", f"c{i},B,2024-03-{2 + i:02d}T08:00:00Z,x"]
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="stray", source_format="csv",
                                  mapping=MAPPING)["log_id"]
    sharing.set_share(log_id, "aggregates")
    with pytest.raises(workspace.EventLogWorkspaceError, match="start-date range"):
        sharing.work_in_progress(log_id)
    result = sharing.work_in_progress(log_id, case_filter={"started_from": "2024-01-01T00:00:00Z"})
    assert 1 <= len(result["buckets"]) <= 3
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.work_in_progress(log_id, case_filter={"started_before": "2000-01-01T00:00:00Z"})
