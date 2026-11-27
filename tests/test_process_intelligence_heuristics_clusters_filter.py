"""Agents can mine the heuristics net and trace clusters of one group of cases."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_heuristics_net_and_clusters_within_one_group():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    assert sharing.heuristics_net(log_id, case_filter={"resource": "bob"})["cases"] == 6
    clusters = sharing.trace_clusters(log_id, case_filter={"resource": "bob"})
    assert sum(c["cases"] for c in clusters["clusters"]) + clusters["withheld"]["cases"] <= 6
    for call in (sharing.heuristics_net, sharing.trace_clusters):
        with pytest.raises(workspace.EventLogWorkspaceError):
            call(log_id, case_filter={"case_ids": ["c0"]})
