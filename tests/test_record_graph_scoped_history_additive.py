"""Record-graph retrieval must not lose old cases to unrelated newer work.

This only changes opt-in, read-only record lookup. Workflow mapping, capture,
procedural memory, discovery, and exports retain their existing read surfaces.
"""
from __future__ import annotations

import json

import pytest

from server import db
from server import record_graph as graph

REF = "owg:r:" + "a" * 32
OTHER = "owg:r:" + "b" * 32


def _reference(ref=REF, *, provider="github", kind="pull_request", host="github.com"):
    return {"resource_reference": {
        "resource_ref": ref, "provider": provider, "resource_kind": kind, "host": host,
    }}


def _insert(conn, event_id, when, meta):
    conn.execute(
        "INSERT INTO events (event_id, observed_at, session_id, actor_id, event_type, "
        "source, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, when, "session", "actor", "browser_click", "browser_extension",
         json.dumps(meta) if isinstance(meta, dict) else meta),
    )


@pytest.fixture()
def evidence_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "only-test-record-events.db")
    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE events (event_id TEXT, observed_at TEXT, session_id TEXT, "
            "actor_id TEXT, event_type TEXT, source TEXT, metadata_json TEXT)"
        )
    return db


def test_older_record_not_hidden_by_thirty_thousand_newer_unrelated_events(evidence_store):
    calls = []
    with evidence_store.connect() as conn:
        _insert(conn, "old-record", "2026-01-10T10:00:00Z", _reference())
        conn.executemany(
            "INSERT INTO events (event_id, observed_at, session_id, actor_id, "
            "event_type, source, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (f"unrelated-{i}", "2026-10-19T12:00:00Z", "different-session",
                 "actor", "focus_span", "desktop", json.dumps(_reference(OTHER)))
                for i in range(30_010)
            ),
        )

    result = graph.record_graph(
        REF, include_source_status=True,
        source_reader=lambda ref: calls.append(ref) or {"availability": "source_reported"},
    )
    assert result["record_observed"] is True
    assert result["observed_event_count"] == 1
    assert result["timeline"][0]["event_id"] == "old-record"
    assert result["source_evidence"]["availability"] == "source_reported"
    assert result["source_rows_scoped_to_exact_record"] is True
    assert result["record_identity_complete_within_retained_window"] is True
    assert result["source_event_limit_reached"] is False
    assert calls == [REF]

    # The consented date window is still authoritative; a missing result
    # inside that window does not authorize reading the older business record.
    restricted = graph.record_graph(
        REF, since="2026-10-01T00:00:00Z", include_source_status=True,
        source_reader=lambda ref: calls.append(ref),
    )
    assert restricted["record_observed"] is False
    assert restricted["source_evidence"]["availability"] == "unknown"
    assert calls == [REF]


def test_match_requires_exact_json_path_not_a_text_mention(evidence_store):
    with evidence_store.connect() as conn:
        _insert(conn, "comment", "2026-10-10T12:00:00Z",
                {"untrusted_text": "The string " + REF + " appeared in a page",
                 **_reference(OTHER)})
        _insert(conn, "malformed", "2026-10-10T12:00:01Z", "{broken JSON " + REF)
    calls = []
    result = graph.record_graph(
        REF, include_source_status=True,
        source_reader=lambda ref: calls.append(ref),
    )
    assert result["record_observed"] is False
    assert result["source_evidence"]["availability"] == "unknown"
    assert calls == []


def test_truncated_record_evidence_must_not_authorize_source_read(evidence_store):
    with evidence_store.connect() as conn:
        _insert(conn, "recent", "2026-10-10T12:00:03Z", _reference())
        _insert(conn, "middle", "2026-10-10T12:00:02Z", _reference())
        _insert(conn, "conflicting-old", "2026-10-10T12:00:01Z",
                _reference(provider="stripe", kind="invoice", host="dashboard.stripe.com"))
    calls = []
    result = graph.record_graph(
        REF, source_event_limit=2, include_source_status=True,
        source_reader=lambda ref: calls.append(ref),
    )
    assert result["source_event_limit_reached"] is True
    assert result["record_identity_complete_within_retained_window"] is False
    assert result["source_evidence"]["reason"] == "record_evidence_window_truncated"
    assert calls == []

    # With sufficient allowed record rows, conflicting provider evidence is
    # discovered rather than dropped by unrelated global recency limits.
    full = graph.record_graph(REF, source_event_limit=3, include_source_status=True,
                              source_reader=lambda ref: calls.append(ref))
    assert full["source_event_limit_reached"] is False
    assert full["record_observed"] is False
    assert full["record_identity_complete_within_retained_window"] is False
    assert calls == []


def test_exactly_at_limit_is_not_falsely_reported_truncated(evidence_store):
    with evidence_store.connect() as conn:
        _insert(conn, "first", "2026-10-10T12:00:01Z", _reference())
        _insert(conn, "second", "2026-10-10T12:00:02Z", _reference())
    result = graph.record_graph(REF, source_event_limit=2, include_source_status=True,
                                source_reader=lambda _: {"availability": "source_reported"})
    assert result["observed_event_count"] == 2
    assert result["source_event_limit_reached"] is False
    assert result["source_evidence"]["availability"] == "source_reported"


@pytest.mark.parametrize("since,until", [
    ("not-a-date", None),
    (None, "not-a-date"),
    ("2026-10-10T12:00:00Z", "2026-10-10T12:00:00Z"),
])
def test_history_window_validation_preserved(evidence_store, since, until):
    with pytest.raises(ValueError):
        graph.record_graph(REF, since=since, until=until)
