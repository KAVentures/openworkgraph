from __future__ import annotations

import sqlite3
from pathlib import Path


class SyncState:
    """Small endpoint-local state store for Gateway synchronization.

    This database contains no workflow event payloads and no credentials. It only
    tracks the local event cursor, user-controlled pause state, and coarse sync
    health/status so local capture can continue independently of the Gateway.
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
