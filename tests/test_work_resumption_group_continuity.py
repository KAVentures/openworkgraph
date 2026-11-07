"""Cross-session observed group continuity is evidence-only and grant scoped."""
import json
import sqlite3

from server.work_resumption import resumption_candidates

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32
F = "owg:f:" + "f" * 32


def _db(rows):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE events(event_id TEXT, observed_at TEXT, metadata_json TEXT)")
    for i, (when, meta) in enumerate(rows):
        db.execute("INSERT INTO events VALUES (?, ?, ?)", (str(i), when, json.dumps(meta)))
    return db


def _event(ref, with_file=False):
    meta = {"resource_reference": {"resource_ref": ref, "provider": "github", "resource_kind": "issue"}}
    if with_file:
        meta["file_reference"] = {"file_ref": F}
    return meta


def test_correlated_pair_recurs_without_absorbing_interleaved_b():
    rows = [
        ("2026-10-12T09:00:00Z", _event(A, True)),
        ("2026-10-12T09:02:00Z", _event(B)),
        ("2026-10-14T10:00:00Z", _event(B)),
        ("2026-10-14T10:02:00Z", _event(A, True)),
    ]
    result = resumption_candidates(_db(rows), lease={"mode": "all_saved"})
    [match] = result["observed_group_continuity"]
    assert match["corroborated_pair_count"] == 1
    assert {p["resource_ref"] for p in match["matched_resource_pointers"]} == {A, F}
    assert match["requires_review"] and not match["proves_same_case"]
    assert B not in {p["resource_ref"] for p in match["matched_resource_pointers"]}


def test_independent_recurrence_never_creates_group_continuity():
    rows = [
        ("2026-10-12T09:00:00Z", _event(A)),
        ("2026-10-12T09:02:00Z", _event(B)),
        ("2026-10-14T10:00:00Z", _event(A)),
        ("2026-10-14T10:02:00Z", _event(B)),
    ]
    result = resumption_candidates(_db(rows), lease={"mode": "all_saved"})
    assert result["observed_group_continuity"] == []


def test_history_off_prevents_cross_session_group_matching():
    rows = [
        ("2026-10-12T09:00:00Z", _event(A, True)),
        ("2026-10-14T10:00:00Z", _event(A, True)),
    ]
    result = resumption_candidates(_db(rows), lease={"mode": "off"},
                                   run_started_at="2026-10-14T00:00:00Z")
    assert result["observed_group_continuity"] == []
    assert result["history_access_limited"]
