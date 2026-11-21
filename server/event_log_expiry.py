"""Automatic deletion of a saved event log after a date the owner chooses.

An exported log is often needed for a project and not after it. The owner can set a date after which
the log is deleted from this computer, with everything derived from it (the same deletion as the Delete
log button), and change or clear the date at any time. Expired logs are deleted when the list of logs is
read, when an agent asks for a shared log, and by ``sweep()``; an expired log is never answered again,
even before the sweep that removes it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError
from shared.event_log_formats import parse_timestamp

FORMAT = "openworkgraph.event_log_expiry.v1"
MAX_YEARS = 10

# The log_expiry table is part of the workspace schema and is erased with its log by workspace.delete_log.


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _status(conn, log_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT delete_after, set_at FROM log_expiry WHERE log_id = ?", [log_id]).fetchone()
    return {"format": FORMAT, "log_id": log_id,
            "delete_after": row[0].isoformat() + "Z" if row else None,
            "set_at": row[1].isoformat() + "Z" if row else None,
            "note": "The log and everything derived from it are deleted from this computer after this time."}


def _with_conn(log_id: str, fn):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            return fn(conn)
        finally:
            conn.close()


def status(log_id: str) -> dict[str, Any]:
    return _with_conn(log_id, lambda conn: _status(conn, log_id))


def set_expiry(log_id: str, delete_after: Any) -> dict[str, Any]:
    """Set (an ISO time with an offset, in the future, at most 10 years ahead) or clear (None) the date."""
    when = None
    if delete_after is not None:
        stamp = parse_timestamp(delete_after) if isinstance(delete_after, str) and len(delete_after) <= 40 else None
        if stamp is None:
            raise EventLogWorkspaceError("delete_after must be a time with a UTC offset, or null to keep the log")
        when = stamp.astimezone(timezone.utc).replace(tzinfo=None)
        if when <= _now():
            raise EventLogWorkspaceError("delete_after must be in the future; use Delete log to delete it now")
        if when > _now() + timedelta(days=366 * MAX_YEARS):
            raise EventLogWorkspaceError(f"delete_after can be at most {MAX_YEARS} years ahead")

    def run(conn):
        conn.execute("DELETE FROM log_expiry WHERE log_id = ?", [log_id])
        if when is not None:
            conn.execute("INSERT INTO log_expiry VALUES (?, ?, ?)", [log_id, when, _now()])
        return _status(conn, log_id)
    return _with_conn(log_id, run)


def erase(conn, log_id: str) -> None:
    conn.execute("DELETE FROM log_expiry WHERE log_id = ?", [log_id])


def _expired() -> list[str]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            return [i for (i,) in conn.execute("SELECT log_id FROM log_expiry WHERE delete_after <= ? ORDER BY 1",
                                               [_now()]).fetchall()]
        finally:
            conn.close()


def is_expired(log_id: Any) -> bool:
    return isinstance(log_id, str) and log_id in _expired()


def sweep() -> dict[str, Any]:
    """Delete every log whose date has passed. Returns the deleted log IDs."""
    deleted = []
    for log_id in _expired():
        try:
            workspace.delete_log(log_id)
        except EventLogWorkspaceError:
            with workspace._LOCK:
                conn = workspace._connect()
                try:
                    erase(conn, log_id)      # the log is already gone
                finally:
                    conn.close()
            continue
        deleted.append(log_id)
    return {"format": FORMAT, "deleted": deleted}


__all__ = ["status", "set_expiry", "sweep", "is_expired", "erase", "FORMAT"]
