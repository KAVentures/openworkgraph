from __future__ import annotations

import sqlite3
from pathlib import Path


class SyncState:
    """Endpoint-local state for optional Gateway synchronization.

    This database contains no workflow event payloads and no credentials. It tracks
    cursor/health state plus explicit local-ID ranges that must never be uploaded
    (for example evidence captured while the user paused sharing) and terminally
    invalid rows quarantined from synchronization.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sync_state(
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS skip_ranges(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_id INTEGER NOT NULL,
                    end_id INTEGER NOT NULL,
                    reason TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS quarantine(
                    local_id INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def get(self, name: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM sync_state WHERE name = ?", (name,)).fetchone()
        return str(row[0]) if row else str(default)

    def set(self, name: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sync_state(name, value) VALUES (?, ?)
                   ON CONFLICT(name) DO UPDATE SET value = excluded.value""",
                (name, str(value)),
            )

    def delete(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sync_state WHERE name = ?", (name,))

    def get_int(self, name: str, default: int = 0) -> int:
        try:
            return int(self.get(name, str(int(default))))
        except Exception:
            return int(default)

    def set_int(self, name: str, value: int) -> None:
        self.set(name, str(int(value)))

    def get_bool(self, name: str, default: bool = False) -> bool:
        raw = self.get(name, "1" if default else "0").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def set_bool(self, name: str, value: bool) -> None:
        self.set(name, "1" if value else "0")

    def add_skip_range(self, start_id: int, end_id: int, reason: str) -> None:
        start = int(start_id)
        end = int(end_id)
        if end < start:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO skip_ranges(start_id, end_id, reason) VALUES (?, ?, ?)",
                (start, end, str(reason)[:120]),
            )

    def skipped(self, local_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM skip_ranges WHERE start_id <= ? AND end_id >= ? LIMIT 1",
                (int(local_id), int(local_id)),
            ).fetchone()
        return bool(row)

    def quarantine(self, local_id: int, event_id: str, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO quarantine(local_id, event_id, reason) VALUES (?, ?, ?)
                   ON CONFLICT(local_id) DO UPDATE SET event_id=excluded.event_id, reason=excluded.reason""",
                (int(local_id), str(event_id), str(reason)[:500]),
            )

    def quarantine_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()
        return int(row[0] if row else 0)
