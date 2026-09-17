from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from browser_privacy import harden_browser_event, sanitize_pathname, sanitize_url_value


def _browser_event(*, title: str = "Transfer", frame_url: str = "https://bank.example/transfer?session=SECRET123#confirm") -> dict:
    return {
        "event_id": "browser-test-1",
        "observed_at": "2026-09-17T20:00:00+00:00",
        "schema_version": "1.0",
        "device_id": "device",
        "sensor_id": "browser:test",
        "source": "browser_extension",
        "session_id": "session",
        "app": "Google Chrome",
        "window_title": title,
        "event_type": "browser_click",
        "duration_seconds": 0,
        "metadata": {
            "source": "browser_extension",
            "action": "click",
            "frame_url": frame_url,
            "page": {
                "origin": "https://bank.example",
                "hostname": "bank.example",
                "pathname": "/reset/0123456789abcdef0123456789abcdef?session=SECRET123#confirm",
                "title": title,
            },
            "target": {"role": "button", "label": "Confirm transfer"},
            "privacy": {"url_query": False, "url_fragment": False},
        },
    }


def test_url_values_strip_queries_fragments_and_secret_like_paths():
    safe = sanitize_url_value("https://bank.example/reset/0123456789abcdef0123456789abcdef?session=SECRET123#confirm")
    assert safe == "https://bank.example/reset/:token"
    assert "SECRET123" not in safe
    assert "#" not in safe and "?" not in safe
    assert sanitize_pathname("/customers/123456789/orders/42?x=y") == "/customers/:id/orders/42"


def test_browser_exclusion_erases_page_and_target_but_keeps_structure():
    raw = _browser_event(title="Överföring – Mitt Bankkonto")
    safe = harden_browser_event(raw, {
        "excluded_apps": [],
        "excluded_title_patterns": ["bank"],
        "excluded_browser_host_patterns": [],
    })
    assert safe["app"] == "Excluded"
    assert safe["window_title"] == ""
    assert safe["event_type"] == "browser_click"
    assert safe["metadata"]["action"] == "click"
    assert safe["metadata"]["excluded"] is True
    serialized = json.dumps(safe, ensure_ascii=False)
    assert "Mitt Bankkonto" not in serialized
    assert "Confirm transfer" not in serialized
    assert "bank.example" not in serialized
    assert "SECRET123" not in serialized


def test_nonexcluded_browser_event_is_sanitized_without_losing_semantics():
    raw = _browser_event(title="Customer workflow")
    safe = harden_browser_event(raw, {
        "excluded_apps": [],
        "excluded_title_patterns": [],
        "excluded_browser_host_patterns": [],
    })
    serialized = json.dumps(safe, ensure_ascii=False)
    assert safe["metadata"]["page"]["hostname"] == "bank.example"
    assert safe["metadata"]["page"]["pathname"] == "/reset/:token"
    assert safe["metadata"]["target"]["label"] == "Confirm transfer"
    assert "SECRET123" not in serialized
    assert "0123456789abcdef0123456789abcdef" not in serialized


def test_api_rejects_rebinding_hosts_and_history_reads_from_extensions():
    from server import main

    client = TestClient(main.app)
    assert client.get("/health", headers={"host": "attacker.example"}).status_code == 400
    assert client.get("/health", headers={"origin": "https://attacker.example"}).status_code == 403

    extension_origin = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert client.get("/v1/summary", headers={"origin": extension_origin}).status_code == 403
    assert client.get("/v1/events", headers={"origin": extension_origin}).status_code == 403
    assert client.get("/v1/browser-context", headers={"origin": extension_origin}).status_code == 200
    assert client.get("/health", headers={"origin": "http://127.0.0.1:8787"}).status_code == 200


def test_browser_ingest_defense_in_depth_sanitizes_old_sensor_payload(monkeypatch):
    from server import main

    captured: list[dict] = []
    monkeypatch.setattr(main, "insert_events", lambda events: captured.extend(events) or len(events))
    monkeypatch.setattr(main, "_runtime_config", lambda: {
        "excluded_apps": [], "excluded_title_patterns": [], "excluded_browser_host_patterns": []
    })
    main.COLLECTOR_STATUS.clear()
    main.COLLECTOR_STATUS.update({"app": "Google Chrome", "session_id": "session", "device_id": "device"})

    event = main.BrowserEvent(
        event_id="old-sensor",
        observed_at="2026-09-17T20:00:00+00:00",
        device_id="device",
        work_session_id="session",
        action="click",
        page={
            "origin": "https://bank.example",
            "hostname": "bank.example",
            "pathname": "/reset/0123456789abcdef0123456789abcdef?session=SECRET123",
            "title": "Customer workflow",
        },
        target={"role": "button", "label": "Continue"},
        metadata={"frame_url": "https://bank.example/transfer?session=SECRET123#confirm"},
    )
    result = main.browser_event(event)
    assert result["inserted"] == 1
    serialized = json.dumps(captured[0], ensure_ascii=False)
    assert "SECRET123" not in serialized
    assert "0123456789abcdef0123456789abcdef" not in serialized
    assert captured[0]["metadata"]["page"]["pathname"] == "/reset/:token"


def test_existing_db_browser_rows_are_migrated(monkeypatch, tmp_path):
    from server import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "workflow_observer.db")
    db.init_db()
    raw = _browser_event(title="Customer workflow")
    db.insert_events([raw])
    before = db.rows("SELECT * FROM events WHERE event_id = ?", (raw["event_id"],))[0]
    assert "SECRET123" in json.dumps(before)

    changed = db.harden_existing_browser_events({
        "excluded_apps": [], "excluded_title_patterns": [], "excluded_browser_host_patterns": []
    })
    assert changed == 1
    after = db.rows("SELECT * FROM events WHERE event_id = ?", (raw["event_id"],))[0]
    serialized = json.dumps(after)
    assert "SECRET123" not in serialized
    assert after["metadata"]["page"]["pathname"] == "/reset/:token"
    context = db.context_rows("SELECT * FROM context_events WHERE event_id = ?", (raw["event_id"],))[0]
    assert "SECRET123" not in json.dumps(context)


def test_extension_source_does_not_send_full_frame_url_and_suppresses_subframe_navigation():
    root = Path(__file__).resolve().parents[1]
    content = (root / "browser_extension" / "content.js").read_text(encoding="utf-8")
    background = (root / "browser_extension" / "background.js").read_text(encoding="utf-8")
    assert "frame_url: location.href" not in content
    assert "if (!isTopFrame()) return;" in content
    assert "sanitizePendingBrowserQueue" in background
    assert "pageSource = topFrame" in background
    assert "sender.tab?.url" in background


def test_demo_data_populates_current_analytics_with_real_focus_spans(monkeypatch):
    from demo_data import build_demo_events
    from server import analytics

    base = datetime.now(timezone.utc) - timedelta(hours=1)
    events = build_demo_events(base)
    assert sum(e["event_type"] == "focus_span" for e in events) == 12
    assert sum(e["event_type"].startswith("browser_") for e in events) == 12
    assert not any(e["event_type"] == "window_change" for e in events)

    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: events)
    out = analytics.summary(since=base.isoformat())
    assert out["focus_events"] == 12
    assert out["total_engaged_seconds"] > 0
    assert out["keypress_count"] > 0
    assert len(out["surfaces"]) >= 3
    assert out["candidate_task_count"] > 0
