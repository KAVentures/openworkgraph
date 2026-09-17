from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from browser_privacy import harden_browser_event
from contextualizer import contextualize_event
from normalizer import normalize_event

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("WORKFLOW_OBSERVER_DATA", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "workflow_observer.db"

IDENTITY_COLUMNS = """
  schema_version TEXT NOT NULL DEFAULT '1.0',
  organization_id TEXT NOT NULL DEFAULT '',
  actor_id TEXT NOT NULL DEFAULT '',
  sensor_id TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'desktop',
"""

SCHEMA = f"""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  observed_at TEXT NOT NULL,
  {IDENTITY_COLUMNS}
  device_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  app TEXT,
  window_title TEXT,
  event_type TEXT NOT NULL,
  duration_seconds REAL DEFAULT 0,
  screenshot_path TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(observed_at);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_events_app ON events(app);

CREATE TABLE IF NOT EXISTS normalized_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  observed_at TEXT NOT NULL,
  {IDENTITY_COLUMNS}
  device_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  app TEXT,
  window_title TEXT,
  event_type TEXT NOT NULL,
  duration_seconds REAL DEFAULT 0,
  screenshot_path TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS idx_norm_time ON normalized_events(observed_at);
CREATE INDEX IF NOT EXISTS idx_norm_session ON normalized_events(session_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_norm_app ON normalized_events(app);

CREATE TABLE IF NOT EXISTS context_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  observed_at TEXT NOT NULL,
  schema_version TEXT NOT NULL DEFAULT '1.0',
  organization_id TEXT NOT NULL DEFAULT '',
  actor_id TEXT NOT NULL DEFAULT '',
  device_id TEXT NOT NULL DEFAULT '',
  sensor_id TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'desktop',
  surface TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '',
  resource_title TEXT NOT NULL DEFAULT '',
  resource_locator TEXT NOT NULL DEFAULT '',
  target_label TEXT NOT NULL DEFAULT '',
  context_text TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS idx_context_time ON context_events(observed_at);
CREATE INDEX IF NOT EXISTS idx_context_session ON context_events(session_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_context_surface ON context_events(surface, observed_at);
CREATE INDEX IF NOT EXISTS idx_context_actor ON context_events(actor_id, observed_at);

CREATE TABLE IF NOT EXISTS privacy_migrations (
  migration_key TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_identity_columns(conn: sqlite3.Connection, table: str) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    wanted = {
        "schema_version": "TEXT NOT NULL DEFAULT '1.0'",
        "organization_id": "TEXT NOT NULL DEFAULT ''",
        "actor_id": "TEXT NOT NULL DEFAULT ''",
        "sensor_id": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT 'desktop'",
    }
    for name, definition in wanted.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _row_to_event(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    d.pop("id", None)
    if "metadata_json" in d:
        d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
    return d


def _identity_value(e: dict[str, Any], key: str, default: str = "") -> str:
    meta = e.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    if key == "source":
        return str(e.get(key) or meta.get("source") or default or "desktop")
    return str(e.get(key) or default)


def _insert_event(conn: sqlite3.Connection, table: str, e: dict[str, Any]) -> int:
    cur = conn.execute(
        f"""
        INSERT OR IGNORE INTO {table}(
          event_id, observed_at, schema_version, organization_id, actor_id,
          sensor_id, source, device_id, session_id, app, window_title,
          event_type, duration_seconds, screenshot_path, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            e["event_id"], e["observed_at"], str(e.get("schema_version") or "1.0"),
            _identity_value(e, "organization_id"), _identity_value(e, "actor_id"),
            _identity_value(e, "sensor_id"), _identity_value(e, "source", "desktop"),
            str(e.get("device_id") or ""), str(e.get("session_id") or ""),
            e.get("app"), e.get("window_title"), e["event_type"],
            float(e.get("duration_seconds", 0) or 0), e.get("screenshot_path"),
            json.dumps(e.get("metadata") or {}, ensure_ascii=False),
        ),
    )
    return int(cur.rowcount)


def _insert_context(conn: sqlite3.Connection, c: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO context_events(
          event_id, observed_at, schema_version, organization_id, actor_id,
          device_id, sensor_id, session_id, source, surface, action,
          resource_title, resource_locator, target_label, context_text, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            c["event_id"], c["observed_at"], c.get("schema_version", "1.0"),
            c.get("organization_id", ""), c.get("actor_id", ""), c.get("device_id", ""),
            c.get("sensor_id", ""), c.get("session_id", ""), c.get("source", "desktop"),
            c.get("surface", ""), c.get("action", ""), c.get("resource_title", ""),
            c.get("resource_locator", ""), c.get("target_label", ""), c.get("context_text", ""),
            json.dumps(c.get("metadata") or {}, ensure_ascii=False),
        ),
    )
    return int(cur.rowcount)


def _backfill_derived(conn: sqlite3.Connection) -> None:
    missing_norm = conn.execute(
        """
        SELECT e.* FROM events e
        LEFT JOIN normalized_events n ON n.event_id = e.event_id
        WHERE n.event_id IS NULL
        ORDER BY e.id ASC
        """
    ).fetchall()
    for row in missing_norm:
        raw = _row_to_event(row)
        _insert_event(conn, "normalized_events", normalize_event(raw))

    missing_context = conn.execute(
        """
        SELECT e.* FROM events e
        LEFT JOIN context_events c ON c.event_id = e.event_id
        WHERE c.event_id IS NULL
        ORDER BY e.id ASC
        """
    ).fetchall()
    for row in missing_context:
        _insert_context(conn, contextualize_event(_row_to_event(row)))


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _ensure_identity_columns(conn, "events")
        _ensure_identity_columns(conn, "normalized_events")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_events_sensor ON events(sensor_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_norm_actor ON normalized_events(actor_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_norm_sensor ON normalized_events(sensor_id, observed_at);
            """
        )
        _backfill_derived(conn)


def _browser_privacy_migration_key(config: dict[str, Any]) -> str:
    relevant = {
        "excluded_apps": config.get("excluded_apps") or [],
        "excluded_title_patterns": config.get("excluded_title_patterns") or [],
        "excluded_browser_host_patterns": config.get("excluded_browser_host_patterns") or [],
    }
    digest = hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return f"browser_privacy_v39:{digest}"


def harden_existing_browser_events(config: dict[str, Any]) -> int:
    """Sanitize legacy browser rows once for each effective privacy config."""
    changed = 0
    migration_key = _browser_privacy_migration_key(config)
    with connect() as conn:
        if conn.execute(
            "SELECT 1 FROM privacy_migrations WHERE migration_key = ?", (migration_key,)
        ).fetchone():
            return 0
        existing = conn.execute(
            "SELECT * FROM events WHERE event_type LIKE 'browser_%' ORDER BY id ASC"
        ).fetchall()
        for row in existing:
            raw = _row_to_event(row)
            safe = harden_browser_event(raw, config)
            if safe == raw:
                continue
            conn.execute(
                """
                UPDATE events SET app = ?, window_title = ?, screenshot_path = ?, metadata_json = ?
                WHERE event_id = ?
                """,
                (
                    safe.get("app"), safe.get("window_title"), safe.get("screenshot_path"),
                    json.dumps(safe.get("metadata") or {}, ensure_ascii=False),
                    safe.get("event_id"),
                ),
            )
            conn.execute("DELETE FROM normalized_events WHERE event_id = ?", (safe.get("event_id"),))
            conn.execute("DELETE FROM context_events WHERE event_id = ?", (safe.get("event_id"),))
            _insert_event(conn, "normalized_events", normalize_event(safe))
            _insert_context(conn, contextualize_event(safe))
            changed += 1
        conn.execute(
            "INSERT OR IGNORE INTO privacy_migrations(migration_key) VALUES (?)", (migration_key,)
        )
    return changed


def insert_events(events: Iterable[dict[str, Any]]) -> int:
    """Persist rich evidence plus operational and context derivatives atomically."""
    inserted = 0
    with connect() as conn:
        for raw in events:
            e = dict(raw)
            inserted += _insert_event(conn, "events", e)
            _insert_event(conn, "normalized_events", normalize_event(e))
            _insert_context(conn, contextualize_event(e))
    return inserted


def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        result = []
        for row in conn.execute(query, params).fetchall():
            d = dict(row)
            if "metadata_json" in d:
                d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            result.append(d)
        return result


def normalized_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if "normalized_events" not in query.lower():
        raise ValueError("normalized_rows queries must target normalized_events")
    return rows(query, params)


def context_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if "context_events" not in query.lower():
        raise ValueError("context_rows queries must target context_events")
    return rows(query, params)
