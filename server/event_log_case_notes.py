"""Case notes: the owner's written notes on individual cases of a saved log.

Investigating a log means looking at single cases: why order 4711 waited three weeks, which customer
complained, what the clerk said on the phone. A note records that finding next to the case, with the
time it was written, so it is still there the next time the case comes up. Notes are the owner's own
text, not events of the log, and never change its data or any analysis. They stay on this computer,
are never shared with AI agents and are deleted with the log.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_case_notes.v1"
MAX_NOTES_PER_LOG = 5000
MAX_LISTED = 500

_DDL = """CREATE TABLE IF NOT EXISTS case_notes (
  log_id VARCHAR NOT NULL, note_id VARCHAR NOT NULL, case_id VARCHAR NOT NULL, text VARCHAR NOT NULL,
  created_at TIMESTAMP NOT NULL)"""


class NoteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=2000)


def _ensure(conn) -> None:
    conn.execute(_DDL)


def _listing(conn, log_id: str, case_id: str | None) -> dict[str, Any]:
    where, args = ("AND case_id = ?", [log_id, case_id]) if case_id is not None else ("", [log_id])
    total = conn.execute(f"SELECT count(*) FROM case_notes WHERE log_id = ? {where}", args).fetchone()[0]
    rows = conn.execute(f"""SELECT note_id, case_id, text, created_at FROM case_notes WHERE log_id = ? {where}
        ORDER BY created_at DESC, note_id LIMIT ?""", [*args, MAX_LISTED]).fetchall()
    cases = conn.execute("SELECT count(DISTINCT case_id) FROM case_notes WHERE log_id = ?", [log_id]).fetchone()[0]
    return {"format": FORMAT, "log_id": log_id, **({"case_id": case_id} if case_id is not None else {}),
            "notes": [{"note_id": n, "case_id": c, "text": t, "created_at": at.isoformat() + "Z"} for n, c, t, at in rows],
            "notes_total": total, "cases_with_notes": cases, "listed_limit": MAX_LISTED,
            "note": "Your notes stay on this computer and are never shared with AI agents."}


def _with_conn(log_id: str, fn):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            _ensure(conn)
            return fn(conn)
        finally:
            conn.close()


def notes(log_id: str, case_id: str | None = None) -> dict[str, Any]:
    return _with_conn(log_id, lambda conn: _listing(conn, log_id, case_id))


def add(log_id: str, spec: Any) -> dict[str, Any]:
    try:
        note = NoteSpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise EventLogWorkspaceError("case note is invalid: " + (where + " " if where else "") + first["msg"]) from None
    text = note.text.strip()
    if not text:
        raise EventLogWorkspaceError("write something in the note")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
        raise EventLogWorkspaceError("the note must not contain control characters")

    def run(conn):
        if not conn.execute("SELECT 1 FROM objects WHERE log_id = ? AND object_id = ?", [log_id, note.case_id]).fetchone():
            raise EventLogWorkspaceError("no case or object with this ID in this log")
        if conn.execute("SELECT count(*) FROM case_notes WHERE log_id = ?", [log_id]).fetchone()[0] >= MAX_NOTES_PER_LOG:
            raise EventLogWorkspaceError(f"a log keeps at most {MAX_NOTES_PER_LOG} case notes")
        note_id = "note_" + secrets.token_hex(8)
        conn.execute("INSERT INTO case_notes VALUES (?, ?, ?, ?, ?)",
                     [log_id, note_id, note.case_id, text, datetime.now(timezone.utc).replace(tzinfo=None)])
        return {**_listing(conn, log_id, note.case_id), "added": note_id}
    return _with_conn(log_id, run)


def remove(log_id: str, note_id: str) -> dict[str, Any]:
    def run(conn):
        row = conn.execute("SELECT case_id FROM case_notes WHERE log_id = ? AND note_id = ?", [log_id, note_id]).fetchone()
        if not row:
            raise EventLogWorkspaceError("no case note with this id in this log")
        conn.execute("DELETE FROM case_notes WHERE log_id = ? AND note_id = ?", [log_id, note_id])
        return {**_listing(conn, log_id, None), "removed": note_id, "removed_from_case": row[0]}
    return _with_conn(log_id, run)


def erase(conn, log_id: str) -> None:
    """Called by workspace.delete_log inside its transaction, so notes go exactly when the log goes."""
    _ensure(conn)
    conn.execute("DELETE FROM case_notes WHERE log_id = ?", [log_id])


__all__ = ["notes", "add", "remove", "erase", "FORMAT"]
