"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations


import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_variants_report_how_many_variants_cover_most_cases():
    log_id = _log()
    sharing.set_share(log_id, "aggregates")
    result = sharing.variants(log_id)
    targets = {t["target_percent"]: t["variants_needed"] for t in result["coverage_targets"]}
    assert set(targets) == {50, 80, 90, 95}
    assert 1 <= targets[50] <= targets[95] <= result["distinct_variants"]
