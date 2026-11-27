"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations

import json

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_handovers_are_pseudonymous_and_withhold_small_pairs():
    log_id = _log()
    with pytest.raises(sharing.NotShared):
        sharing.handovers(log_id)
    sharing.set_share(log_id, "aggregates")
    result = sharing.handovers(log_id)
    text = json.dumps(result)
    for name in ("clerk", "ana", "bob", "bot"):
        assert f'"{name}"' not in text
    assert result["privacy"]["names_returned"] is False
    assert all(row["cases"] >= 5 for row in result["handover_of_work"])
    assert all(row["cases"] >= 5 for row in result["resources"])
    # clerk -> ana/bob -> bot in each of 12 cases, split 6/6: every pair is shown.
    assert len(result["handover_of_work"]) == 4 and result["cases_with_handover"] == 12
    assert all(n is None or n >= 5 for row in result["activity_resource_matrix"]["events"] for n in row)
