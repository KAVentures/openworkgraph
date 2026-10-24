"""Bounded, dashboard-only evidence pages for explicitly retained business logs."""
from __future__ import annotations

from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

MAX_PATH = 500
ORDERING = "Timestamp, then event ID and literal event fields for display; equal timestamps do not verify business order."


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise EventLogWorkspaceError("case selectors require nonblank text of at most 200 characters")
    return value


def _integer(value: Any, maximum: int, *, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise EventLogWorkspaceError("case page size or position is outside its supported bounds")
    return value


def _spec(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - keys:
        raise EventLogWorkspaceError("unsupported case query fields")
    return value


def cases(log_id: str, spec: Any, case_type: str | None = None) -> dict[str, Any]:
    spec = _spec(spec, {"cohort", "path", "transition", "after", "limit"})
    cohort = cohorts.parse_cohort(spec.get("cohort"))
    limit = _integer(spec.get("limit", 30), 100)
    after = _text(spec["after"]) if spec.get("after") is not None else None
    path = spec.get("path")
    if path is not None:
        if not isinstance(path, list) or not 0 < len(path) <= MAX_PATH:
            raise EventLogWorkspaceError("an exact case path requires 1 to 500 activity names")
        path = [_text(item) for item in path]
    transition = spec.get("transition")
    if transition is not None:
        if not isinstance(transition, list) or len(transition) != 2:
            raise EventLogWorkspaceError("an adjacent transition requires exactly two activity names")
        transition = [_text(item) for item in transition]

    def run(conn):
        predicate, params = cohorts._predicate(cohort, log_id)
        if transition is not None:
            predicate += """ AND case_id IN (SELECT case_id FROM (
                SELECT case_id, activity, lead(activity) OVER (
                    PARTITION BY case_id ORDER BY ts, event_id) AS next_activity FROM ce
                ) WHERE activity = ? AND next_activity = ?)"""
            params.extend(transition)
        having = " HAVING list(activity ORDER BY ts, event_id) = ?" if path is not None else ""
        if path is not None:
            params.append(path)
        conn.execute(f"""CREATE TEMP TABLE selected_cases AS SELECT case_id, count(*) AS events,
            min(ts) AS started, max(ts) AS ended, count(*) - count(DISTINCT ts) AS timestamp_ties
            FROM ce WHERE {predicate} GROUP BY case_id{having}""", params)
        total = conn.execute("SELECT count(*) FROM selected_cases").fetchone()[0]
        where, args = (" WHERE case_id > ?", [after]) if after is not None else ("", [])
        rows = conn.execute("SELECT * FROM selected_cases" + where + " ORDER BY case_id LIMIT ?", [*args, limit + 1]).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return {
            "selected_cases": total, "cohort": cohorts.describe(cohort), "path": path,
            "transition": transition,
            "cases": [{"case_id": key, "events": n, "started_at": start.isoformat() + "Z",
                       "ended_at": end.isoformat() + "Z", "elapsed_seconds": (end-start).total_seconds(),
                       "timestamp_ties": ties} for key, n, start, end, ties in rows],
            "next_after": rows[-1][0] if more else None, "ordering": ORDERING,
            "case_identity": "literal supplied key within this imported log and object type",
        }
    return workspace._query(log_id, case_type, run)


def events(log_id: str, spec: Any, case_type: str | None = None) -> dict[str, Any]:
    spec = _spec(spec, {"case_id", "offset", "limit"})
    case_id = _text(spec.get("case_id"))
    offset = _integer(spec.get("offset", 0), 10**12, minimum=0)
    limit = _integer(spec.get("limit", 100), 250)

    def run(conn):
        n, unique_times = conn.execute("SELECT count(*), count(DISTINCT ts) FROM ce WHERE case_id = ?", [case_id]).fetchone()
        if not n:
            raise EventLogWorkspaceError("case is not present in the selected log and object type")
        rows = conn.execute("""SELECT event_id, activity, ts, resource FROM ce WHERE case_id = ?
            ORDER BY ts, event_id, activity, resource, attributes LIMIT ? OFFSET ?""", [case_id, limit, offset]).fetchall()
        return {
            "case_id": case_id, "total_events": n, "offset": offset,
            "events": [{"event_id": key, "activity": activity, "timestamp": stamp.isoformat() + "Z",
                        "resource": resource} for key, activity, stamp, resource in rows],
            "next_offset": offset + len(rows) if offset + len(rows) < n else None,
            "timestamp_ties": n - unique_times, "ordering": ORDERING,
            "case_identity": "literal supplied key within this imported log and object type",
        }
    return workspace._query(log_id, case_type, run)
