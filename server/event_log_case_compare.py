"""Compare two cases side by side: which steps they share, which only one has, and where time went.

Case explorers in process mining tools let a reviewer put a slow case next to a normal one. This
aligns the two cases' activity sequences with an edit-distance alignment (matching equal activities
in order, at least cost), so shared steps line up and steps only one case has stand out. For each
row it gives the time from that case's start, and for shared steps how much later the second case
reached it. It describes the two recorded cases only.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_case_compare.v1"
MAX_EVENTS = 500


def _align(a: list[str], b: list[str]) -> list[tuple[int | None, int | None]]:
    """Least-cost alignment: a match costs 0, a step only one case has costs 1 (no substitutions)."""
    n, m = len(a), len(b)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        cost[i][0] = i
    for j in range(m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = min(cost[i - 1][j], cost[i][j - 1]) + 1
            if a[i - 1] == b[j - 1]:
                best = min(best, cost[i - 1][j - 1])
            cost[i][j] = best
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i or j:
        if i and j and a[i - 1] == b[j - 1] and cost[i][j] == cost[i - 1][j - 1]:
            pairs.append((i - 1, j - 1)); i -= 1; j -= 1
        elif i and cost[i][j] == cost[i - 1][j] + 1:
            pairs.append((i - 1, None)); i -= 1
        else:
            pairs.append((None, j - 1)); j -= 1
    return pairs[::-1]


def compare_cases(log_id: str, case_a: str, case_b: str, case_type: str | None = None) -> dict[str, Any]:
    for name, value in (("case_a", case_a), ("case_b", case_b)):
        if not isinstance(value, str) or not value or len(value) > 500:
            raise EventLogWorkspaceError(f"give {name} as a case ID")
    if case_a == case_b:
        raise EventLogWorkspaceError("choose two different cases")

    def run(conn):
        rows = conn.execute("""SELECT case_id, list(activity ORDER BY ts, event_id), list(epoch(ts) ORDER BY ts, event_id)
            FROM ce WHERE case_id IN (?, ?) GROUP BY case_id""", [case_a, case_b]).fetchall()
        return {"rows": {r[0]: (r[1], r[2]) for r in rows}}

    rows = workspace._query(log_id, case_type, run)["rows"]
    for cid in (case_a, case_b):
        if cid not in rows:
            raise EventLogWorkspaceError(f"no case {cid!r} in this log")
        if len(rows[cid][0]) > MAX_EVENTS:
            raise EventLogWorkspaceError(f"case {cid!r} has more than {MAX_EVENTS} events")
    (acts_a, ts_a), (acts_b, ts_b) = rows[case_a], rows[case_b]
    hours = lambda seconds: round(seconds / 3600, 3)  # noqa: E731
    steps = []
    for i, j in _align(list(acts_a), list(acts_b)):
        a_at = hours(ts_a[i] - ts_a[0]) if i is not None else None
        b_at = hours(ts_b[j] - ts_b[0]) if j is not None else None
        kind = "both" if i is not None and j is not None else ("only_a" if j is None else "only_b")
        steps.append({"activity": acts_a[i] if i is not None else acts_b[j], "kind": kind,
                      "a_hours_from_start": a_at, "b_hours_from_start": b_at,
                      "b_later_by_hours": round(b_at - a_at, 3) if kind == "both" else None})
    shared = [s for s in steps if s["kind"] == "both"]
    # Between which two shared steps did the second case fall furthest behind?
    gap = None
    for prev, cur in zip(shared, shared[1:]):
        grew = round(cur["b_later_by_hours"] - prev["b_later_by_hours"], 3)
        if grew > 0 and (gap is None or grew > gap["extra_hours"]):
            gap = {"to": cur["activity"], "from": prev["activity"], "extra_hours": grew}
    return {
        "format": FORMAT, "log_id": log_id,
        "case_a": {"case_id": case_a, "events": len(acts_a), "hours": hours(ts_a[-1] - ts_a[0])},
        "case_b": {"case_id": case_b, "events": len(acts_b), "hours": hours(ts_b[-1] - ts_b[0])},
        "steps": steps,
        "shared_steps": len(shared), "only_a": sum(s["kind"] == "only_a" for s in steps),
        "only_b": sum(s["kind"] == "only_b" for s in steps),
        "largest_gap_growth": gap,
        "method": "edit-distance alignment of the two activity sequences (shared steps matched in order)",
        "caveat": "Two recorded cases side by side; a difference between them is not a cause.",
    }


__all__ = ["compare_cases", "FORMAT"]
