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
import hashlib
import json
import io
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_activity_glossary.v1"
MAX_NOTES_PER_LOG = 2000
AUTOMATION = ("manual", "partly", "automated", "candidate")
# A spreadsheet may evaluate a cell as a formula after leading spaces, line breaks or a byte-order mark.
_FORMULA_PREFIX = re.compile(r"^[\s\ufeff]*[=+\-@]")


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


def _guarded(text: str) -> bool:
    return text.startswith(("\t", "\r", "\n")) or bool(_FORMULA_PREFIX.match(text))


def _cell(value: Any) -> Any:
    if isinstance(value, str) and _guarded(value):
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


MAX_IMPORT_BYTES = 1_000_000
_AUTOMATION_WORDS = {"manual": "manual", "partly": "partly", "partly automated": "partly", "automated": "automated",
                     "candidate": "candidate", "automation candidate": "candidate", "": None}


def _uncell(value: str) -> str:
    """Undo the formula guard of glossary_csv, so a downloaded glossary imports unchanged."""
    if len(value) > 1 and value[0] == "'" and _guarded(value[1:]):
        return value[1:]
    return value


def import_csv(log_id: str, text: Any, *, preview: bool = False, expected_fingerprint: str | None = None) -> dict[str, Any]:
    """Fill the glossary from CSV with an ``activity`` column and any of description, owner_team, system and
    automation. Rows for activities not in the log are skipped and listed. A downloaded glossary imports as is;
    columns it does not know (in_log, events) are ignored. Imported values replace the activity's entry."""
    if not isinstance(text, str) or not text.strip():
        raise EventLogWorkspaceError("send the glossary as CSV text")
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise EventLogWorkspaceError("the glossary CSV is larger than 1 MB")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    if "activity" not in header:
        raise EventLogWorkspaceError("the CSV needs an activity column")
    reader.fieldnames = header
    notes: dict[str, Note] = {}
    for number, row in enumerate(reader, start=2):
        if len(notes) >= MAX_NOTES_PER_LOG:
            raise EventLogWorkspaceError(f"a glossary has at most {MAX_NOTES_PER_LOG} entries")
        activity = _uncell((row.get("activity") or "").strip())
        if not activity:
            continue
        word = (row.get("automation") or "").strip().lower()
        if word not in _AUTOMATION_WORDS:
            raise EventLogWorkspaceError(f"line {number}: automation must be manual, partly, automated or candidate")
        spec = {"activity": activity, "automation": _AUTOMATION_WORDS[word]}
        for name in ("description", "owner_team", "system"):
            spec[name] = _uncell((row.get(name) or "").strip())
        try:
            notes[activity] = _parse(spec)
        except EventLogWorkspaceError as exc:
            raise EventLogWorkspaceError(f"line {number}: {exc}") from None

    def run(conn):
        known = {a for (a,) in conn.execute("SELECT DISTINCT activity FROM events WHERE log_id = ?", [log_id]).fetchall()}
        chosen = [n for a, n in notes.items() if a in known]
        unknown = sorted(a for a in notes if a not in known)
        existing = {a for (a,) in conn.execute("SELECT activity FROM activity_notes WHERE log_id = ?", [log_id]).fetchall()}
        if len(existing | {n.activity for n in chosen}) > MAX_NOTES_PER_LOG:
            raise EventLogWorkspaceError(f"a log keeps at most {MAX_NOTES_PER_LOG} glossary entries")
        previous = {row["activity"]: row for row in _rows(conn, log_id)}
        changes = []
        for note in chosen:
            old = previous[note.activity]
            values = {"description": note.description.strip(), "owner_team": note.owner_team.strip(),
                      "system": note.system.strip(), "automation": note.automation}
            before = {key: old[key] for key in values}
            empty = not any(values.values())
            action = ("remove" if old["described"] and empty else
                      "add" if not old["described"] and not empty else
                      "unchanged" if before == values else "update")
            changes.append({"activity": note.activity, "action": action, "before": before, "after": values})
        fingerprint = hashlib.sha256(json.dumps({
            "log_id": log_id, "file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "changes": changes, "unknown": unknown,
        }, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        if preview:
            return {"format": "openworkgraph.glossary-import-preview.v1", "fingerprint": fingerprint,
                    "changes": changes, "skipped_not_in_log": unknown[:50],
                    "skipped_not_in_log_total": len(unknown)}
        if expected_fingerprint is not None and expected_fingerprint != fingerprint:
            raise EventLogWorkspaceError("The glossary or file changed. Preview the import again.")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute("BEGIN")
        try:
            for n in chosen:
                conn.execute("DELETE FROM activity_notes WHERE log_id = ? AND activity = ?", [log_id, n.activity])
                fields = (n.description.strip(), n.owner_team.strip(), n.system.strip())
                if any(fields) or n.automation is not None:
                    conn.execute("INSERT INTO activity_notes VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 [log_id, n.activity, *fields, n.automation, now])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {**_listing(conn, log_id), "imported": len(chosen), "skipped_not_in_log": unknown[:50],
                "skipped_not_in_log_total": len(unknown)}
    return _with_conn(log_id, run)


__all__ = ["glossary", "set_note", "delete_note", "glossary_csv", "import_csv", "FORMAT", "AUTOMATION"]
