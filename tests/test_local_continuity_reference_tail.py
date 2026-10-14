"""Continuity resource fallback must never widen saved-history disclosure."""
from __future__ import annotations

import json
import sqlite3

from server.recent_resource_references import reference_tail

REF_A = "owg:r:" + "a" * 32
REF_B = "owg:r:" + "b" * 32


def _database():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE events(
      id INTEGER PRIMARY KEY, event_id TEXT, observed_at TEXT,
      source TEXT, metadata_json TEXT
    );
    """)
    rows = [
        (1, "old", "2026-10-12T10:00:00Z", "browser_extension",
         {"resource_reference": {"provider": "github", "resource_kind": "pull_request",
                                 "resource_ref": REF_A, "host": "github.com",
                                 "resolver_locator": "https://secret.example/key?token=bad"},
          "message_body": "PRIVATE_MESSAGE"}),
        (2, "new", "2026-10-14T10:00:00Z", "browser_extension",
         {"resource_reference": {"provider": "gmail", "resource_kind": "message",
                                 "resource_ref": REF_B, "host": "mail.google.com"}}),
        (3, "non-pointer", "2026-10-14T10:01:00Z", "desktop",
         {"message_body": "PRIVATE_MESSAGE"}),
    ]
    db.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", [
        (i, eid, at, source, json.dumps(meta)) for i, eid, at, source, meta in rows
    ])
    return db


def test_off_grant_sees_only_current_session_and_never_returns_raw_text():
    db = _database()
    result = reference_tail(db, lease={"mode": "off"},
                            run_started_at="2026-10-14T00:00:00Z")
    assert [row["event_id"] for row in result["recent_evidence"]] == ["new"]
    assert "PRIVATE" not in str(result)
    assert "secret.example" not in str(result)
    assert result["history_access_limited"] is True


def test_off_without_current_run_fails_closed():
    db = _database()
    result = reference_tail(db, lease={"mode": "off"}, run_started_at=None)
    assert result["recent_evidence"] == []


def test_selected_window_exposes_only_its_dates_plus_current_run():
    db = _database()
    result = reference_tail(
        db, lease={"mode": "selected_range", "since": "2026-10-12T00:00:00Z",
                   "until": "2026-10-13T00:00:00Z"},
        run_started_at="2026-10-14T00:00:00Z",
    )
    assert {row["event_id"] for row in result["recent_evidence"]} == {"old", "new"}
    assert "PRIVATE_MESSAGE" not in str(result)
    assert "token=bad" not in str(result)
    assert result["recent_evidence"][0]["metadata"]["resource_reference"]["resource_ref"] == REF_B


def test_all_saved_does_not_retain_arbitrary_metadata():
    db = _database()
    result = reference_tail(db, lease={"mode": "all_saved"}, run_started_at=None)
    assert len(result["recent_evidence"]) == 2
    assert "resolver_locator" not in str(result)
    assert "message_body" not in str(result)
    assert "host" in str(result)
