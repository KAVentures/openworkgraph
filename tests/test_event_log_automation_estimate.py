"""Automation benefit ranges come only from stated assumptions and are labelled measured or assumed."""
from __future__ import annotations

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_automation_estimate as est
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)


def test_ranges_follow_the_stated_assumptions():
    log_id = _log()
    result = est.estimate(log_id, handling_minutes=[5, 10], hourly_cost=60)
    assert result["candidates"]
    for row in result["candidates"]:
        lo, hi = est.DEFAULT_AUTOMATABLE_SHARE[row["routing_pattern"]]
        per_week = row["measured"]["occurrences_per_week"]
        assert row["hours_per_week"] == [round(per_week * 5 / 60 * lo, 2), round(per_week * 10 / 60 * hi, 2)]
        assert row["cost_per_year"] == [round(h * 52 * 60, 0) for h in row["hours_per_week"]]
        assert row["assumed"]["handling_minutes"] == [5.0, 10.0]
    custom = est.estimate(log_id, handling_minutes=[5, 10], automatable_share={"fixed": [1, 1], "mostly_fixed": [1, 1],
                                                                               "variable": [1, 1]})
    assert all(r["hours_per_week"][0] == round(r["measured"]["occurrences_per_week"] * 5 / 60, 2)
               for r in custom["candidates"])
    assert "cost_per_year_total" not in custom


@pytest.mark.parametrize("bad", [{"handling_minutes": [10, 5]}, {"handling_minutes": [1]},
                                 {"handling_minutes": [1, 2], "automatable_share": {"fixed": [0.5, 2]}},
                                 {"handling_minutes": [1, 2], "automatable_share": {"other": [0, 1]}},
                                 {"handling_minutes": [1, 2], "hourly_cost": -1}])
def test_bad_assumptions_are_refused(bad):
    log_id = _log()
    with pytest.raises(workspace.EventLogWorkspaceError):
        est.estimate(log_id, **bad)


def test_agents_get_the_estimate_at_aggregates_level():
    log_id = _log()
    with pytest.raises(sharing.NotShared):
        sharing.automation_estimate(log_id, [5, 10])
    sharing.set_share(log_id, "aggregates")
    result = sharing.automation_estimate(log_id, [5, 10])
    assert result["hours_per_week_total"][0] <= result["hours_per_week_total"][1] and "log" not in result
