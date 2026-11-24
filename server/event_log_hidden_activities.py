"""Hidden activities: leave chosen activities out of every case-centric analysis of a log.

Logs often carry steps that are noise for the question at hand: system heartbeats, "Comment added",
automatic status syncs. Hiding them removes their events from what variants, maps, KPIs, conformance,
predictions and the other case-centric analyses see, the event filter of other process-mining tools.
The stored events are never changed, and showing an activity again restores it. Hidden activities
are matched on the stored activity names, before any activity mapping is applied. Every result
computed with hidden activities says so in ``hidden_activities``. Saved signal rules and
object-centric views keep every event, as with activity mappings. Deleting the log deletes the list.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_hidden_activities.v1"
MAX_HIDDEN = 500
MAX_LISTED = 2000
Label = Annotated[str, Field(min_length=1, max_length=200)]

_DDL = """CREATE TABLE IF NOT EXISTS hidden_activities (
  log_id VARCHAR NOT NULL, activity VARCHAR NOT NULL, hidden_at TIMESTAMP NOT NULL)"""


class HiddenSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activities: list[Label] = Field(max_length=MAX_HIDDEN)


def _ensure(conn) -> None:
    conn.execute(_DDL)


def _status(conn, log_id: str) -> dict[str, Any]:
    _ensure(conn)
    hidden = dict(conn.execute("SELECT activity, hidden_at FROM hidden_activities WHERE log_id = ?", [log_id]).fetchall())
    counts = conn.execute("""SELECT activity, count(*) FROM events WHERE log_id = ? GROUP BY 1
        ORDER BY 2 DESC, 1 LIMIT ?""", [log_id, MAX_LISTED]).fetchall()
    total = conn.execute("SELECT count(*) FROM events WHERE log_id = ?", [log_id]).fetchone()[0]
    in_log = dict(conn.execute("""SELECT activity, count(*) FROM events WHERE log_id = ? AND activity IN
        (SELECT activity FROM hidden_activities WHERE log_id = ?) GROUP BY 1""", [log_id, log_id]).fetchall())
    return {"format": FORMAT, "log_id": log_id,
            "hidden": [{"activity": a, "events": in_log.get(a, 0), "hidden_at": at.isoformat() + "Z"}
                       for a, at in sorted(hidden.items())],
            "activities": [{"activity": a, "events": n, "hidden": a in hidden} for a, n in counts],
            "activities_listed_limit": MAX_LISTED,
            "events_total": total, "events_hidden": sum(in_log.values()),
            "note": "Hidden activities are left out of case-centric analyses; the stored events are unchanged."}


def _with_conn(log_id: str, fn):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            return fn(conn)
        finally:
            conn.close()


def hidden(log_id: str) -> dict[str, Any]:
    return _with_conn(log_id, lambda conn: _status(conn, log_id))


def set_hidden(log_id: str, spec: Any) -> dict[str, Any]:
    """Replace the log's hidden activities with the given list (an empty list shows everything)."""
    try:
        names = HiddenSpec.model_validate(spec).activities
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise EventLogWorkspaceError("hidden activities are invalid: " + (where + " " if where else "") + first["msg"]) from None
    if len(set(names)) != len(names):
        raise EventLogWorkspaceError("list each activity only once")

    def run(conn):
        _ensure(conn)
        known = {a for (a,) in conn.execute("SELECT DISTINCT activity FROM events WHERE log_id = ? AND list_contains(?, activity)",
                                            [log_id, names]).fetchall()} if names else set()
        unknown = [n for n in names if n not in known]
        if unknown:
            raise EventLogWorkspaceError(f"not an activity of this log: {unknown[0]}")
        left = conn.execute("SELECT count(*) FROM events WHERE log_id = ? AND NOT list_contains(?, activity)",
                            [log_id, names]).fetchone()[0]
        if names and not left:
            raise EventLogWorkspaceError("keep at least one activity visible")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        kept = dict(conn.execute("SELECT activity, hidden_at FROM hidden_activities WHERE log_id = ?", [log_id]).fetchall())
        conn.execute("BEGIN")
        try:
            conn.execute("DELETE FROM hidden_activities WHERE log_id = ?", [log_id])
            if names:
                conn.executemany("INSERT INTO hidden_activities VALUES (?, ?, ?)",
                                 [[log_id, n, kept.get(n, now)] for n in names])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return _status(conn, log_id)
    return _with_conn(log_id, run)


def hidden_names(conn, log_id: str) -> list[str]:
    _ensure(conn)
    return [name for (name,) in conn.execute(
        "SELECT activity FROM hidden_activities WHERE log_id = ? ORDER BY 1", [log_id]).fetchall()]


def apply_to_case_events(conn, log_id: str) -> dict[str, Any] | None:
    """Remove hidden activities from the request's ``ce`` table, if any; describe what was done."""
    names = hidden_names(conn, log_id)
    if not names:
        return None
    events, cases = conn.execute("SELECT count(*), count(DISTINCT case_id) FROM ce").fetchone()
    conn.execute("DELETE FROM ce WHERE list_contains(?, activity)", [names])
    events_after, cases_after = conn.execute("SELECT count(*), count(DISTINCT case_id) FROM ce").fetchone()
    return {"activities": names, "events_hidden": events - events_after,
            "cases_with_only_hidden_events": cases - cases_after,
            "note": "These activities are left out of this result; the stored events are unchanged."}


def erase(conn, log_id: str) -> None:
    """Called inside workspace.delete_log's transaction, so the list goes exactly when the log goes."""
    _ensure(conn)
    conn.execute("DELETE FROM hidden_activities WHERE log_id = ?", [log_id])


__all__ = ["hidden", "set_hidden", "apply_to_case_events", "erase", "FORMAT", "MAX_HIDDEN"]
