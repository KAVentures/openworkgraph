"""Activity co-occurrence: which activities happen in the same cases, regardless of order.

Process maps and Declare rules describe order; co-occurrence asks a different question, as in the
co-occurrence and correlation views of process mining tools: when a case has activity A, how
often does it also have B? For the most common activities this gives, for every pair:

* ``both``: cases with both activities;
* ``share_of_a``: the share of cases with A that also have B (read the matrix row by row);
* ``lift``: how much more often the two occur together than if they were independent
  (``both × cases / (cases with A × cases with B)``); above 1 they go together, below 1 they avoid
  each other, 0 means never together.

It also lists the pairs that go together most (by lift, with enough cases) and the pairs of common
activities that never occur in the same case, which often mark alternative branches. Co-occurrence
describes the recorded cases; it is not a dependency or a cause.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_cooccurrence.v1"
MAX_ACTIVITIES = 25
MIN_SUPPORT = 5
TOP_PAIRS = 15


def cooccurrence(log_id: str, case_type: str | None = None, *, limit: int = 12, min_support: int = MIN_SUPPORT,
                 cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= MAX_ACTIVITIES:
        raise EventLogWorkspaceError(f"limit must be a whole number from 2 to {MAX_ACTIVITIES}")
    if isinstance(min_support, bool) or not isinstance(min_support, int) or not 1 <= min_support <= 10 ** 6:
        raise EventLogWorkspaceError("min_support must be a whole number of cases from 1")

    def run(conn):
        total, total_activities = conn.execute("SELECT count(DISTINCT case_id), count(DISTINCT activity) FROM ce").fetchone()
        top = conn.execute("""SELECT activity, count(DISTINCT case_id) AS n FROM ce GROUP BY 1
            ORDER BY n DESC, 1 LIMIT ?""", [limit]).fetchall()
        names = [a for a, _ in top]
        pairs = conn.execute("""WITH s AS (SELECT DISTINCT case_id, activity FROM ce WHERE activity IN (SELECT unnest(?)))
            SELECT x.activity, y.activity, count(*) FROM s x JOIN s y ON x.case_id = y.case_id AND x.activity < y.activity
            GROUP BY 1, 2""", [names]).fetchall()
        return {"total": total, "total_activities": total_activities, "top": top, "pairs": pairs}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    total, top = data["total"], data["top"]
    if not total:
        raise EventLogWorkspaceError("no cases in this selection")
    if len(top) < 2:
        raise EventLogWorkspaceError("at least two activities are needed")
    cases = dict(top)
    names = [a for a, _ in top]
    both: dict[tuple[str, str], int] = {}
    for a, b, n in data["pairs"]:
        both[(a, b)] = both[(b, a)] = n

    def lift(a: str, b: str) -> float | None:
        n = both.get((a, b), 0)
        return round(n * total / (cases[a] * cases[b]), 3) if cases[a] and cases[b] else None

    matrix = [{"activity": a, "cases": cases[a],
               "with": [{"activity": b, "both": cases[a] if a == b else both.get((a, b), 0),
                         "share_of_a": 1.0 if a == b else round(both.get((a, b), 0) / cases[a], 4),
                         "lift": None if a == b else lift(a, b)} for b in names]}
              for a in names]
    pair_rows = [{"a": a, "b": b, "both": both.get((a, b), 0), "cases_a": cases[a], "cases_b": cases[b],
                  "share_of_a": round(both.get((a, b), 0) / cases[a], 4),
                  "share_of_b": round(both.get((a, b), 0) / cases[b], 4), "lift": lift(a, b)}
                 for a, b in combinations(names, 2)]
    together = sorted((p for p in pair_rows if p["both"] >= min_support and (p["lift"] or 0) > 1),
                      key=lambda p: (-p["lift"], -p["both"], p["a"], p["b"]))
    never = sorted((p for p in pair_rows if p["both"] == 0 and min(p["cases_a"], p["cases_b"]) >= min_support),
                   key=lambda p: (-min(p["cases_a"], p["cases_b"]), p["a"], p["b"]))
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "cases": total, "activities": [{"activity": a, "cases": cases[a], "share": round(cases[a] / total, 4)}
                                       for a in names],
        "matrix": matrix, "go_together": together[:TOP_PAIRS], "never_together": never[:TOP_PAIRS],
        "activities_in_scope": data["total_activities"], "activities_truncated": len(top) < data["total_activities"],
        "qualifying_go_together_pairs": len(together), "qualifying_never_together_pairs": len(never),
        "pair_list_limit": TOP_PAIRS, "cohort": data.get("cohort", {}),
        "min_support": min_support, "limit": limit,
        "method": ("activities compared by the cases they occur in, regardless of order; lift = cases with both × all "
                   "cases / (cases with A × cases with B)"),
        "caveat": ("Co-occurrence describes the recorded cases. Two activities that go together need not depend on each "
                   "other, and pairs that never occur together may be alternative branches or simply rare."),
    }


__all__ = ["cooccurrence", "FORMAT"]
