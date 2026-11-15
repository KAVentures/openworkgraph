"""Case playback over the process map (#370 R7, Disco-style animation).

Returns, for an evenly sampled set of cases, each case's events as offsets
from the log's first event, restricted to the same most frequent activities the
process map shows for the same ``activities`` share. The dashboard moves one
token per case along the drawn map edges between consecutive events.

This is a visualization of recorded timestamps. It never executes, replays or
schedules anything. Events that share a time within a case are disclosed: the
token jumps between them instantly, in event-identifier order.
"""
from __future__ import annotations

from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_CASES = 500
MAX_POINTS = 30_000
MAX_ACTIVITIES = 64      # the process map's bound


def playback(log_id: str, case_type: str | None = None, *, activities: float = 1.0, max_cases: int = 200,
             cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if not 0 < float(activities) <= 1:
        raise workspace.EventLogWorkspaceError("activities must be a share above 0 and at most 1")
    if not 1 <= int(max_cases) <= MAX_CASES:
        raise workspace.EventLogWorkspaceError(f"max_cases must be between 1 and {MAX_CASES}")
    chosen = cohorts.parse_cohort(cohort)

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        # Only the most frequent MAX_ACTIVITIES can be drawn. Obtain the full
        # count and total as window aggregates without fetching every distinct
        # activity label into application memory.
        ranked = conn.execute("""SELECT activity, n, count(*) OVER () AS activity_count,
            sum(n) OVER () AS total_events FROM (SELECT activity, count(*) AS n FROM ce GROUP BY activity)
            ORDER BY n DESC, activity LIMIT ?""", [MAX_ACTIVITIES]).fetchall()
        activity_count = ranked[0][2] if ranked else 0
        total = ranked[0][3] if ranked else 0
        kept, covered = [], 0
        for activity, n, _, _ in ranked:      # the same selection as the process map
            if kept and (covered >= activities * total or len(kept) >= MAX_ACTIVITIES):
                break
            kept.append(activity)
            covered += n
        index = {a: i for i, a in enumerate(kept)}
        conn.execute("CREATE OR REPLACE TEMP TABLE pe AS SELECT * FROM ce WHERE list_contains(?, activity)", [kept])
        origin, last = conn.execute("SELECT min(ts), max(ts) FROM pe").fetchone()
        conn.execute("""CREATE OR REPLACE TEMP TABLE pcases AS SELECT case_id, min(ts) AS start_ts, count(*) AS n,
              row_number() OVER (ORDER BY min(ts), case_id) - 1 AS rank FROM pe GROUP BY case_id""")
        cases_total = conn.execute("SELECT count(*) FROM pcases").fetchone()[0]
        shown = min(int(max_cases), cases_total)
        per_case = max(2, MAX_POINTS // max(shown, 1))
        # Limit each sampled case *before* building the list returned to Python.
        # A selected case can contain millions of events; slicing its fully
        # materialized list after fetch would defeat the playback point budget.
        rows = conn.execute("""WITH pick AS (SELECT DISTINCT CAST(floor(i * ? / ?) AS BIGINT) AS rank FROM range(?) t(i)),
              chosen AS (SELECT c.* FROM pcases c JOIN pick USING (rank)),
              numbered AS (SELECT c.rank, c.case_id, c.n, e.ts, e.event_id, e.activity,
                  row_number() OVER (PARTITION BY c.rank ORDER BY e.ts, e.event_id) AS point_number
                  FROM chosen c JOIN pe e USING (case_id))
            SELECT rank, case_id, n, list(struct_pack(ts := ts, activity := activity) ORDER BY ts, event_id)
            FROM numbered WHERE point_number <= ? GROUP BY rank, case_id, n ORDER BY rank""",
                            [cases_total, max(shown, 1), shown, per_case]).fetchall()
        cases, points, tied, truncated = [], 0, 0, 0
        for _, case_id, n, items in rows:
            if len(items) < n:
                truncated += 1
            steps = [[(item["ts"] - origin).total_seconds(), index[item["activity"]]] for item in items]
            tied += sum(1 for a, b in zip(steps, steps[1:]) if a[0] == b[0])
            points += len(steps)
            cases.append({"case_id": case_id, "events": steps, "events_truncated": len(steps) < n})
        return {
            "activities": kept,
            "activities_hidden": activity_count - len(kept),
            "origin": origin.isoformat() + "Z" if origin else None,
            "span_seconds": (last - origin).total_seconds() if origin else 0,
            "cases": cases,
            "coverage": {"cases": cases_total, "cases_shown": len(cases), "cases_sampled": len(cases) < cases_total,
                         "events_shown": points, "cases_truncated": truncated,
                         "steps_sharing_a_timestamp": tied},
            "settings": {"activities": float(activities), "max_cases": int(max_cases)},
            "cohort": cohorts.describe(chosen),
            "interpretation": ("Tokens move between recorded events along the drawn map; this shows timing, not work "
                               "being done, and nothing is executed. Steps with the same time jump instantly in "
                               "event-identifier order. Hidden activities' events are left out, as in the map."),
        }
    return workspace._query(log_id, case_type, run)


__all__ = ["playback"]
