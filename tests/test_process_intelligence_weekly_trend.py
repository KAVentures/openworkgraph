"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations


import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_overview_weekly_trend_withholds_small_weeks():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    weeks = sharing.overview(log_id)["completed_per_week"]
    assert sum(w["cases"] for w in weeks) == 12
    for week in weeks:
        assert (week["median_throughput_seconds"] is None) == (week["cases"] < 5)
