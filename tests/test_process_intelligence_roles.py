"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations

import json

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_agent_roles_count_members_without_listing_them():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    result = sharing.roles(log_id)
    text = json.dumps(result)
    assert "members" not in text.replace("members_listed", "")
    for name in ("clerk", "ana", "bob", "bot"):
        assert f'"{name}"' not in text
    assert sum(role["member_count"] for role in result["roles"]) >= 3
    assert all(a["events"] >= 5 for role in result["roles"] for a in role["activity_mix"])
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.roles(log_id, threshold=1.5)
