"""Disco-style abstracted process map (roadmap #326 G3).

Two sliders, as in Fluxicon Disco or Apromore:

- ``activities`` (0–100%): keep the most frequent activities until they cover
  that share of all events. Hidden activities' events are removed *before* the
  map is computed, so cases are reconnected through the activities that remain
  (the map stays a true directly-follows map of the filtered log).
- ``paths`` (0–100%): keep the most frequent transitions until they cover that
  share of all transitions, then add back each kept activity's strongest
  incoming and outgoing transition so no activity is left disconnected.

Each transition carries frequency and performance (median, mean and 90th
percentile gap between the two events). Gaps are elapsed time between recorded
events, not measured work or verified waiting.
"""
from __future__ import annotations

from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_MAP_ACTIVITIES = 64      # the dashboard graph renderer's bound
MAX_MAP_TRANSITIONS = 4096
START, END = "▶ start", "■ end"


def process_map(log_id: str, case_type: str | None = None, *, activities: float = 1.0, paths: float = 1.0,
                cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    for name, value in (("activities", activities), ("paths", paths)):
        if not 0 < float(value) <= 1:
            raise workspace.EventLogWorkspaceError(f"{name} must be a share above 0 and at most 1")
    chosen = cohorts.parse_cohort(cohort)

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        ranked = conn.execute("""SELECT activity, count(*) AS events, count(DISTINCT case_id) AS cases
            FROM ce GROUP BY activity ORDER BY events DESC, activity""").fetchall()
        total_events = sum(row[1] for row in ranked)
        kept, covered = [], 0
        for activity, events, cases in ranked:
            if kept and (covered >= activities * total_events or len(kept) >= MAX_MAP_ACTIVITIES):
                break
            kept.append((activity, events, cases))
            covered += events
        names = [row[0] for row in kept]
        conn.execute("CREATE OR REPLACE TEMP TABLE fe AS SELECT case_id, activity, ts, event_id FROM ce "
                     "WHERE list_contains(?, activity)", [names])
        # Directly-follows over the filtered log, including start and end pseudo-transitions.
        edges = conn.execute(f"""WITH seq AS (
              SELECT case_id, activity, ts,
                lag(activity) OVER w AS prev, lag(ts) OVER w AS prev_ts,
                lead(activity) OVER w AS nxt FROM fe WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id)),
            pairs AS (
              SELECT prev AS src, activity AS dst, case_id, epoch(ts - prev_ts) AS gap FROM seq WHERE prev IS NOT NULL
              UNION ALL SELECT '{START}', activity, case_id, NULL FROM seq WHERE prev IS NULL
              UNION ALL SELECT activity, '{END}', case_id, NULL FROM seq WHERE nxt IS NULL)
            SELECT src, dst, count(*) AS n, count(DISTINCT case_id) AS cases,
              quantile_cont(gap, 0.5), avg(gap), quantile_cont(gap, 0.9)
            FROM pairs GROUP BY src, dst ORDER BY n DESC, src, dst""").fetchall()
        real = [e for e in edges if e[0] != START and e[1] != END]
        total_transitions = sum(e[2] for e in real)
        keep: set[tuple[str, str]] = set()
        running = 0
        for src, dst, n, *_ in real:
            if keep and running >= paths * total_transitions:
                break
            keep.add((src, dst))
            running += n
        # Keep every visible activity connected: strongest in and out (start/end count).
        for name in names:
            incoming = [e for e in edges if e[1] == name]
            outgoing = [e for e in edges if e[0] == name]
            if incoming and not any((e[0], e[1]) in keep for e in incoming):
                keep.add((incoming[0][0], incoming[0][1]))
            if outgoing and not any((e[0], e[1]) in keep for e in outgoing):
                keep.add((outgoing[0][0], outgoing[0][1]))
        for src, dst, *_ in edges:   # start/end edges are always shown for kept activities
            if src == START or dst == END:
                keep.add((src, dst))
        shown = [e for e in edges if (e[0], e[1]) in keep][:MAX_MAP_TRANSITIONS]
        shown_real = sum(e[2] for e in shown if e[0] != START and e[1] != END)
        hidden = len(ranked) - len(kept)
        return {
            "activities": [{"activity": a, "event_count": e, "case_count": c} for a, e, c in kept],
            "transitions": [{"from": s, "to": d, "transition_count": n, "case_count": c,
                             "median_elapsed_gap_seconds": med, "mean_elapsed_gap_seconds": mean,
                             "p90_elapsed_gap_seconds": p90}
                            for s, d, n, c, med, mean, p90 in shown if s != START and d != END],
            "start_activities": {d: n for s, d, n, *_ in shown if s == START},
            "end_activities": {s: n for s, d, n, *_ in shown if d == END},
            "settings": {"activities": float(activities), "paths": float(paths)},
            "coverage": {"activities_shown": len(kept), "activities_hidden": hidden,
                         "event_share_shown": covered / total_events if total_events else None,
                         "transition_types_shown": sum(1 for e in shown if e[0] != START and e[1] != END),
                         "transition_types_total": len(real),
                         "transition_share_shown": shown_real / total_transitions if total_transitions else None,
                         "activity_limit_reached": hidden > 0 and len(kept) >= MAX_MAP_ACTIVITIES},
            "cohort": cohorts.describe(chosen),
            "interpretation": ("Hidden activities are filtered out before the map is drawn, so arrows can join "
                               "steps that had hidden work between them. Gaps are elapsed time between recorded "
                               "events, not measured work or verified waiting."),
        }
    return workspace._query(log_id, case_type, run)


__all__ = ["process_map"]
