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
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_due_dates.v1"
MAX_DAYS = 3650
MAX_LATE_CASES = 50
GROUPS = ("on_time", "early", "late", "open_overdue", "open_not_due", "no_due_date", "unreadable_due_date")


def _parse_due(raw: Any) -> datetime | None:
    text = str(raw).strip()
    if not text:
        return None
    try:
        if len(text) == 10:   # YYYY-MM-DD: due by the end of that day
            return datetime.fromisoformat(text) + timedelta(days=1)
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
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


def due_dates(log_id: str, activity: str, case_type: str | None = None, *, due_attribute: str | None = None,
              due_days: float | None = None, early_days: float | None = None, include_case_ids: bool = True,
              cohort: dict[str, Any] | None = None) -> dict[str, Any]:
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

    def run(conn):
        cases = conn.execute("""SELECT case_id, min(ts), min(ts) FILTER (WHERE activity = ?) FROM ce
            GROUP BY case_id""", [activity]).fetchall()
        log_end = conn.execute("SELECT max(ts) FROM ce").fetchone()[0]
        has_activity = conn.execute("SELECT count(*) FROM ce WHERE activity = ?", [activity]).fetchone()[0]
        raw: dict[str, Any] = {}
        if due_attribute is not None:
            path = '$."' + due_attribute.replace('"', '\\"') + '"'
            for case_id, value in conn.execute("""SELECT case_id, arg_min(v, (ts, event_id)) FROM (
                  SELECT case_id, ts, event_id, json_extract_string(attributes, ?) AS v FROM ce)
                WHERE v IS NOT NULL AND trim(v) <> '' GROUP BY case_id""", [path]).fetchall():
                raw[case_id] = value
            for object_id, attributes in conn.execute("""SELECT object_id, attributes FROM objects
                WHERE log_id = ? AND object_id IN (SELECT DISTINCT case_id FROM ce)""", [log_id]).fetchall():
                value = json.loads(attributes or "{}").get(due_attribute)
                if value not in (None, ""):
                    raw[object_id] = value    # a case attribute wins over an event attribute
        return {"cases_rows": cases, "log_end": log_end, "has_activity": has_activity, "raw_due": raw}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows, log_end, raw_due = data.pop("cases_rows"), data.pop("log_end"), data.pop("raw_due")
    if not data.pop("has_activity"):
        raise EventLogWorkspaceError(f"no case in this selection has the activity {activity!r}")
    groups: Counter = Counter()
    days_late: list[float] = []
    days_early: list[float] = []
    months: dict[str, Counter] = {}
    late_cases = []
    for case_id, started, done in rows:
        if due_days is not None:
            due = started + timedelta(days=due_days)
        elif case_id not in raw_due:
            groups["no_due_date"] += 1
            continue
        else:
            due = _parse_due(raw_due[case_id])
            if due is None:
                groups["unreadable_due_date"] += 1
                continue
        if done is None:
            group = "open_overdue" if log_end is not None and due < log_end else "open_not_due"
        elif done > due:
            group = "late"
            late = (done - due).total_seconds() / 86400
            days_late.append(late)
            late_cases.append((late, case_id, done, due))
        elif early_days is not None and (due - done).total_seconds() / 86400 > early_days:
            group = "early"
            days_early.append((due - done).total_seconds() / 86400)
        else:
            group = "on_time"
        groups[group] += 1
        months.setdefault(due.strftime("%Y-%m"), Counter())[group] += 1
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
        "method": ("the first occurrence of the activity in each case is compared with the case's due date; a date "
                   "without a time is due by the end of that day (UTC); open cases are overdue when the due date is "
                   "before the log's last event"),
        "caveat": ("Due-date adherence says how often deadlines were met, not why. Cases without a readable due "
                   "date are counted separately, never guessed."),
    }


__all__ = ["due_dates", "FORMAT"]
