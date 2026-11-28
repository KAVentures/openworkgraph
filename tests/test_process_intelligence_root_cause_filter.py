"""Agents can look for what goes with an outcome within one group of cases."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_root_cause_within_one_group():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    whole = sharing.root_cause(log_id, "duration_over", hours=24)
    group = sharing.root_cause(log_id, "duration_over", hours=24, case_filter={"resource": "bob"})
    assert whole["cases"] == 12 and group["cases"] == 6
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.root_cause(log_id, "duration_over", hours=24, case_filter={"case_ids": ["c0"]})
