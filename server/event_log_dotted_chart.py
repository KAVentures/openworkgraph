"""Dotted chart: every event of every case over time (roadmap #326 H2).

As in Disco or ProM, each case is a row and each event a dot at its time,
coloured by activity. Time is either absolute (calendar time, to see arrival
waves, batches and backlogs) or relative to each case's first event (to compare
how cases unfold). Rows are ordered by case start or by duration.

To stay drawable, at most ``max_cases`` rows are returned, sampled evenly across
the ordered cases so the overall shape is kept, and at most ``MAX_POINTS`` dots,
thinned evenly within each case. Activity colours follow each activity's event
count in the whole log, so a filter never repaints an activity.
"""
from __future__ import annotations

from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_CASES = 1000
MAX_POINTS = 30_000
COLOURED_ACTIVITIES = 7   # the categorical palette's slots; the rest is "Other"


def dotted_chart(log_id: str, case_type: str | None = None, *, time: str = "absolute", order: str = "start",
                 max_cases: int = 300, cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if time not in ("absolute", "relative"):
        raise workspace.EventLogWorkspaceError("time must be absolute or relative")
    if order not in ("start", "duration"):
        raise workspace.EventLogWorkspaceError("order must be start or duration")
    if not 1 <= int(max_cases) <= MAX_CASES:
        raise workspace.EventLogWorkspaceError(f"max_cases must be between 1 and {MAX_CASES}")
    chosen = cohorts.parse_cohort(cohort)

    def run(conn):
        # Colours come from the whole log, before any cohort, so they stay put.
        ranked = conn.execute("""SELECT activity, count(*) AS n FROM ce GROUP BY activity
            ORDER BY n DESC, activity""").fetchall()
        slots = {a: i for i, (a, _) in enumerate(ranked[:COLOURED_ACTIVITIES])}
        cohorts.restrict(conn, chosen, log_id)
        sort = "min(ts), case_id" if order == "start" else "max(ts) - min(ts) DESC, min(ts), case_id"
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE dc AS SELECT case_id, min(ts) AS start_ts, max(ts) AS end_ts, count(*) AS events,
              row_number() OVER (ORDER BY {sort}) - 1 AS rank FROM ce GROUP BY case_id""")
        total_cases, total_events = conn.execute("SELECT count(*), coalesce(sum(events), 0) FROM dc").fetchone()
        first, last = conn.execute("SELECT min(ts), max(ts) FROM ce").fetchone()
        shown = min(int(max_cases), total_cases)
        # Even sample over the ordered cases keeps the chart's shape.
        conn.execute("""CREATE OR REPLACE TEMP TABLE pick AS SELECT DISTINCT
              CAST(floor(i * ? / ?) AS BIGINT) AS rank FROM range(?) t(i)""",
                     [total_cases, max(shown, 1), shown])
        per_case = max(1, MAX_POINTS // max(shown, 1))
        rows = conn.execute("""WITH chosen AS (SELECT d.* FROM dc d JOIN pick p USING (rank)),
              numbered AS (SELECT e.case_id, e.activity, e.ts, c.rank, c.start_ts, c.end_ts, c.events,
                  row_number() OVER (PARTITION BY e.case_id ORDER BY e.ts, e.event_id) - 1 AS k
                FROM ce e JOIN chosen c USING (case_id))
            SELECT rank, case_id, start_ts, end_ts, events, list(struct_pack(ts := ts, activity := activity) ORDER BY ts, k)
            FROM numbered WHERE events <= ? OR k % CAST(ceil(events / ?) AS BIGINT) = 0
            GROUP BY rank, case_id, start_ts, end_ts, events ORDER BY rank""", [per_case, per_case]).fetchall()
        origin = first
        other = len(slots)
        cases, points = [], 0
        for _, case_id, start, end, events, items in rows:
            base = start if time == "relative" else origin
            dots = [[(item["ts"] - base).total_seconds(), slots.get(item["activity"], other)] for item in items]
            points += len(dots)
            cases.append({"case_id": case_id, "start": start.isoformat() + "Z",
                          "duration_seconds": (end - start).total_seconds(), "events": events,
                          "points": dots})
        counts = dict(conn.execute("SELECT activity, count(*) FROM ce GROUP BY activity").fetchall())
        activities = [{"activity": a, "slot": i, "events": counts.get(a, 0)} for a, i in slots.items()]
        other_events = sum(n for a, n in counts.items() if a not in slots)
        if other_events:
            activities.append({"activity": None, "slot": other, "events": other_events,
                               "activities": sum(1 for a in counts if a not in slots)})
        span = max((d[0] for c in cases for d in c["points"]), default=0)
        # Separate omitted case rows from within-case dot thinning. These
        # counts describe the selected cohort, not the unfiltered log.
        events_in_shown_cases = sum(c["events"] for c in cases)
        omitted_by_case_sampling = total_events - events_in_shown_cases
        omitted_by_point_thinning = events_in_shown_cases - points
        return {
            "settings": {"time": time, "order": order, "max_cases": int(max_cases)},
            "cohort": cohorts.describe(chosen),
            "activities": activities, "cases": cases,
            "time_origin": origin.isoformat() + "Z" if origin and time == "absolute" else None,
            "span_seconds": span,
            "coverage": {"cases": total_cases, "cases_shown": len(cases), "cases_sampled": len(cases) < total_cases,
                         "events": total_events, "events_in_shown_cases": events_in_shown_cases,
                         "events_omitted_by_case_sampling": omitted_by_case_sampling,
                         "events_omitted_by_point_thinning": omitted_by_point_thinning,
                         "points_shown": points,
                         "points_thinned": omitted_by_point_thinning > 0,
                         "last_event_at": last.isoformat() + "Z" if last else None},
            "interpretation": ("Each row is a case and each dot an event at its recorded time. Sampled rows and "
                               "thinned dots keep the overall shape, not every event."),
        }
    return workspace._query(log_id, case_type, run)


__all__ = ["dotted_chart"]
