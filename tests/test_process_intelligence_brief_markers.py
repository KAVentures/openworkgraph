"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations


import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_brief_lists_owner_change_markers_without_notes():
    import json
    from server import event_log_change_markers as markers
    log_id = _log()
    markers.add(log_id, {"marked_at": "2024-03-05T00:00:00+00:00", "label": "New form", "note": "private detail"})
    sharing.set_share(log_id, "aggregates")
    result = sharing.brief(log_id)
    assert [m["label"] for m in result["owner_change_markers"]] == ["New form"]
    assert "private detail" not in json.dumps(result)
