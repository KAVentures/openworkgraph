"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations


import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_previews_a_draft_signal_without_saving_or_case_ids():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    rule = {"name": "Slow cases", "when": {"kind": "cases", "condition": "duration_over", "seconds": 86400}}
    result = sharing.preview_signal(log_id, rule)
    assert result["saved"] is False and result["signal"]["matching_cases"] == 6
    assert "evidence_cases" not in result["signal"]
    assert sharing.signals(log_id)["signals"] == []
    sharing.set_share(log_id, "cases")
    assert sharing.preview_signal(log_id, rule)["signal"]["evidence_cases"]
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.preview_signal(log_id, {**rule, "cohort": {"case_ids": ["c0"]}})
