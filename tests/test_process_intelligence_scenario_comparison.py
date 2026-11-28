"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations


import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_compares_named_scenarios_against_one_baseline():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    result = sharing.compare_scenarios(log_id, {"cases": 200, "arrivals_per_hour": 1},
                                       [{"label": "More approvers", "activities": {"Approve": {"resources": 3}}},
                                        {"label": "Faster pay", "activities": {"Pay": {"minutes": 5}}}])
    assert [a["label"] for a in result["alternatives"]] == ["More approvers", "Faster pay"]
    assert result["calibrated_paths_returned"] is False and "log" not in result
    assert "paths" not in str(result["baseline"])
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.compare_scenarios(log_id, {"cases": 50_000}, [{"label": "a"}, {"label": "b"}])
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.compare_scenarios(log_id, None, [{"label": "only one"}])
