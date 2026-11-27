"""Agents can see the performance spectrum of one group, with segment support counted within the group."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_spectrum_of_one_group_counts_support_within_it():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    whole = {(s["from"], s["to"]) for s in sharing.performance_spectrum(log_id)["segments"]}
    group = sharing.performance_spectrum(log_id, case_filter={"resource": "bob"})
    shown = {(s["from"], s["to"]) for s in group["segments"]}
    assert ("Receive", "Approve") in shown and shown <= whole
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.performance_spectrum(log_id, case_filter={"case_ids": ["c0"]})
