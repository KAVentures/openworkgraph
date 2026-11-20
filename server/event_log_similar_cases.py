"""Similar cases: the recorded cases whose path is closest to a chosen case, and how they went.

When a reviewer looks at one case (an escalation, a complaint, a slow order) the next question is
usually "have we seen this before?". Case explorers in process mining suites answer it by listing
cases like the selected one. This module ranks every other case in the selection by the distance
between its sequence of activities and the chosen case's:

* the distance counts the steps one case has that the other lacks, after matching equal activities
  in order (the same alignment as *compare two cases*, so a match from this list lines up there);
* similarity is ``1 - distance / (length of both paths)``: 1 for the same path, 0 for no shared step;
* among cases at the same distance, those whose duration is closest to the chosen case come first.

For each similar case it lists the activities it has more of or fewer of than the chosen case, its
duration and start, and over the returned cases the median duration, so the reviewer sees how cases
like this one usually went. Distinct paths are compared once; a cheap lower bound (the multiset
difference of activities) skips paths that cannot be closer than the ones already found. Similar
recorded paths do not mean the cases had the same cause.
"""
from __future__ import annotations

import bisect
from collections import Counter
from datetime import datetime
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_similar_cases.v1"
MAX_LIMIT = 50
MAX_TRACE_LENGTH = 500
MAX_COMPARISONS = 20_000


def _distance(a: list[int], b: list[int]) -> int:
    """Steps only one path has after matching equal steps in order: len(a) + len(b) - 2 * LCS."""
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return len(a) + len(b) - 2 * prev[-1]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _iso(ts: Any) -> str | None:
    return ts.isoformat(sep="T") + "Z" if isinstance(ts, datetime) else None


def similar_cases(log_id: str, case_id: str, case_type: str | None = None, *, limit: int = 10,
                  cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(case_id, str) or not case_id or len(case_id) > 500:
        raise EventLogWorkspaceError("give case_id as a case ID")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise EventLogWorkspaceError(f"limit must be a whole number from 1 to {MAX_LIMIT}")

    def target(conn):
        row = conn.execute("""SELECT list(activity ORDER BY ts, event_id), epoch(max(ts) - min(ts)), min(ts)
            FROM ce WHERE case_id = ?""", [case_id]).fetchone()
        return {"row": row}

    found = workspace._query(log_id, case_type, target)
    row = found["row"]
    if not row or not row[0]:
        raise EventLogWorkspaceError("this case is not in the log under the chosen case notion")
    path, duration, started = list(row[0]), row[1], row[2]
    if len(path) > MAX_TRACE_LENGTH:
        raise EventLogWorkspaceError(f"the case has {len(path)} events; at most {MAX_TRACE_LENGTH} can be compared")

    def others(conn):
        rows = conn.execute("""SELECT case_id, list(activity ORDER BY ts, event_id) AS path,
              epoch(max(ts) - min(ts)), min(ts) FROM ce WHERE case_id <> ? GROUP BY case_id
            HAVING count(*) <= ?""", [case_id, MAX_TRACE_LENGTH]).fetchall()
        total = conn.execute("SELECT count(DISTINCT case_id) FROM ce WHERE case_id <> ?", [case_id]).fetchone()[0]
        return {"rows": rows, "total": total}

    data = workspace._query_filtered(log_id, case_type, cohort, others)
    rows, total = data["rows"], data["total"]
    if not rows:
        raise EventLogWorkspaceError("no other cases in this selection")
    codes: dict[str, int] = {}
    encode = lambda p: [codes.setdefault(a, len(codes)) for a in p]  # noqa: E731
    mine = encode(path)
    mine_bag = Counter(mine)
    by_path: dict[tuple, list] = {}
    for cid, p, dur, start in rows:
        by_path.setdefault(tuple(p), []).append((cid, dur, start))
    # Exact lower bound of the distance: occurrences that cannot be matched whatever the order.
    candidates = []
    for p, members in by_path.items():
        coded = encode(list(p))
        bound = len(mine) + len(coded) - 2 * sum((mine_bag & Counter(coded)).values())
        candidates.append((bound, -len(members), p, coded, members))
    candidates.sort(key=lambda c: (c[0], c[1]))
    scored: list[tuple[int, int, tuple, list]] = []
    compared = 0
    cutoff = None
    exhausted = False
    for bound, _, p, coded, members in candidates:
        if cutoff is not None and bound > cutoff:
            break
        if compared >= MAX_COMPARISONS:
            exhausted = True
            break
        # With nothing in common the bound is the distance; otherwise compute it.
        d = bound if bound == len(mine) + len(coded) else _distance(mine, coded)
        bisect.insort(scored, (d, compared, p, members))
        compared += 1
        if cutoff is None or d < cutoff:
            covered = 0
            for dist, _, _, mem in scored:
                covered += len(mem)
                if covered >= limit:
                    cutoff = dist
                    break
    out = []
    for d, _, p, members in scored:
        mem = sorted(members, key=lambda m: (abs((m[1] or 0) - (duration or 0)), str(m[0])))
        bag = Counter(p)
        more = sorted((bag - Counter(path)).items(), key=lambda x: (-x[1], x[0]))
        fewer = sorted((Counter(path) - bag).items(), key=lambda x: (-x[1], x[0]))
        for cid, dur, start in mem:
            if len(out) >= limit:
                break
            out.append({"case_id": cid, "distance": d,
                        "similarity": round(1 - d / (len(path) + len(p)), 4) if path or p else 1.0,
                        "same_path": d == 0, "events": len(p),
                        "more_of": [{"activity": a, "times": n} for a, n in more[:10]],
                        "fewer_of": [{"activity": a, "times": n} for a, n in fewer[:10]],
                        "duration_seconds": dur, "started_at": _iso(start)})
        if len(out) >= limit:
            break
    same = len(by_path.get(tuple(path), []))
    durations = [c["duration_seconds"] for c in out if c["duration_seconds"] is not None]
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": found.get("case_notion"),
        "case": {"case_id": case_id, "events": len(path), "path": path[:50], "path_truncated": len(path) > 50,
                 "duration_seconds": duration, "started_at": _iso(started)},
        "cases_searched": len(rows), "cases_in_selection": total,
        "cases_too_long_to_compare": max(0, total - len(rows)),
        "same_path_cases": same, "distinct_paths": len(by_path), "paths_compared": compared,
        "search_complete": not exhausted,
        "similar": out,
        "similar_median_duration_seconds": _median(durations),
        "method": ("cases ranked by the number of steps only one of the two paths has after matching equal steps in "
                   "order (as in compare two cases); ties broken by the closest duration"),
        "caveat": ("Similar recorded paths do not mean the cases had the same cause or outcome. Durations are first to "
                   "last recorded event."),
    }


__all__ = ["similar_cases", "FORMAT"]
