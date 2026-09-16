from __future__ import annotations

import json
from pathlib import Path


def _raw_browser(event_id: str, ts: str, *, title: str, host: str, path: str, label: str, action: str = "click"):
    return {
        "event_id": event_id,
        "observed_at": ts,
        "device_id": "d1",
        "session_id": "s1",
        "app": "Google Chrome",
        "window_title": title,
        "event_type": f"browser_{action}",
        "duration_seconds": 0,
        "screenshot_path": None,
        "metadata": {
            "source": "browser_extension",
            "action": action,
            "page": {"hostname": host, "pathname": path, "title": title},
            "target": {"tag": "button", "label": label},
        },
    }


def test_normalizer_preserves_structure_but_not_payload():
    from normalizer import normalize_event

    raw = _raw_browser(
        "e1", "2026-01-01T00:00:00Z",
        title="Alice Example <alice@example.com> — RE: Confidential salary - Gmail",
        host="mail.google.com",
        path="/mail/u/0/#inbox/abc123",
        label="Send to Alice Example <alice@example.com>",
    )
    safe = normalize_event(raw)
    blob = json.dumps(safe, ensure_ascii=False)
    assert safe["app"] == "Gmail"
    assert safe["window_title"] == "Gmail"
    assert safe["metadata"]["target"]["label"] == "Send"
    assert "Alice" not in blob
    assert "alice@example.com" not in blob
    assert "Confidential salary" not in blob
    assert "abc123" not in blob
    assert "mail.google.com" not in blob


def test_dual_storage_keeps_raw_and_safe_separately(monkeypatch, tmp_path):
    # Reload db after redirecting its data directory so this test is isolated.
    import importlib
    import os
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.db as db
    importlib.reload(db)
    db.init_db()

    raw = _raw_browser(
        "e2", "2026-01-01T00:00:00Z",
        title="Bob Example — Project Phoenix - GitHub",
        host="github.com",
        path="/Acme/Project-Phoenix/issues/48213",
        label="Create repository Project Phoenix",
    )
    assert db.insert_events([raw]) == 1
    raw_rows = db.rows("SELECT * FROM events")
    safe_rows = db.normalized_rows("SELECT * FROM normalized_events")
    assert len(raw_rows) == len(safe_rows) == 1
    assert "Project Phoenix" in json.dumps(raw_rows[0])
    safe_blob = json.dumps(safe_rows[0])
    assert safe_rows[0]["app"] == "GitHub"
    assert "Project Phoenix" not in safe_blob
    assert "48213" not in safe_blob


def test_two_fresh_emails_repeat_on_safe_task_layer(monkeypatch):
    from server import analytics

    sample = [
        {
            "event_id":"f1","device_id":"d","session_id":"s1","observed_at":"2026-01-01T00:00:00Z",
            "app":"Google Chrome","window_title":"Alice <alice@example.com> - New Message - Gmail",
            "event_type":"focus_span","duration_seconds":28,
            "metadata":{"activity":{"engaged_seconds":25,"keypress_count":90,"click_count":4}},
        },
        _raw_browser("b1", "2026-01-01T00:00:02Z", title="Alice - Gmail", host="mail.google.com", path="/mail/u/0/#inbox/a", label="Compose"),
        _raw_browser("b2", "2026-01-01T00:00:24Z", title="Alice - Gmail", host="mail.google.com", path="/mail/u/0/#inbox/a", label="Send to alice@example.com"),
        {
            "event_id":"f2","device_id":"d","session_id":"s1","observed_at":"2026-01-01T00:01:00Z",
            "app":"Google Chrome","window_title":"Bob <bob@example.com> - New Message - Gmail",
            "event_type":"focus_span","duration_seconds":31,
            "metadata":{"activity":{"engaged_seconds":29,"keypress_count":105,"click_count":5}},
        },
        _raw_browser("b3", "2026-01-01T00:01:03Z", title="Bob - Gmail", host="mail.google.com", path="/mail/u/0/#inbox/b", label="Compose"),
        _raw_browser("b4", "2026-01-01T00:01:27Z", title="Bob - Gmail", host="mail.google.com", path="/mail/u/0/#inbox/b", label="Send to bob@example.com"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=20)
    assert len(out["patterns"]) == 1
    p = out["patterns"][0]
    assert p["task_family"] == "email.compose_send"
    assert p["observed_count"] == 2
    assert p["suggested_label"] == "Compose and send email"

def test_two_live_emails_repeat_without_finalized_focus_span(monkeypatch):
    """Browser semantic evidence alone should produce the repeated family live."""
    from server import analytics
    sample = [
        _raw_browser("l1", "2026-01-01T00:00:01Z", title="Alice subject - Gmail", host="mail.google.com", path="/mail/u/0/#compose/a", label="Compose"),
        _raw_browser("l2", "2026-01-01T00:00:15Z", title="Alice subject - Gmail", host="mail.google.com", path="/mail/u/0/#compose/a", label="Send to alice@example.com"),
        _raw_browser("l3", "2026-01-01T00:00:20Z", title="Bob subject - Gmail", host="mail.google.com", path="/mail/u/0/#compose/b", label="Compose"),
        _raw_browser("l4", "2026-01-01T00:00:37Z", title="Bob subject - Gmail", host="mail.google.com", path="/mail/u/0/#compose/b", label="Send to bob@example.com"),
    ]
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=25000, since=None: sample)
    out = analytics.candidate_tasks(gap_seconds=60)
    assert len(out["tasks"]) == 2
    assert len(out["patterns"]) == 1
    assert out["patterns"][0]["task_family"] == "email.compose_send"
    assert out["patterns"][0]["observed_count"] == 2
