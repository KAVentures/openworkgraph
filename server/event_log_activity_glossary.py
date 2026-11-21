"""The owner's activity glossary: what each activity of a saved event log means.

Activity names in an export are terse ("ZF02 posted", "Chk 2"). Process mining tools keep a
knowledge model next to the data so that a map can be read by someone who does not know the
source system. This module stores, per activity of a log:

* ``description``: what happens in the step, in the owner's words;
* ``owner_team``: the team or role that usually performs it;
* ``system``: the application where it happens;
* ``automation``: ``manual``, ``partly``, ``automated`` or ``candidate`` (worth automating), or none.

The glossary is the owner's own documentation, not something derived from the log, and the log's
events are never changed. Notes stay when an activity disappears from the log (for example after a
new import); the listing marks them ``in_log: false``. Deleting the log deletes its glossary.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_activity_glossary.v1"
MAX_NOTES_PER_LOG = 2000
AUTOMATION = ("manual", "partly", "automated", "candidate")
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2000)
    owner_team: str = Field(default="", max_length=200)
    system: str = Field(default="", max_length=200)
    automation: Literal["manual", "partly", "automated", "candidate"] | None = None


def _parse(spec: Any) -> Note:
    try:
        note = Note.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise EventLogWorkspaceError("glossary entry is invalid: " + (where + " " if where else "") + first["msg"]) from None
    for name in ("description", "owner_team", "system"):
        value = getattr(note, name)
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
            raise EventLogWorkspaceError(f"{name} must not contain control characters")
    return note


def _rows(conn, log_id: str) -> list[dict[str, Any]]:
    counts = conn.execute("SELECT activity, count(*) FROM events WHERE log_id = ? GROUP BY activity", [log_id]).fetchall()
    notes = {a: (d, t, s, au, at) for a, d, t, s, au, at in conn.execute(
        """SELECT activity, description, owner_team, system, automation, updated_at FROM activity_notes
           WHERE log_id = ?""", [log_id]).fetchall()}
    stats = dict(counts)
    out = []
    for activity in sorted(set(stats) | set(notes), key=lambda a: (-stats.get(a, 0), a)):
        events = stats.get(activity, 0)
        d, t, s, au, at = notes.get(activity, ("", "", "", None, None))
        out.append({"activity": activity, "in_log": activity in stats, "events": events,
                    "described": activity in notes, "description": d, "owner_team": t, "system": s,
                    "automation": au, "updated_at": at.isoformat() + "Z" if at else None})
    return out


def _listing(conn, log_id: str) -> dict[str, Any]:
    rows = _rows(conn, log_id)
    in_log = [r for r in rows if r["in_log"]]
    described = [r for r in in_log if r["described"]]
    return {"format": FORMAT, "log_id": log_id, "activities": rows,
            "coverage": {"activities_in_log": len(in_log), "described": len(described),
                         "events_described_share": round(sum(r["events"] for r in described) /
                                                         max(1, sum(r["events"] for r in in_log)), 4),
                         "notes_not_in_log": sum(1 for r in rows if r["described"] and not r["in_log"])},
            "automation": {k: sum(1 for r in in_log if r["automation"] == k) for k in AUTOMATION},
            "caveat": "The glossary is the owner's documentation of the activities, not something derived from the log."}


def _with_conn(log_id: str, fn):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            return fn(conn)
        finally:
            conn.close()


def glossary(log_id: str) -> dict[str, Any]:
    return _with_conn(log_id, lambda conn: _listing(conn, log_id))


def set_note(log_id: str, spec: Any) -> dict[str, Any]:
    note = _parse(spec)
    fields = (note.description.strip(), note.owner_team.strip(), note.system.strip())
    empty = not any(fields) and note.automation is None

    def run(conn):
        known = conn.execute("SELECT 1 FROM events WHERE log_id = ? AND activity = ? LIMIT 1",
                             [log_id, note.activity]).fetchone()
        exists = conn.execute("SELECT 1 FROM activity_notes WHERE log_id = ? AND activity = ?",
                              [log_id, note.activity]).fetchone()
        if not known and not exists:
            raise EventLogWorkspaceError("this activity is not in the log")
        if not exists and not empty:
            total = conn.execute("SELECT count(*) FROM activity_notes WHERE log_id = ?", [log_id]).fetchone()[0]
            if total >= MAX_NOTES_PER_LOG:
                raise EventLogWorkspaceError(f"a log keeps at most {MAX_NOTES_PER_LOG} glossary entries")
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM activity_notes WHERE log_id = ? AND activity = ?", [log_id, note.activity])
            if not empty:
                conn.execute("INSERT INTO activity_notes VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [log_id, note.activity, *fields, note.automation,
                              datetime.now(timezone.utc).replace(tzinfo=None)])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {**_listing(conn, log_id), "changed": note.activity, "removed": empty}
    return _with_conn(log_id, run)


def delete_note(log_id: str, activity: Any) -> dict[str, Any]:
    if not isinstance(activity, str) or not 1 <= len(activity) <= 500:
        raise EventLogWorkspaceError("activity must be a name")

    def run(conn):
        if not conn.execute("SELECT 1 FROM activity_notes WHERE log_id = ? AND activity = ?", [log_id, activity]).fetchone():
            raise EventLogWorkspaceError("this activity has no glossary entry")
        conn.execute("DELETE FROM activity_notes WHERE log_id = ? AND activity = ?", [log_id, activity])
        return {**_listing(conn, log_id), "changed": activity, "removed": True}
    return _with_conn(log_id, run)


def _cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA):
        return "'" + value
    return value


def glossary_csv(log_id: str) -> str:
    rows = glossary(log_id)["activities"]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["activity", "in_log", "events", "description", "owner_team", "system", "automation"])
    for r in rows:
        writer.writerow([_cell(r["activity"]), "yes" if r["in_log"] else "no", r["events"],
                         _cell(r["description"]), _cell(r["owner_team"]), _cell(r["system"]), r["automation"] or ""])
    return out.getvalue()


__all__ = ["glossary", "set_note", "delete_note", "glossary_csv", "FORMAT", "AUTOMATION"]
