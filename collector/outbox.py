from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from sensitive_identifiers import sanitize_event_identifiers


class EventOutbox:
    """Small durable queue between local capture and the local API.

    Events are keyed by event_id, so retries are idempotent against the server's
    UNIQUE(event_id) constraint. The queue survives collector/API restarts and is
    removed only after a successful HTTP acknowledgement.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS pending_events (
                  event_id TEXT PRIMARY KEY,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_pending_created
                  ON pending_events(created_at);
                """
            )
        self._sanitize_existing_pending()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _sanitize_existing_pending(self) -> None:
        """One-time-on-start hardening for events queued by older versions."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT event_id, payload_json FROM pending_events"
            ).fetchall()
            for row in rows:
                try:
                    event = json.loads(row["payload_json"])
                    safe = sanitize_event_identifiers(event)
                    payload = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
                except Exception:
                    # Do not destroy a delivery queue merely because one legacy
                    # payload is malformed; new events are always sanitized.
                    continue
                if payload != row["payload_json"]:
                    conn.execute(
                        "UPDATE pending_events SET payload_json = ? WHERE event_id = ?",
                        (payload, row["event_id"]),
                    )

    def enqueue(self, event: dict[str, Any]) -> None:
        safe = sanitize_event_identifiers(event)
        event_id = str(safe.get("event_id") or "")
        if not event_id:
            raise ValueError("outbox event requires event_id")
        payload = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pending_events(event_id, payload_json) VALUES (?, ?)",
                (event_id, payload),
            )

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM pending_events ORDER BY created_at, rowid LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def acknowledge(self, event_ids: list[str]) -> None:
        ids = [str(x) for x in event_ids if x]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connect() as conn:
            conn.execute(f"DELETE FROM pending_events WHERE event_id IN ({placeholders})", tuple(ids))

    def mark_failed(self, event_ids: list[str], error: str) -> None:
        ids = [str(x) for x in event_ids if x]
        if not ids:
            return
        message = str(error)[:500]
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE pending_events SET attempts = attempts + 1, last_error = ? WHERE event_id IN ({placeholders})",
                (message, *ids),
            )

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM pending_events").fetchone()
        return int(row["n"] if row else 0)
