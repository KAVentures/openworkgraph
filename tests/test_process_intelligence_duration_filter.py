"""Agents can see how durations are spread within one group of cases."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_duration_bands_of_one_group():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    slow = sharing.duration_distribution(log_id, case_filter={"resource": "bob"})
    fast = sharing.duration_distribution(log_id, case_filter={"resource": "ana"})
    assert slow["cases"] == fast["cases"] == 6
    assert slow["observed_duration_seconds"]["median"] > fast["observed_duration_seconds"]["median"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="fewer than 5 cases"):
        sharing.duration_distribution(log_id, case_filter={"started_before": "2024-03-02T00:00:00Z"})
