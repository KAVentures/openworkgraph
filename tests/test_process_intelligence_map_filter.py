"""Agents can draw the process map of one group of cases."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_map_of_one_group_counts_only_its_cases():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    whole = sharing.process_map(log_id)
    group = sharing.process_map(log_id, case_filter={"resource": "bob"})
    count = {a["activity"]: a["case_count"] for a in group["activities"]}
    assert count["Approve"] == 6 and {a["activity"]: a["case_count"] for a in whole["activities"]}["Approve"] == 12
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.process_map(log_id, case_filter={"case_ids": ["c0"]})
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.process_map(log_id, case_filter={})
