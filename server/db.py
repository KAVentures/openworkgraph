from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

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


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def insert_events(events: Iterable[dict[str, Any]]) -> int:
    inserted = 0
    with connect() as conn:
        for e in events:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO events(
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
            inserted += cur.rowcount
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
