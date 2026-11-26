"""Agents can see when one group of cases is worked on, with the same minimum-support rule."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_time_patterns_of_one_group_keep_the_support_rule():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    group = sharing.time_patterns(log_id, case_filter={"resource": "bob"})
    assert group["total_case_arrivals"] == 6 and group["time_bins_withheld"] is True
    # Approve by bob happens in 6 cases: enough. Narrowed further it falls under the minimum and is refused.
    sharing.time_patterns(log_id, activity="Approve", case_filter={"resource": "bob"})
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.time_patterns(log_id, activity="Approve", case_filter={"case_ids": ["c0", "c2"]})
