"""Agents can profile step-to-step timings within one group of cases."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_temporal_profile_of_one_group():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    group = sharing.temporal_profile(log_id, case_filter={"resource": "bob"})
    assert group["cases"] == 6
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.temporal_profile(log_id, case_filter={"case_ids": ["c0"]})
