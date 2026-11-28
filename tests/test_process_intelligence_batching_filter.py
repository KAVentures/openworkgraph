"""Agents can look at batch processing within one group of cases."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_batching_within_one_group():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    group = sharing.batching(log_id, case_filter={"resource": "bob"})
    pay = {a["activity"]: a for a in group["activities"]}["Pay"]
    assert pay["events"] == 6
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.batching(log_id, case_filter={"case_ids": ["c0"]})
