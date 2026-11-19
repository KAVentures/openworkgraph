"""Path explorer: after a sequence of steps, what happens next, how often and how fast.

Interactive variant exploration (Celonis's Process Explorer, UiPath Process Mining's and ProM's
variant trees) lets a reviewer walk the process one step at a time: start at the beginning of the
cases (or at any point where a chosen sequence of steps occurs), see which activities follow and in
how many cases, then pick one and go deeper. For each next step this module reports:

* how many cases continue with it, as a share of the cases at this point;
* the median and 90th-percentile hours from the last step of the sequence to it;
* the median hours from it to the end of the case;
* whether the case ends right there (``(end)``).

With ``anchor="start"`` the sequence must be how the case begins; with ``anchor="anywhere"`` the
first place in the case where the steps occur one right after another is used. The explorer
describes recorded order and timing, not why cases branch.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_path_explorer.v1"
END = "(end)"
MAX_PREFIX = 30
MAX_NEXT = 30
_ANCHORS = {"start", "anywhere"}


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return round((ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)) / 3600, 3)


def _find(path: list[str], prefix: list[str], anchor: str) -> int | None:
    """Index just after the prefix in ``path``, or None."""
    k = len(prefix)
    if not k:
        return 0
    if anchor == "start":
        return k if path[:k] == prefix else None
    first = prefix[0]
    for i in range(len(path) - k + 1):
        if path[i] == first and path[i:i + k] == prefix:
            return i + k
    return None


def next_steps(log_id: str, prefix: list[str] | None = None, case_type: str | None = None, *,
               anchor: str = "start", cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    prefix = [str(p) for p in (prefix or [])]
    if len(prefix) > MAX_PREFIX or any(not p or len(p) > 500 for p in prefix):
        raise EventLogWorkspaceError(f"the sequence has at most {MAX_PREFIX} named steps")
    if anchor not in _ANCHORS:
        raise EventLogWorkspaceError("anchor must be start or anywhere")
    if anchor == "anywhere" and not prefix:
        raise EventLogWorkspaceError("choose at least one step to look for anywhere in the cases")

    def run(conn):
        rows = conn.execute("""SELECT list(activity ORDER BY ts, event_id), list(epoch(ts) ORDER BY ts, event_id)
            FROM ce GROUP BY case_id""").fetchall()
        return {"rows": rows}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows = data.pop("rows")
    total = len(rows)
    at_point = 0
    gaps: dict[str, list[float]] = defaultdict(list)
    to_end: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    elapsed: list[float] = []
    for path, stamps in rows:
        after = _find(path, prefix, anchor)
        if after is None:
            continue
        at_point += 1
        if prefix:
            elapsed.append(stamps[after - 1] - stamps[0])
        if after >= len(path):
            counts[END] += 1
            continue
        step = path[after]
        counts[step] += 1
        gaps[step].append(stamps[after] - stamps[after - 1] if after else 0.0)
        to_end[step].append(stamps[-1] - stamps[after])
    order = sorted(counts, key=lambda a: (-counts[a], a == END, a))
    steps = []
    for activity in order[:MAX_NEXT]:
        n = counts[activity]
        steps.append({
            "activity": activity, "is_end": activity == END, "cases": n,
            "share": round(n / at_point, 4) if at_point else None,
            "hours_from_previous": None if activity == END or not prefix else {
                "median": _quantile(gaps[activity], 0.5), "p90": _quantile(gaps[activity], 0.9)},
            "hours_to_case_end": None if activity == END else {"median": _quantile(to_end[activity], 0.5)},
        })
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "prefix": prefix, "anchor": anchor, "cases": total, "cases_at_point": at_point,
        "share_of_cases": round(at_point / total, 4) if total else None,
        "hours_to_reach_point": {"median": _quantile(elapsed, 0.5), "p90": _quantile(elapsed, 0.9)}
                                if prefix and anchor == "start" else None,
        "next": steps, "next_total": len(order),
        "method": ("cases whose activities " + ("begin with" if anchor == "start" else "contain, one right after "
                   "another,") + " the chosen steps are followed to the activity that comes next"),
        "caveat": "The explorer shows recorded order and timing, not why cases branch.",
    }


__all__ = ["next_steps", "FORMAT", "END"]
