"""Agents can list the owner's saved filter sets, without person names or tiny counts."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_filter_sets as filter_sets
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_saved_filter_sets_are_listed_for_agents_safely():
    log_id = _log()
    filter_sets.save(log_id, {"name": "Slow approvals", "cohort": {"duration": {"min": 86400}}})
    filter_sets.save(log_id, {"name": "Bob's cases", "cohort": {"resource": "bob"}})
    filter_sets.save(log_id, {"name": "One case", "cohort": {"started_from": "2024-03-12T00:00:00Z"}})
    with pytest.raises(sharing.NotShared):
        sharing.filter_sets(log_id)
    sharing.set_share(log_id, "aggregates")
    result = sharing.filter_sets(log_id)
    by_name = {s["name"]: s for s in result["filter_sets"]}
    assert "Bob's cases" not in by_name and result["withheld_naming_a_resource"] == 1
    assert by_name["Slow approvals"]["usable"] and by_name["Slow approvals"]["matching_cases"] == 6
    assert not by_name["One case"]["usable"] and by_name["One case"]["matching_cases"] is None
    assert "bob" not in str(result)
    group = sharing.overview_for_cases(log_id, by_name["Slow approvals"]["case_filter"])
    assert group["cases"] == 6
