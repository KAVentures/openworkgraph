"""Regression: duplicate external event labels cannot link unrelated cases."""
import json
import sqlite3

from server.work_resumption import resumption_candidates


def test_colliding_event_labels_do_not_contribute_identity_or_candidate_evidence():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE events (event_id TEXT, observed_at TEXT, metadata_json TEXT)")

    def insert(event_id, when, ref):
        metadata = {"resource_reference": {
            "resource_ref": "owg:r:" + ref * 32,
            "provider": "github", "resource_kind": "issue",
        }}
        db.execute("INSERT INTO events VALUES (?, ?, ?)",
                   (event_id, when, json.dumps(metadata)))

    # Two distinct observed rows reuse an external label. Their resource
    # identities must not imply continuity, even though both sessions contain A.
    insert("duplicate", "2026-11-01T10:00:00Z", "a")
    insert("duplicate", "2026-11-01T12:00:00Z", "a")
    insert("unique-current", "2026-11-01T12:01:00Z", "b")
    result = resumption_candidates(db, lease={"mode": "all_saved"}, gap_minutes=30)

    assert result["coverage"]["ambiguous_event_id_collisions"] == 1
    assert result["coverage"]["excluded_colliding_pointer_rows"] == 2
    assert result["coverage"]["scan_truncated"] is True
    assert result["candidates"] == []
    assert result["current_session"]["pointer_count"] == 1
    assert result["current_session"]["resource_refs"] == ["owg:r:" + "b" * 32]
    assert result["observed_pair_event_assignments"] == []
    assert result["identity_timelines"] == []
