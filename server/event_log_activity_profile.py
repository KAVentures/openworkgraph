"""Activity profile: one activity seen from every side.

An activity overview (Celonis's activity view, Disco's activity statistics) answers "what about this
step?" in one place: how many cases have it and how often it repeats within them, what usually
comes before and after it and how long those gaps take, when in the week and day it happens, and how
long cases with it take compared with cases without it. Everything is computed from the recorded
events; a longer duration for cases with the step is an association, not proof the step causes it.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_activity_profile.v1"
TOP = 5
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _q(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * p
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return round((s[lo] + (s[hi] - s[lo]) * (pos - lo)) / 3600, 3)


def activity_profile(log_id: str, activity: str, case_type: str | None = None, *,
                     cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(activity, str) or not activity or len(activity) > 500:
        raise EventLogWorkspaceError("choose an activity")

    def run(conn):
        rows = conn.execute("""SELECT case_id, list(activity ORDER BY ts, event_id), list(epoch(ts) ORDER BY ts, event_id),
              list(isodow(ts) ORDER BY ts, event_id), list(hour(ts) ORDER BY ts, event_id)
            FROM ce GROUP BY case_id""").fetchall()
        return {"rows": rows}

    rows = workspace._query_filtered(log_id, case_type, cohort, run)["rows"]
    total = len(rows)
    with_cases, occurrences = 0, 0
    per_case: Counter = Counter()
    before: Counter[str | None] = Counter()
    after: Counter[str | None] = Counter()
    gap_in: dict[str, list[float]] = {}
    gap_out: dict[str, list[float]] = {}
    weekday: Counter = Counter()
    hours: Counter = Counter()
    dur_with, dur_without, first_at = [], [], []
    for _, acts, stamps, dows, hrs in rows:
        duration = stamps[-1] - stamps[0]
        idx = [i for i, a in enumerate(acts) if a == activity]
        if not idx:
            dur_without.append(duration)
            continue
        with_cases += 1
        occurrences += len(idx)
        per_case[len(idx)] += 1
        dur_with.append(duration)
        first_at.append(stamps[idx[0]] - stamps[0])
        for i in idx:
            weekday[_WEEKDAYS[dows[i] - 1]] += 1
            hours[hrs[i]] += 1
            prev = acts[i - 1] if i else None
            nxt = acts[i + 1] if i + 1 < len(acts) else None
            before[prev] += 1
            after[nxt] += 1
            if prev is not None:
                gap_in.setdefault(prev, []).append(stamps[i] - stamps[i - 1])
            if nxt is not None:
                gap_out.setdefault(nxt, []).append(stamps[i + 1] - stamps[i])
    if not with_cases:
        raise EventLogWorkspaceError(f"no case in this selection has the activity {activity!r}")

    def neighbours(counts: Counter[str | None], gaps: dict[str, list[float]], boundary: str) -> list[dict[str, Any]]:
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0] is None, item[0] or ""))
        return [{"activity": a if a is not None else boundary, "times": n, "share": round(n / occurrences, 4),
                 "median_hours": _q(gaps.get(a, []), 0.5) if a is not None else None,
                 **({"boundary": True} if a is None else {})} for a, n in ranked[:TOP]]

    repeat_cases = sum(n for k, n in per_case.items() if k > 1)
    return {
        "format": FORMAT, "log_id": log_id, "activity": activity,
        "cases": total, "cases_with_activity": with_cases, "share_of_cases": round(with_cases / total, 4),
        "occurrences": occurrences, "occurrences_per_case": round(occurrences / with_cases, 3),
        "cases_repeating_it": repeat_cases, "repeat_share": round(repeat_cases / with_cases, 4),
        "comes_after": neighbours(before, gap_in, "(start)"), "comes_before": neighbours(after, gap_out, "(end)"),
        "hours_from_case_start_to_first": {"median": _q(first_at, 0.5), "p90": _q(first_at, 0.9)},
        "case_hours": {"with_activity": {"median": _q(dur_with, 0.5), "p90": _q(dur_with, 0.9)},
                       "without_activity": {"median": _q(dur_without, 0.5), "p90": _q(dur_without, 0.9)}},
        "by_weekday": [{"day": d, "times": weekday[d]} for d in _WEEKDAYS],
        "by_hour": [{"hour": h, "times": hours[h]} for h in range(24)],
        "caveat": ("Counts and durations of recorded events. Cases with the activity can take longer for many reasons; "
                   "times of day are in the log's stored time zone (UTC)."),
    }


__all__ = ["activity_profile", "FORMAT"]
