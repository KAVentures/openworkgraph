from __future__ import annotations

import sqlite3
from pathlib import Path


class SyncState:
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

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def get_int(self, name: str, default: int = 0) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM sync_state WHERE name = ?", (name,)).fetchone()
        try:
            return int(row[0]) if row else int(default)
        except Exception:
            return int(default)

    def set_int(self, name: str, value: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sync_state(name, value) VALUES (?, ?)
                   ON CONFLICT(name) DO UPDATE SET value = excluded.value""",
                (name, str(int(value))),
            )
