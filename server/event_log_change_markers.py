"""Change markers: dated notes on a log's timeline, such as "Automation went live" or "New approval rule".

Before/after impact, drift and period comparisons all start from a date. The owner records each change
once, with a short label and an optional note, and picks it again later instead of retyping the date.
Markers are the owner's notes, not events of the log, and never change its data. Deleting the log
deletes its markers.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.event_log_formats import parse_timestamp
from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_change_markers.v1"
MAX_MARKERS_PER_LOG = 200

# The change_markers table is part of the workspace schema and is erased with its log by workspace.delete_log.


class Marker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marked_at: str = Field(min_length=10, max_length=40)
    label: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=1000)


def _parse(spec: Any) -> tuple[datetime, str, str]:
    try:
        marker = Marker.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise EventLogWorkspaceError("change marker is invalid: " + (where + " " if where else "") + first["msg"]) from None
    stamp = parse_timestamp(marker.marked_at)
    if stamp is None:
        raise EventLogWorkspaceError("marked_at must be a time with a UTC offset")
    label, note = marker.label.strip(), marker.note.strip()
    if not label:
        raise EventLogWorkspaceError("a change marker needs a label")
    for name, value in (("label", label), ("note", note)):
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
            raise EventLogWorkspaceError(f"{name} must not contain control characters")
    return stamp.astimezone(timezone.utc).replace(tzinfo=None), label, note


def _listing(conn, log_id: str) -> dict[str, Any]:
    rows = conn.execute("""SELECT marker_id, marked_at, label, note, created_at FROM change_markers WHERE log_id = ?
        ORDER BY marked_at, marker_id""", [log_id]).fetchall()
    first, last = conn.execute("SELECT min(ts), max(ts) FROM events WHERE log_id = ?", [log_id]).fetchone()
    return {"format": FORMAT, "log_id": log_id,
            "markers": [{"marker_id": m, "marked_at": at.isoformat() + "Z", "label": label, "note": note,
                         "within_log": bool(first and last and first <= at <= last),
                         "created_at": created.isoformat() + "Z"} for m, at, label, note, created in rows],
            "log_span": {"first_event_at": first.isoformat() + "Z" if first else None,
                         "last_event_at": last.isoformat() + "Z" if last else None},
            "note": "Markers are your notes on the timeline, not events of the log."}


def _with_conn(log_id: str, fn):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            return fn(conn)
        finally:
            conn.close()


def markers(log_id: str) -> dict[str, Any]:
    return _with_conn(log_id, lambda conn: _listing(conn, log_id))


def add(log_id: str, spec: Any) -> dict[str, Any]:
    at, label, note = _parse(spec)

    def run(conn):
        if conn.execute("SELECT count(*) FROM change_markers WHERE log_id = ?", [log_id]).fetchone()[0] >= MAX_MARKERS_PER_LOG:
            raise EventLogWorkspaceError(f"a log keeps at most {MAX_MARKERS_PER_LOG} change markers")
        marker_id = "mark_" + secrets.token_hex(8)
        conn.execute("INSERT INTO change_markers VALUES (?, ?, ?, ?, ?, ?)",
                     [log_id, marker_id, at, label, note, datetime.now(timezone.utc).replace(tzinfo=None)])
        return {**_listing(conn, log_id), "added": marker_id}
    return _with_conn(log_id, run)


def remove(log_id: str, marker_id: str) -> dict[str, Any]:
    def run(conn):
        if not conn.execute("SELECT 1 FROM change_markers WHERE log_id = ? AND marker_id = ?", [log_id, marker_id]).fetchone():
            raise EventLogWorkspaceError("no change marker with this id in this log")
        conn.execute("DELETE FROM change_markers WHERE log_id = ? AND marker_id = ?", [log_id, marker_id])
        return {**_listing(conn, log_id), "removed": marker_id}
    return _with_conn(log_id, run)


def erase_log(log_id: str) -> None:
    """Called by workspace.delete_log after the log's own rows are gone."""
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute("DELETE FROM change_markers WHERE log_id = ?", [log_id])
        finally:
            conn.close()


__all__ = ["markers", "add", "remove", "erase_log", "FORMAT"]
