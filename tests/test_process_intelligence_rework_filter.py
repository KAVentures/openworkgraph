"""Agents can narrow rework to a case filter, for example to analyse logs above the rework case limit."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_rework as rework_module
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_rework_case_filter_narrows_and_unlocks_large_logs(monkeypatch):
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    assert sharing.rework(log_id)["cases"] == 12
    assert sharing.rework(log_id, case_filter={"resource": "bob"})["cases"] == 6
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.rework(log_id, case_filter={"case_ids": ["c0"]})
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.rework(log_id, case_filter={})
    monkeypatch.setattr(rework_module, "MAX_CASES", 8)
    with pytest.raises(workspace.EventLogWorkspaceError, match="use a cohort"):
        sharing.rework(log_id)
    assert sharing.rework(log_id, case_filter={"resource": "ana"})["cases"] == 6
