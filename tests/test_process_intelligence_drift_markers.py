"""Split out of test_process_intelligence_sharing.py so parallel branches do not collide at its end."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from tests.test_process_intelligence_sharing import MAPPING, _dirs, _log  # noqa: F401  (autouse fixture)

def test_agent_drift_relates_owner_change_markers_to_change_points():
    import json
    from server import event_log_change_markers as markers
    rows = ["case,activity,time"]
    for month, path in ((1, ("A", "B", "C")), (2, ("A", "B", "C")), (3, ("A", "X", "Y", "C")), (4, ("A", "X", "Y", "C"))):
        for i in range(40):
            rows += [f"m{month}c{i},{act},2024-{month:02d}-{1 + i % 25:02d}T{8 + n:02d}:00:00Z" for n, act in enumerate(path)]
    log_id = workspace.import_log(BytesIO("\n".join(rows).encode()), name="d", source_format="csv",
                                mapping={k: v for k, v in MAPPING.items() if k != "resource_column"})["log_id"]
    markers.add(log_id, {"marked_at": "2024-03-01T00:00:00+00:00", "label": "New routing", "note": "secret note"})
    markers.add(log_id, {"marked_at": "2024-01-02T00:00:00+00:00", "label": "Unrelated"})
    sharing.set_share(log_id, "aggregates")
    result = sharing.drift(log_id)
    assert "secret note" not in json.dumps(result)
    by_label = {m["label"]: m for m in result["owner_change_markers"]}
    assert result["change_points"], result
    assert by_label["New routing"]["near_change_points"]
    assert by_label["Unrelated"]["near_change_points"] == []
