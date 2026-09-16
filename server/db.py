from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from normalizer import normalize_event

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("WORKFLOW_OBSERVER_DATA", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "workflow_observer.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events (
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
CREATE INDEX IF NOT EXISTS idx_events_time ON events(observed_at);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_events_app ON events(app);

-- Privacy-safe operational layer derived from the rich local evidence above.
-- It intentionally mirrors the event shape so existing analytics can operate
-- on it without needing the raw content payload.
CREATE TABLE IF NOT EXISTS normalized_events (
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
CREATE INDEX IF NOT EXISTS idx_norm_time ON normalized_events(observed_at);
CREATE INDEX IF NOT EXISTS idx_norm_session ON normalized_events(session_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_norm_app ON normalized_events(app);
"""

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_event(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    d.pop("id", None)
    if "metadata_json" in d:
        d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
    return d


def _insert_one(conn: sqlite3.Connection, table: str, e: dict[str, Any]) -> int:
    cur = conn.execute(
        f"""
        INSERT OR IGNORE INTO {table}(
          event_id, observed_at, device_id, session_id, app, window_title,
          event_type, duration_seconds, screenshot_path, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            e["event_id"], e["observed_at"], e["device_id"], e["session_id"],
            e.get("app"), e.get("window_title"), e["event_type"],
            float(e.get("duration_seconds", 0) or 0), e.get("screenshot_path"),
            json.dumps(e.get("metadata") or {}, ensure_ascii=False),
        ),
    )
    return int(cur.rowcount)


def _backfill_normalized(conn: sqlite3.Connection) -> int:
    missing = conn.execute(
        """
        SELECT e.* FROM events e
        LEFT JOIN normalized_events n ON n.event_id = e.event_id
        WHERE n.event_id IS NULL
        ORDER BY e.id ASC
        """
    ).fetchall()
    inserted = 0
    for row in missing:
        raw = _row_to_event(row)
        inserted += _insert_one(conn, "normalized_events", normalize_event(raw))
    return inserted


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Existing v23 local data remains untouched; derive the safe layer next to
        # it once so a customer does not lose historical operational structure.
        _backfill_normalized(conn)


def insert_events(events: Iterable[dict[str, Any]]) -> int:
    """Persist rich local evidence and derive a safe operational twin.

    Return value remains the raw inserted count for backwards compatibility.
    """
    inserted = 0
    with connect() as conn:
        for raw in events:
            e = dict(raw)
            raw_inserted = _insert_one(conn, "events", e)
            inserted += raw_inserted
            # Even when the raw row already existed, INSERT OR IGNORE ensures the
            # operational twin is present (useful after upgrades/backfills).
            _insert_one(conn, "normalized_events", normalize_event(e))
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
    """Run a SELECT written against normalized_events.

    Kept separate from rows() to make raw-vs-operational intent explicit at call
    sites and reduce accidental exposure through future MCP/API additions.
    """
    if "normalized_events" not in query.lower():
        raise ValueError("normalized_rows queries must target normalized_events")
    return rows(query, params)
