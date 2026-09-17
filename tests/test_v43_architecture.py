from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient


def _event(i: int, *, session: str = "s1") -> dict:
    base = datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc)
    at = base + timedelta(seconds=i)
    if i % 50 == 0:
        return {
            "event_id": f"f-{i}",
            "observed_at": at.isoformat(),
            "device_id": "d1",
            "session_id": session,
            "app": "Google Chrome",
            "window_title": "Inbox - Gmail - Google Chrome",
            "event_type": "focus_span",
            "duration_seconds": 50.0,
            "metadata": {"activity": {
                "engaged_seconds": 40.0,
                "idle_seconds": 10.0,
                "active_input_seconds": 20.0,
                "keypress_count": 20,
                "click_count": 2,
                "scroll_count": 1,
            }},
        }
    action = "Send" if i % 20 == 19 else "Open message"
    return {
        "event_id": f"e-{i}",
        "observed_at": at.isoformat(),
        "device_id": "d1",
        "session_id": session,
        "app": "Google Chrome",
        "window_title": "Inbox - Gmail",
        "event_type": "browser_click",
        "duration_seconds": 0.0,
        "metadata": {
            "action": "click",
            "page": {"hostname": "mail.google.com", "pathname": "/mail/u/0/inbox", "title": "Inbox - Gmail"},
            "target": {"role": "button", "label": action},
        },
    }


def test_summary_cache_does_not_recompute_unchanged_revision(monkeypatch):
    from server import analytics

    analytics.clear_summary_cache()
    calls = {"n": 0}
    monkeypatch.setattr(analytics, "table_revision", lambda operational=False: (123, 0))

    def fake(limit=10000, since=None, operational=False):
        calls["n"] += 1
        return {"recent_evidence": [], "recent_browser_actions": [], "events": 1}

    monkeypatch.setattr(analytics, "_summary_uncached", fake)
    first = analytics.summary(1000, since="2026-09-18T00:00:00+00:00")
    second = analytics.summary(1000, since="2026-09-18T00:00:00+00:00")
    assert calls["n"] == 1
    assert first == second
    assert first is not second


def test_candidate_tasks_can_reuse_summary_snapshot(monkeypatch):
    from server import analytics

    events = [_event(i) for i in range(200)]
    monkeypatch.setattr(
        analytics,
        "_event_rows",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected second DB load")),
    )
    result = analytics.candidate_tasks(_raw_events=events)
    assert result["tasks"]


def test_5100_event_summary_path_is_bounded(monkeypatch):
    """Regression guard against returning to per-task full-session rescans."""
    from server import analytics

    events = [_event(i) for i in range(5100)]
    analytics.clear_summary_cache()
    monkeypatch.setattr(analytics, "table_revision", lambda operational=False: (5100, 0))
    monkeypatch.setattr(analytics, "_event_rows", lambda limit=10000, since=None: events[:limit])

    started = time.perf_counter()
    result = analytics.summary(10000, since="2026-09-18T00:00:00+00:00")
    elapsed = time.perf_counter() - started

    assert result["events"] == 5100
    assert result["candidate_task_count"] > 0
    # Intentionally generous for GitHub's slow Windows runners. The old measured
    # dashboard path was ~4.5 s at this size; this in-memory path should be far lower.
    assert elapsed < 3.5


def test_importing_server_has_no_policy_install_side_effects():
    root = Path(__file__).resolve().parents[1]
    source = (root / "server" / "__init__.py").read_text(encoding="utf-8")
    assert "install(" not in source
    assert "_install_" not in source


def test_derived_single_os_first_name_is_not_global_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    import server.presentation as presentation

    monkeypatch.setattr(presentation, "_load_config", lambda: {"owner_aliases": []})
    monkeypatch.setattr(presentation, "_windows_display_name", lambda: "")
    monkeypatch.setattr(presentation, "_posix_display_name", lambda: "Koyar Afrasyab")
    monkeypatch.setattr(presentation.getpass, "getuser", lambda: "koyar")
    presentation._OWNER_CACHE.clear()

    aliases, _, _ = presentation._owner_identity()
    assert aliases.get("koyar afrasyab") == "OWNER"
    assert "koyar" not in aliases


def test_http_get_summary_is_read_only_after_startup(monkeypatch, tmp_path):
    from server import db, main, presentation

    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "workflow_observer.db")
    presentation._KEY_CACHE.clear()
    presentation._OWNER_CACHE.clear()
    main.COLLECTOR_STATUS.clear()
    main.BROWSER_STATUS.clear()

    with TestClient(main.app) as client:
        payload = {
            "events": [{
                "event_id": "http-identity-1",
                "observed_at": "2026-09-18T08:00:00+00:00",
                "device_id": "d1",
                "session_id": "s1",
                "app": "Google Chrome",
                "window_title": "Inbox - Gmail",
                "event_type": "browser_click",
                "metadata": {
                    "action": "click",
                    "page": {"hostname": "mail.google.com", "pathname": "/mail/u/0/inbox", "title": "Inbox - Gmail"},
                    "target": {"label": "Reply to Sarah Johnson"},
                },
            }]
        }
        assert client.post("/v1/events", json=payload).status_code == 200

        key_path = tmp_path / ".display_redaction_key"
        registry_path = tmp_path / ".presentation_people.json"
        assert key_path.exists()
        assert registry_path.exists()
        before_key = key_path.read_bytes()
        before_registry = registry_path.read_bytes()
        key_mtime = key_path.stat().st_mtime_ns
        registry_mtime = registry_path.stat().st_mtime_ns

        assert client.get("/v1/summary", params={"scope": "current"}).status_code == 200
        assert client.get("/v1/summary", params={"scope": "current"}).status_code == 200

        assert key_path.read_bytes() == before_key
        assert registry_path.read_bytes() == before_registry
        assert key_path.stat().st_mtime_ns == key_mtime
        assert registry_path.stat().st_mtime_ns == registry_mtime


def test_http_browser_ingest_strips_url_secrets_before_db(monkeypatch, tmp_path):
    from server import db, main, presentation

    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "workflow_observer.db")
    presentation._KEY_CACHE.clear()
    main.COLLECTOR_STATUS.clear()
    main.BROWSER_STATUS.clear()

    with TestClient(main.app) as client:
        main.COLLECTOR_STATUS.update({"app": "Google Chrome", "session_id": "s1", "device_id": "d1"})
        body = {
            "event_id": "http-browser-secret",
            "observed_at": "2026-09-18T08:01:00+00:00",
            "device_id": "d1",
            "work_session_id": "s1",
            "sensor_version": main.EXPECTED_BROWSER_SENSOR_VERSION,
            "action": "click",
            "page": {
                "origin": "https://example.com",
                "hostname": "example.com",
                "pathname": "/reset/0123456789abcdef0123456789abcdef?session=SECRET123#confirm",
                "title": "Reset",
            },
            "target": {"role": "button", "label": "Continue"},
            "metadata": {"frame_url": "https://example.com/reset?token=SECRET123#confirm"},
        }
        response = client.post("/v1/browser-events", json=body)
        assert response.status_code == 200

        stored = db.rows("SELECT * FROM events WHERE event_id = ?", ("http-browser-secret",))[0]
        blob = json.dumps(stored)
        assert "SECRET123" not in blob
        assert "0123456789abcdef0123456789abcdef" not in blob
        assert stored["metadata"]["page"]["pathname"] == "/reset/:token"
