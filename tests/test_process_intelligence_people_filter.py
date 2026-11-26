"""Agents can look at handovers and roles within one group of cases."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_handovers_and_roles_within_one_group():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    whole = sharing.handovers(log_id)
    group = sharing.handovers(log_id, case_filter={"resource": "bob"})
    assert group["cases_with_handover"] == 6 and whole["cases_with_handover"] == 12
    assert sum(r["member_count"] for r in sharing.roles(log_id, case_filter={"resource": "bob"})["roles"]) >= 2
    for call in (sharing.handovers, sharing.roles):
        with pytest.raises(workspace.EventLogWorkspaceError):
            call(log_id, case_filter={"case_ids": ["c0"]})
