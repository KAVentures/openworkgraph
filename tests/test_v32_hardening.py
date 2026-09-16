from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from collector.identity import load_or_create_identity
from collector.outbox import EventOutbox
from contextualizer import contextualize_event
from normalizer import normalize_event


def test_identity_is_stable_and_not_shared_default(tmp_path: Path):
    first = load_or_create_identity(tmp_path, {"device_id": "", "organization_id": "org-1", "actor_id": "user-1"})
    second = load_or_create_identity(tmp_path, {"device_id": "", "organization_id": "org-1", "actor_id": "user-1"})
    assert first == second
    assert first["device_id"]
    assert first["device_id"] != "my-laptop"
    assert first["sensor_id"].startswith("desktop:")


def test_outbox_survives_reopen_and_acknowledges(tmp_path: Path):
    path = tmp_path / "outbox.db"
    event = {"event_id": "evt-1", "observed_at": "2026-01-01T00:00:00Z"}
    outbox = EventOutbox(path)
    outbox.enqueue(event)
    assert outbox.count() == 1
    assert EventOutbox(path).pending() == [event]
    outbox.acknowledge(["evt-1"])
    assert EventOutbox(path).count() == 0


def test_normalized_and_context_layers_preserve_identity_but_differ_in_content():
    raw = {
        "event_id": "evt-1",
        "observed_at": "2026-01-01T00:00:00Z",
        "schema_version": "1.0",
        "organization_id": "org-1",
        "actor_id": "alice",
        "device_id": "device-1",
        "sensor_id": "browser:s1",
        "source": "browser_extension",
        "session_id": "session-1",
        "app": "Google Chrome",
        "window_title": "Acme renewal - Gmail",
        "event_type": "browser_click",
        "duration_seconds": 0,
        "metadata": {
            "source": "browser_extension",
            "action": "click",
            "page": {"hostname": "mail.google.com", "pathname": "/mail/u/0/inbox", "title": "Acme renewal - Gmail"},
            "target": {"role": "button", "label": "Reply"},
        },
    }
    operational = normalize_event(raw)
    context = contextualize_event(raw)

    for key in ("organization_id", "actor_id", "device_id", "sensor_id", "session_id"):
        assert operational[key] == raw[key]
        assert context[key] == raw[key]

    assert operational["app"] == "Gmail"
    assert "Acme" not in operational["window_title"]
    assert "Acme renewal" in context["resource_title"]
    assert context["resource_locator"] == "mail.google.com/mail/u/0/inbox"


def test_existing_v31_database_migrates_without_identity_index_failure(tmp_path: Path, monkeypatch):
    from server import db

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    legacy = """
    CREATE TABLE events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id TEXT NOT NULL UNIQUE,
      observed_at TEXT NOT NULL,
      device_id TEXT NOT NULL,
      session_id TEXT NOT NULL,
      app TEXT,
      window_title TEXT,
      event_type TEXT NOT NULL,
      duration_seconds REAL DEFAULT 0,
      screenshot_path TEXT,
      metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE normalized_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id TEXT NOT NULL UNIQUE,
      observed_at TEXT NOT NULL,
      device_id TEXT NOT NULL,
      session_id TEXT NOT NULL,
      app TEXT,
      window_title TEXT,
      event_type TEXT NOT NULL,
      duration_seconds REAL DEFAULT 0,
      screenshot_path TEXT,
      metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    """
    conn.executescript(legacy)
    conn.execute(
        "INSERT INTO events(event_id,observed_at,device_id,session_id,app,window_title,event_type,metadata_json) VALUES (?,?,?,?,?,?,?,?)",
        ("legacy-1", "2026-01-01T00:00:00Z", "old-device", "s1", "Chrome", "Gmail", "focus_span", json.dumps({"activity": {}})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()

    with sqlite3.connect(path) as check:
        event_columns = {row[1] for row in check.execute("PRAGMA table_info(events)")}
        assert {"schema_version", "organization_id", "actor_id", "sensor_id", "source"} <= event_columns
        assert check.execute("SELECT COUNT(*) FROM context_events WHERE event_id='legacy-1'").fetchone()[0] == 1
        assert check.execute("SELECT COUNT(*) FROM normalized_events WHERE event_id='legacy-1'").fetchone()[0] == 1


def test_windows_build_installs_input_sensor_dependency():
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert "platform_system == 'Darwin' or platform_system == 'Windows'" in text
