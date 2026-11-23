"""Bounded, dashboard-only evidence pages for explicitly retained business logs."""
from __future__ import annotations

import re
from typing import Any

from . import event_log_cohorts as cohorts
from .event_log_variant_coverage import VARIANT_KEY_SQL
from .event_log_rework import MAX_PATTERN_LENGTH
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


def _activity_selection(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or not 1 <= len(values) <= 10:
        raise EventLogWorkspaceError("choose from 1 to 10 activity labels")
    return list(dict.fromkeys(_text(value) for value in values))


def cases(log_id: str, spec: Any, case_type: str | None = None) -> dict[str, Any]:
    variant_id = spec.get("variant_id") if isinstance(spec, dict) else None
    if variant_id is not None and (not isinstance(variant_id, str) or not re.fullmatch(r"[a-f0-9]{64}", variant_id)):
        raise EventLogWorkspaceError("invalid exact variant identifier")
    spec = _spec(spec, {"cohort", "path", "transition", "after", "limit", "variant_id", "required_activities", "excluded_activities", "repeat_activity", "loop_pattern"})
    cohort = cohorts.parse_cohort(spec.get("cohort"))
    limit = _integer(spec.get("limit", 30), 100)
    after = _text(spec["after"]) if spec.get("after") is not None else None
    required = _activity_selection(spec.get("required_activities"))
    excluded = _activity_selection(spec.get("excluded_activities"))
    if set(required).intersection(excluded):
        raise EventLogWorkspaceError("an activity cannot be both required and excluded")
    path = spec.get("path")
    if path is not None:
        if not isinstance(path, list) or not 0 < len(path) <= MAX_PATH:
            raise EventLogWorkspaceError("an exact case path requires 1 to 500 activity names")
        path = [_text(item) for item in path]
        if variant_id is not None:
            raise EventLogWorkspaceError("choose either a path or a variant identifier")
    transition = spec.get("transition")
    if transition is not None:
        if not isinstance(transition, list) or len(transition) != 2:
            raise EventLogWorkspaceError("an adjacent transition requires exactly two activity names")
        transition = [_text(item) for item in transition]
    repeat = _text(spec["repeat_activity"]) if spec.get("repeat_activity") is not None else None
    loop = spec.get("loop_pattern")
    if loop is not None:
        if not isinstance(loop, list) or not 2 <= len(loop) <= MAX_PATTERN_LENGTH:
            raise EventLogWorkspaceError(f"a rework loop requires 2 to {MAX_PATTERN_LENGTH} activity names")
        loop = [_text(item) for item in loop]
        if repeat is not None or loop[0] != loop[-1] or loop[0] in loop[1:-1]:
            raise EventLogWorkspaceError("a loop selects consecutive occurrences of one activity without a separate repeat selector")

    def run(conn):
        predicate, params = cohorts._predicate(cohort, log_id)
        if required:
            predicate += """ AND case_id IN (SELECT case_id FROM ce WHERE activity IN (SELECT unnest(?))
                GROUP BY case_id HAVING count(DISTINCT activity) = ?)"""
            params.extend([required, len(required)])
        if excluded:
            predicate += " AND case_id NOT IN (SELECT case_id FROM ce WHERE activity IN (SELECT unnest(?)))"
            params.append(excluded)
        if repeat is not None:
            predicate += " AND case_id IN (SELECT case_id FROM ce WHERE activity = ? GROUP BY case_id HAVING count(*) > 1)"
            params.append(repeat)
        if loop is not None:
            following = len(loop) - 1
            predicate += f""" AND case_id IN (SELECT case_id FROM (
                SELECT case_id, list(activity) OVER (PARTITION BY case_id ORDER BY ts, event_id
                    ROWS BETWEEN CURRENT ROW AND {following} FOLLOWING) AS steps FROM ce
                ) WHERE steps = ?)"""
            params.append(loop)
        if transition is not None:
            predicate += """ AND case_id IN (SELECT case_id FROM (
                SELECT case_id, activity, lead(activity) OVER (
                    PARTITION BY case_id ORDER BY ts, event_id) AS next_activity FROM ce
                ) WHERE activity = ? AND next_activity = ?)"""
            params.extend(transition)
        sequence = "list(activity ORDER BY ts, event_id)"
        shape = f", {sequence} AS path" if variant_id is not None else ""
        having = ""
        if path is not None:
            having = f" HAVING {sequence} = ?"
            params.append(path)
        if variant_id is not None:
            having = f" HAVING {VARIANT_KEY_SQL.format(path=sequence)} = ?"
            params.append(variant_id)
        conn.execute(f"""CREATE TEMP TABLE selected_cases AS SELECT case_id, count(*) AS events,
            min(ts) AS started, max(ts) AS ended, count(*) - count(DISTINCT ts) AS timestamp_ties{shape}
            FROM ce WHERE {predicate} GROUP BY case_id{having}""", params)
        if variant_id is not None and conn.execute("SELECT count(DISTINCT path) FROM selected_cases").fetchone()[0] > 1:
            raise EventLogWorkspaceError("this identifier matches multiple paths")
        total = conn.execute("SELECT count(*) FROM selected_cases").fetchone()[0]
        where, args = (" WHERE case_id > ?", [after]) if after is not None else ("", [])
        rows = conn.execute("SELECT case_id, events, started, ended, timestamp_ties FROM selected_cases" +
                            where + " ORDER BY case_id LIMIT ?", [*args, limit + 1]).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return {
            "selected_cases": total, "cohort": cohorts.describe(cohort), "path": path,
            "transition": transition,
            **({"required_activities": required} if required else {}),
            **({"excluded_activities": excluded} if excluded else {}),
            **({"repeat_activity": repeat} if repeat is not None else {}),
            **({"loop_pattern": loop} if loop is not None else {}),
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
