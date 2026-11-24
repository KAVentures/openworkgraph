"""Due dates: did the step that matters happen by the date it was due?

Due-date analysis (UiPath Process Mining's due dates, Celonis's on-time delivery and payment views)
compares when a chosen activity happened in each case (for example *Pay invoice* or *Ship order*)
with the date it was due. The due date is either an attribute of the case (a case attribute, or the
first value of an event attribute in the case, such as a payment due date column), or a fixed number
of days after the case started.

Each case lands in one group:

* **on time**: the activity happened by the due date (and, with an early window, not too early);
* **early**: more than the early window before the due date (only when a window is given);
* **late**: it happened after the due date, with how many days late;
* **open, overdue**: it has not happened and the due date is before the end of the log;
* **open, not yet due**: it has not happened and the due date is still ahead;
* **no due date** / **unreadable due date**: counted, never guessed.

A due date given as a date without a time counts as due by the end of that day (UTC). The report
says how often deadlines were met, not why; a case may have been late for reasons outside the process.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from heapq import nsmallest
from typing import Any

from shared.event_log_formats import parse_timestamp

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_due_dates.v1"
MAX_DAYS = 3650
MAX_LATE_CASES = 50
MAX_EVIDENCE_PAGE = 100
GROUPS = ("on_time", "early", "late", "open_overdue", "open_not_due", "no_due_date", "unreadable_due_date")


def _parse_due(raw: Any) -> datetime | None:
    text = str(raw).strip()
    if not text:
        return None
    try:
        if len(text) == 10:   # YYYY-MM-DD: due by the end of that day
            return datetime.fromisoformat(text) + timedelta(days=1)
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        return None
    return value


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p90": None, "max": None}
    ordered = sorted(values)

    def q(p: float) -> float:
        pos = (len(ordered) - 1) * p
        lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
        return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo), 2)
    return {"median": q(0.5), "p90": q(0.9), "max": round(ordered[-1], 2)}


def collect(conn, log_id: str, activity: str, due_attribute: str | None, table: str = "ce", *,
            use_case_attributes: bool = True) -> dict[str, Any]:
    """Per case: start, first occurrence of ``activity``, and the raw due-date value, from ``table``."""
    if table not in ("ce", "rc"):
        raise ValueError("unknown case-event table")
    cases = conn.execute(f"""SELECT case_id, min(ts), min(ts) FILTER (WHERE activity = ?) FROM {table}
        GROUP BY case_id""", [activity]).fetchall()
    log_end = conn.execute(f"SELECT max(ts) FROM {table}").fetchone()[0]
    has_activity = conn.execute(f"SELECT count(*) FROM {table} WHERE activity = ?", [activity]).fetchone()[0]
    raw: dict[str, Any] = {}
    if due_attribute is not None:
        path = '$."' + due_attribute.replace('"', '\\"') + '"'
        for case_id, value in conn.execute(f"""SELECT case_id, arg_min(v, (ts, event_id)) FROM (
              SELECT case_id, ts, event_id, json_extract_string(attributes, ?) AS v FROM {table})
            WHERE v IS NOT NULL AND trim(v) <> '' GROUP BY case_id""", [path]).fetchall():
            raw[case_id] = value
        if use_case_attributes:
            for object_id, attributes in conn.execute(f"""SELECT object_id, attributes FROM objects
                WHERE log_id = ? AND object_id IN (SELECT DISTINCT case_id FROM {table})""", [log_id]).fetchall():
                value = json.loads(attributes or "{}").get(due_attribute)
                if value not in (None, ""):
                    raw[object_id] = value    # a case attribute wins over an event attribute
    return {"cases_rows": cases, "log_end": log_end, "has_activity": has_activity, "raw_due": raw}


def _classifications(rows, raw_due, log_end, due_days, early_days):
    for case_id, started, done in rows:
        if due_days is not None:
            due = started + timedelta(days=due_days)
        elif case_id not in raw_due:
            yield case_id, "no_due_date", done, None, None
            continue
        else:
            due = _parse_due(raw_due[case_id])
            if due is None:
                yield case_id, "unreadable_due_date", done, None, None
                continue
        difference = (done - due).total_seconds() / 86400 if done is not None else None
        if done is None:
            group = "open_overdue" if log_end is not None and due < log_end else "open_not_due"
        elif difference > 0:
            group = "late"
        elif early_days is not None and difference < -early_days:
            group = "early"
        else:
            group = "on_time"
        yield case_id, group, done, due, difference


def classify(rows, raw_due: dict[str, Any], log_end, *, due_days: float | None, early_days: float | None):
    """Group cases and collect the statistics used by summaries and signals."""
    groups: Counter = Counter()
    days_late: list[float] = []
    days_early: list[float] = []
    months: dict[str, Counter] = {}
    late_cases = []
    for case_id, group, done, due, difference in _classifications(rows, raw_due, log_end, due_days, early_days):
        groups[group] += 1
        if due is not None:
            months.setdefault(due.strftime("%Y-%m"), Counter())[group] += 1
        if group == "late":
            days_late.append(difference)
            late_cases.append((difference, case_id, done, due))
        elif group == "early":
            days_early.append(-difference)
    return groups, days_late, days_early, months, late_cases


def _validated_data(log_id, activity, case_type, due_attribute, due_days, early_days, cohort, as_of):
    if not isinstance(activity, str) or not activity or len(activity) > 500:
        raise EventLogWorkspaceError("choose the activity that has a due date")
    if (due_attribute is None) == (due_days is None):
        raise EventLogWorkspaceError("give either a due-date attribute or a number of days after the case starts")
    if due_attribute is not None and (not isinstance(due_attribute, str) or not due_attribute or len(due_attribute) > 200):
        raise EventLogWorkspaceError("the due-date attribute must be a name")
    for name, value in (("due_days", due_days), ("early_days", early_days)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not 0 < value <= MAX_DAYS):
            raise EventLogWorkspaceError(f"{name} must be a number of days from above 0 to {MAX_DAYS}")

    selected = cohorts.parse_cohort(cohort)
    cutoff = None
    if as_of is not None:
        try:
            stamp = parse_timestamp(as_of) if isinstance(as_of, str) and len(as_of) <= 64 else None
        except OverflowError:
            stamp = None
        if stamp is None:
            raise EventLogWorkspaceError("as_of must be a timezone-qualified ISO time")
        cutoff = stamp.astimezone(timezone.utc).replace(tzinfo=None)

    def run(conn):
        if not selected and cutoff is None:
            return collect(conn, log_id, activity, due_attribute)
        log_end, has_activity = conn.execute(
            "SELECT max(ts), count(*) FILTER (WHERE activity = ?) FROM ce", [activity]).fetchone()
        if cutoff is not None:
            conn.execute("DELETE FROM ce WHERE ts > ?", [cutoff])
        cohorts.restrict(conn, selected, log_id)
        data = collect(conn, log_id, activity, due_attribute, use_case_attributes=cutoff is None)
        return {**data, "log_end": log_end, "has_activity": has_activity}

    data = workspace._query(log_id, case_type, run)
    if not data.pop("has_activity"):
        raise EventLogWorkspaceError(f"no case in this case notion has the activity {activity!r}")
    return data, cutoff


def due_dates(log_id: str, activity: str, case_type: str | None = None, *, due_attribute: str | None = None,
              due_days: float | None = None, early_days: float | None = None, include_case_ids: bool = True,
              cohort: dict[str, Any] | None = None, as_of: str | None = None) -> dict[str, Any]:
    data, cutoff = _validated_data(log_id, activity, case_type, due_attribute, due_days, early_days, cohort, as_of)
    rows, log_end, raw_due = data.pop("cases_rows"), data.pop("log_end"), data.pop("raw_due")
    groups, days_late, days_early, months, late_cases = classify(rows, raw_due, cutoff or log_end, due_days=due_days,
                                                                  early_days=early_days)
    evaluated = sum(groups[g] for g in ("on_time", "early", "late", "open_overdue", "open_not_due"))
    decided = groups["on_time"] + groups["early"] + groups["late"]
    late_cases.sort(key=lambda c: (-c[0], str(c[1])))

    def share(n: int, of: int) -> float | None:
        return round(n / of, 4) if of else None

    by_month = []
    for month, counts in sorted(months.items()):
        done_n = counts["on_time"] + counts["early"] + counts["late"]
        by_month.append({"due_month": month, "due": sum(counts.values()), "on_time": counts["on_time"],
                         "late": counts["late"], "early": counts["early"],
                         "open_overdue": counts["open_overdue"], "open_not_due": counts["open_not_due"],
                         "on_time_share_of_done": share(counts["on_time"], done_n)})
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "activity": activity, "due": ({"attribute": due_attribute} if due_attribute is not None
                                      else {"days_after_start": due_days}),
        "early_days": early_days,
        "cases": len(rows), "evaluated": evaluated,
        "groups": {g: groups[g] for g in GROUPS},
        "on_time_share_of_done": share(groups["on_time"], decided),
        "late_share_of_done": share(groups["late"], decided),
        "days_late": _quantiles(days_late), "days_early": _quantiles(days_early),
        "by_due_month": by_month[-120:],
        "most_late_cases": ([{"case_id": c, "days_late": round(d, 2), "done_at": done.isoformat() + "Z",
                                "due_at": due.isoformat() + "Z"} for d, c, done, due in late_cases[:MAX_LATE_CASES]]
                              if include_case_ids else None),
        "log_end": log_end.isoformat() + "Z" if log_end else None,
        **({"as_of": cutoff.isoformat() + "Z"} if cutoff is not None else {}),
        **({"deadline_attribute_basis": "events_at_or_before_cutoff"} if cutoff is not None and due_attribute is not None else {}),
        "method": ("the first occurrence of the activity in each case is compared with the case's due date; a date "
                   "without a time is due by the end of that day (UTC); open cases are overdue when the due date is "
                   + ("before the selected cutoff" if cutoff is not None else "before the log's last event")),
        "caveat": ("Due-date adherence says how often deadlines were met, not why. Cases without a readable due "
                   "date are counted separately, never guessed."),
    }


def due_cases(log_id: str, activity: str, case_type: str | None = None, *, group: str,
              after: str | None = None, limit: int = 50, due_attribute: str | None = None,
              due_days: float | None = None, early_days: float | None = None,
              cohort: dict[str, Any] | None = None, as_of: str | None = None) -> dict[str, Any]:
    if not isinstance(group, str) or group not in GROUPS:
        raise EventLogWorkspaceError("unknown due-date group")
    if after is not None and (not isinstance(after, str) or not after or len(after) > 200):
        raise EventLogWorkspaceError("invalid case key cursor")
    if type(limit) is not int or not 1 <= limit <= MAX_EVIDENCE_PAGE:
        raise EventLogWorkspaceError("limit must be a whole number from 1 to 100")
    data, cutoff = _validated_data(log_id, activity, case_type, due_attribute, due_days, early_days, cohort, as_of)
    total = 0

    def selected():
        nonlocal total
        for key, category, done, due, difference in _classifications(
                data["cases_rows"], data["raw_due"], cutoff or data["log_end"], due_days, early_days):
            if category == group:
                total += 1
                if after is None or key > after:
                    yield key, done, due, difference

    rows = nsmallest(limit + 1, selected(), key=lambda row: row[0])
    return {"group": group, "total": total, "case_notion": data["case_notion"],
            **({"as_of": cutoff.isoformat() + "Z"} if cutoff is not None else {}),
            "log_end": data["log_end"].isoformat() + "Z" if data["log_end"] else None,
            "next_after": rows[limit - 1][0] if len(rows) > limit else None,
            "cases": [{"case_id": key, "done_at": done.isoformat() + "Z" if done is not None else None,
                       "due_at": due.isoformat() + "Z" if due is not None else None,
                       "days_from_due": round(difference, 2) if difference is not None else None}
                      for key, done, due, difference in rows[:limit]]}


__all__ = ["due_dates", "due_cases", "collect", "classify", "FORMAT"]
