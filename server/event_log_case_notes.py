"""Case notes: the owner's written notes on individual cases of a saved log.

Investigating a log means looking at single cases: why order 4711 waited three weeks, which customer
complained, what the clerk said on the phone. A note records that finding next to the case, with the
time it was written, so it is still there the next time the case comes up. Notes are the owner's own
text, not events of the log, and never change its data or any analysis. They stay on this computer,
are never shared with AI agents and are deleted with the log.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

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


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_text: str = Field(min_length=1, max_length=2000)
    text: str = Field(min_length=1, max_length=2000)


class NoteConflict(EventLogWorkspaceError):
    """The note was changed after it was loaded for editing."""


class NoteQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str | None = Field(default=None, min_length=1, max_length=500)
    search: str | None = Field(default=None, min_length=1, max_length=200)
    after: str | None = Field(default=None, min_length=1, max_length=80)
    limit: StrictInt = Field(default=30, ge=1, le=MAX_LISTED)


def _ensure(conn) -> None:
    conn.execute(_DDL)


def _listing(conn, log_id: str, case_id: str | None, search: str | None = None,
             after: tuple[datetime, str] | None = None, limit: int = MAX_LISTED) -> dict[str, Any]:
    where, args = ["n.log_id = ?"], [log_id]
    if case_id is not None:
        where.append("n.case_id = ?")
        args.append(case_id)
    if search:
        where.append("(contains(lower(n.text), lower(?)) OR contains(lower(n.case_id), lower(?)))")
        args.extend([search, search])
    scope = " AND ".join(where)
    total, matched_cases = conn.execute(
        f"SELECT count(*), count(DISTINCT n.case_id) FROM case_notes n WHERE {scope}", args).fetchone()
    if after is not None:
        scope += " AND (n.created_at < ? OR (n.created_at = ? AND n.note_id > ?))"
        args.extend([after[0], after[0], after[1]])
    rows = conn.execute(f"""SELECT n.note_id, n.case_id, n.text, n.created_at, o.object_type,
        EXISTS (SELECT 1 FROM event_objects eo WHERE eo.log_id = n.log_id AND eo.object_id = n.case_id)
        FROM case_notes n LEFT JOIN objects o ON o.log_id = n.log_id AND o.object_id = n.case_id
        WHERE {scope} ORDER BY n.created_at DESC, n.note_id LIMIT ?""", [*args, limit + 1]).fetchall()
    cursor = rows[limit - 1] if len(rows) > limit else None
    cases = matched_cases if case_id is None and not search else conn.execute(
        "SELECT count(DISTINCT case_id) FROM case_notes WHERE log_id = ?", [log_id]).fetchone()[0]
    return {"format": FORMAT, "log_id": log_id, **({"case_id": case_id} if case_id is not None else {}),
            "notes": [{"note_id": n, "case_id": c, "text": t, "created_at": at.isoformat() + "Z",
                       "object_type": kind, "has_events": linked} for n, c, t, at, kind, linked in rows[:limit]],
            "notes_total": total, "cases_with_notes": cases, "matching_cases": matched_cases, "listed_limit": limit,
            "next_after": cursor[3].isoformat() + "Z|" + cursor[0] if cursor else None,
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


def notes(log_id: str, case_id: str | None = None, *, search: str | None = None,
          after: str | None = None, limit: int = MAX_LISTED) -> dict[str, Any]:
    cursor = None
    if after is not None:
        try:
            stamp, key = after.split("|")
            stamp_at = datetime.fromisoformat(stamp.removesuffix("Z"))
            if stamp != stamp_at.isoformat() + "Z" or not re.fullmatch(r"note_[0-9a-f]{16}", key):
                raise ValueError
            cursor = (stamp_at, key)
        except (TypeError, ValueError, AttributeError):
            raise EventLogWorkspaceError("invalid case note cursor") from None
    if type(limit) is not int or not 1 <= limit <= MAX_LISTED:
        raise EventLogWorkspaceError(f"limit must be a whole number from 1 to {MAX_LISTED}")
    return _with_conn(log_id, lambda conn: _listing(conn, log_id, case_id, search, cursor, limit))


def query(log_id: str, spec: Any) -> dict[str, Any]:
    try:
        selection = NoteQuery.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise EventLogWorkspaceError("case note search is invalid: " + first["msg"]) from None
    return notes(log_id, **selection.model_dump())


def _validated_text(value: str) -> str:
    text = value.strip()
    if not text:
        raise EventLogWorkspaceError("write something in the note")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
        raise EventLogWorkspaceError("the note must not contain control characters")
    return text


def add(log_id: str, spec: Any, *, compact: bool = False) -> dict[str, Any]:
    try:
        note = NoteSpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise EventLogWorkspaceError("case note is invalid: " + (where + " " if where else "") + first["msg"]) from None
    text = _validated_text(note.text)

    def run(conn):
        if not conn.execute("SELECT 1 FROM objects WHERE log_id = ? AND object_id = ?", [log_id, note.case_id]).fetchone():
            raise EventLogWorkspaceError("no case or object with this ID in this log")
        if conn.execute("SELECT count(*) FROM case_notes WHERE log_id = ?", [log_id]).fetchone()[0] >= MAX_NOTES_PER_LOG:
            raise EventLogWorkspaceError(f"a log keeps at most {MAX_NOTES_PER_LOG} case notes")
        note_id = "note_" + secrets.token_hex(8)
        conn.execute("INSERT INTO case_notes VALUES (?, ?, ?, ?, ?)",
                     [log_id, note_id, note.case_id, text, datetime.now(timezone.utc).replace(tzinfo=None)])
        result = {"added": note_id, "case_id": note.case_id}
        return result if compact else {**_listing(conn, log_id, note.case_id), **result}
    return _with_conn(log_id, run)


def update(log_id: str, note_id: str, spec: Any) -> dict[str, Any]:
    try:
        change = NoteUpdate.model_validate(spec)
    except ValidationError as exc:
        raise EventLogWorkspaceError("case note update is invalid: " + exc.errors()[0]["msg"]) from None
    text = _validated_text(change.text)

    def run(conn):
        row = conn.execute("SELECT case_id, text FROM case_notes WHERE log_id = ? AND note_id = ?",
                           [log_id, note_id]).fetchone()
        if row is None:
            raise EventLogWorkspaceError("no case note with this id in this log")
        if row[1] != change.expected_text:
            raise NoteConflict("This note changed since it was opened. Reload it before saving.")
        conn.execute("UPDATE case_notes SET text = ? WHERE log_id = ? AND note_id = ?", [text, log_id, note_id])
        return {"updated": note_id, "case_id": row[0]}
    return _with_conn(log_id, run)


def remove(log_id: str, note_id: str, *, compact: bool = False) -> dict[str, Any]:
    def run(conn):
        row = conn.execute("SELECT case_id FROM case_notes WHERE log_id = ? AND note_id = ?", [log_id, note_id]).fetchone()
        if not row:
            raise EventLogWorkspaceError("no case note with this id in this log")
        conn.execute("DELETE FROM case_notes WHERE log_id = ? AND note_id = ?", [log_id, note_id])
        result = {"removed": note_id, "removed_from_case": row[0]}
        return result if compact else {**_listing(conn, log_id, None), **result}
    return _with_conn(log_id, run)


def erase(conn, log_id: str) -> None:
    """Called by workspace.delete_log inside its transaction, so notes go exactly when the log goes."""
    _ensure(conn)
    conn.execute("DELETE FROM case_notes WHERE log_id = ?", [log_id])


__all__ = ["notes", "query", "add", "update", "remove", "erase", "FORMAT"]
