from __future__ import annotations

import importlib
import json


def test_redaction_masks_identifiers_without_losing_workflow_words(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation
    importlib.reload(presentation)

    payload = {
        "surface": "Gmail",
        "window_title": "Contract renewal - Anna Svensson <anna.svensson@acme.com> - Gmail",
        "label": "Reply to Anna Svensson",
        "note": "Call +46 70 123 45 67 after review",
        "event_id": "evt-123",
        "duration_seconds": 12.5,
    }
    safe = presentation.redact_for_display(payload)
    blob = json.dumps(safe, ensure_ascii=False)

    assert "anna.svensson@acme.com" not in blob.lower()
    assert "Anna Svensson" not in blob
    assert "+46 70 123 45 67" not in blob
    assert "EMAIL_" in blob
    assert "PERSON_" in blob
    assert "PHONE_" in blob
    assert "Contract renewal" in safe["window_title"]
    assert "Gmail" in safe["window_title"]
    assert safe["label"].startswith("Reply to PERSON_")
    assert safe["event_id"] == "evt-123"
    assert safe["duration_seconds"] == 12.5


def test_redaction_is_pure_and_preserves_event_structure(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation
    importlib.reload(presentation)

    raw = {
        "events": [
            {
                "event_id": "e1",
                "observed_at": "2026-09-17T08:00:00+00:00",
                "session_id": "s1",
                "device_id": "d1",
                "app": "Google Chrome",
                "window_title": "Question - Anna Svensson - Gmail",
                "event_type": "focus_span",
                "duration_seconds": 31.0,
                "metadata": {"activity": {"keypress_count": 18, "click_count": 2}},
            },
            {
                "event_id": "e2",
                "observed_at": "2026-09-17T08:00:31+00:00",
                "session_id": "s1",
                "device_id": "d1",
                "app": "Google Chrome",
                "window_title": "Question - Anna Svensson - Gmail",
                "event_type": "browser_click",
                "duration_seconds": 0.0,
                "metadata": {"action": "click", "target": {"label": "Send to anna.svensson@acme.com"}},
            },
        ]
    }
    before = json.loads(json.dumps(raw))
    safe = presentation.redact_for_display(raw)

    assert raw == before  # presentation redaction must not mutate inference input
    assert len(safe["events"]) == len(raw["events"])
    for original, shown in zip(raw["events"], safe["events"]):
        for key in ("event_id", "observed_at", "session_id", "device_id", "event_type", "duration_seconds"):
            assert shown[key] == original[key]
    assert safe["events"][0]["metadata"]["activity"] == raw["events"][0]["metadata"]["activity"]


def test_raw_database_keeps_rich_evidence_while_display_copy_is_masked(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.db as db
    import server.presentation as presentation
    importlib.reload(db)
    importlib.reload(presentation)
    db.init_db()

    event = {
        "event_id": "raw1",
        "observed_at": "2026-09-17T08:00:00+00:00",
        "device_id": "d1",
        "session_id": "s1",
        "app": "Google Chrome",
        "window_title": "Contract renewal - Anna Svensson <anna.svensson@acme.com> - Gmail",
        "event_type": "browser_click",
        "duration_seconds": 0.0,
        "metadata": {
            "source": "browser_extension",
            "action": "click",
            "page": {"hostname": "mail.google.com", "title": "Contract renewal - Anna Svensson <anna.svensson@acme.com> - Gmail"},
            "target": {"label": "Reply to Anna Svensson"},
        },
    }
    db.insert_events([event])
    stored = db.rows("SELECT * FROM events WHERE event_id = ?", ("raw1",))[0]
    shown = presentation.redact_for_display(stored)

    assert "Anna Svensson" in stored["window_title"]
    assert "anna.svensson@acme.com" in stored["window_title"]
    assert "Anna Svensson" not in shown["window_title"]
    assert "anna.svensson@acme.com" not in shown["window_title"].lower()
    assert stored["event_id"] == shown["event_id"] == "raw1"
    assert stored["event_type"] == shown["event_type"] == "browser_click"


def test_same_identifier_gets_stable_pseudonym(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation
    importlib.reload(presentation)

    first = presentation.redact_for_display({"label": "Send to anna.svensson@acme.com"})["label"]
    second = presentation.redact_for_display({"label": "Reply to anna.svensson@acme.com"})["label"]
    first_token = next(part for part in first.split() if part.startswith("EMAIL_"))
    second_token = next(part for part in second.split() if part.startswith("EMAIL_"))
    assert first_token == second_token
