"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations


import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_compares_two_case_groups_and_withholds_small_rows():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    result = sharing.compare_cohorts(log_id, {"resource": "bob"}, {"resource": "ana"})
    assert [c["cases"] for c in result["cohorts"]] == [6, 6]
    assert result["differences_second_minus_first"]
    for row in result["activity_presence"] + result["variants"]:
        assert not any(0 < row[k] < 5 for k in ("cases_in_first", "cases_in_second"))
    assert all("variant_id" not in row for row in result["variants"])
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.compare_cohorts(log_id, {"case_ids": ["c0", "c2"]}, {"resource": "ana"})
