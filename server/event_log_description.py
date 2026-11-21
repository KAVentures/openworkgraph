"""A short description the owner keeps with a saved log: what it is, where it came from, what it is for.

Logs are named at import, often after a file. A description ("SAP export for the Q3 audit, finance only")
keeps the context with the log. It is the owner's note, never part of the events, and is deleted with the
log. The table is created on first use, so older workspaces need no migration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_description.v1"
MAX_CHARS = 2000
_TABLE = """CREATE TABLE IF NOT EXISTS log_descriptions (
  log_id VARCHAR NOT NULL, description VARCHAR NOT NULL, updated_at TIMESTAMP NOT NULL)"""


def _with_conn(log_id: str, fn):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            conn.execute(_TABLE)
            return fn(conn)
        finally:
            conn.close()


def _read(conn, log_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT description, updated_at FROM log_descriptions WHERE log_id = ?", [log_id]).fetchone()
    return {"format": FORMAT, "log_id": log_id, "description": row[0] if row else "",
            "updated_at": row[1].isoformat() + "Z" if row else None}


def description(log_id: str) -> dict[str, Any]:
    return _with_conn(log_id, lambda conn: _read(conn, log_id))


def set_description(log_id: str, text: Any) -> dict[str, Any]:
    if not isinstance(text, str) or len(text) > MAX_CHARS:
        raise EventLogWorkspaceError(f"the description is text of at most {MAX_CHARS} characters")
    text = text.strip()
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
        raise EventLogWorkspaceError("the description must not contain control characters")

    def run(conn):
        conn.execute("DELETE FROM log_descriptions WHERE log_id = ?", [log_id])
        if text:
            conn.execute("INSERT INTO log_descriptions VALUES (?, ?, ?)",
                         [log_id, text, datetime.now(timezone.utc).replace(tzinfo=None)])
        return _read(conn, log_id)
    return _with_conn(log_id, run)


def erase_log(log_id: str) -> None:
    """Called by workspace.delete_log after the log's own rows are gone."""
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute(_TABLE)
            conn.execute("DELETE FROM log_descriptions WHERE log_id = ?", [log_id])
        finally:
            conn.close()


__all__ = ["description", "set_description", "erase_log", "FORMAT"]
