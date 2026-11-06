"""Identity interruption bursts within global sessions."""
import json
import sqlite3
from server.work_resumption import resumption_candidates

def _db(rows):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, event_id TEXT, observed_at TEXT, metadata_json TEXT)")
    for i, (at, ref) in enumerate(rows, 1):
        metadata = {"resource_reference": {"resource_ref": "owg:r:" + ref * 32,
                    "provider": "github", "resource_kind": "issue"}} if ref else {"clicks": 1}
        db.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (i, f"e{i}", at, json.dumps(metadata)))
    return db

def test_interleaved_identity_interruptions_do_not_cross_contaminate():
    db = _db([
        ("2026-10-12T09:00:00Z", "a"),
        ("2026-10-12T09:01:00Z", "a"),
        ("2026-10-12T09:04:00Z", "b"),
        ("2026-10-12T09:08:00Z", "b"),
        ("2026-10-12T09:12:00Z", "a"),
        ("2026-10-12T09:13:00Z", "a"),
        ("2026-10-14T10:00:00Z", "a"),
    ])
    result = resumption_candidates(db, lease={"mode": "all_saved"})
    assert result["coverage"]["sessions_considered"] == 2
    candidate = result["candidates"][0]
    track = candidate["pointer_tracks"][0]["earlier"]
    assert [s["evidence_event_ids"] for s in track["segments"]] == [["e1", "e2"], ["e5", "e6"]]
    assert track["segments"][1]["gap_from_previous_seconds"] == 660
    assert track["segments_are_observation_bursts_not_tasks"] is True
    assert candidate["proves_same_work"] is False
    assert result["identity_timelines"][0]["episodes"][0]["segments"] == track["segments"]

def test_uninterrupted_resource_is_one_burst_and_off_access_has_no_timeline():
    db = _db([
        ("2026-10-12T09:00:00Z", "a"),
        ("2026-10-12T09:02:00Z", "a"),
        ("2026-10-12T09:04:00Z", "a"),
        ("2026-10-14T10:00:00Z", "a"),
    ])
    result = resumption_candidates(db, lease={"mode": "all_saved"})
    assert len(result["candidates"][0]["pointer_tracks"][0]["earlier"]["segments"]) == 1
    assert resumption_candidates(db, lease={"mode": "off"})["identity_timelines"] == []
