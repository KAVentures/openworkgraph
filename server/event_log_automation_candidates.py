"""Automation candidates: which activities of a log look worth automating, and why.

Process-mining and automation vendors rank activities by automation potential (Celonis, UiPath
Process Mining, SAP Signavio). This ranking uses only what an event log can show, and shows every
component:

* **volume**: occurrences per week, scaled against the busiest activity (log scale);
* **routine**: how often the activity follows its most common predecessor and is followed by its
  most common successor (fixed routing suggests rules; variable routing suggests judgement);
* **time**: the share of all waiting time in the log that falls just before this activity;
* repeats per case and how many resources perform it are reported alongside, not hidden in the score.

score = 100 × (0.4·volume + 0.4·routine + 0.2·time). The weights are returned with the result.
An event log records when steps happened, not how long anyone worked on them, so "waiting" is the
gap since the previous event. Nothing here estimates savings or says an activity should be automated.
"""
from __future__ import annotations

import math
from typing import Any

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event_log_automation_candidates.v1"
WEIGHTS = {"volume": 0.4, "routine": 0.4, "time": 0.2}
MIN_CASES = 5
MAX_ACTIVITIES = 50


def _pattern(routine: float, repeats: float) -> str:
    if routine >= 0.8 and repeats < 1.1:
        return "fixed"
    if routine >= 0.6:
        return "mostly_fixed"
    return "variable"


def candidates(log_id: str, case_type: str | None = None, *, limit: int = 15,
               cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= MAX_ACTIVITIES:
        raise workspace.EventLogWorkspaceError(f"limit must be an integer from 1 to {MAX_ACTIVITIES}")

    def run(conn):
        conn.execute("""CREATE OR REPLACE TEMP TABLE ac AS SELECT case_id, activity, resource,
              coalesce(lag(activity) OVER w, '(start)') AS prev, coalesce(lead(activity) OVER w, '(end)') AS next,
              epoch(ts - lag(ts) OVER w) AS wait
            FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id)""")
        first, last = conn.execute("SELECT min(ts), max(ts) FROM ce").fetchone()
        weeks = max(1.0, (last - first).total_seconds() / 604800) if first and last else 1.0
        total_wait = conn.execute("SELECT coalesce(sum(wait), 0) FROM ac WHERE wait > 0").fetchone()[0] or 0.0
        rows = conn.execute("""
            WITH per AS (SELECT activity, count(*) AS n, count(DISTINCT case_id) AS cases,
                    count(DISTINCT nullif(resource, '')) AS resources,
                    coalesce(sum(wait) FILTER (WHERE wait > 0), 0) AS wait_sum,
                    quantile_cont(wait, 0.5) FILTER (WHERE wait IS NOT NULL) AS wait_median
                  FROM ac GROUP BY activity),
            pv AS (SELECT activity, max(c) AS top FROM (SELECT activity, prev, count(*) AS c FROM ac GROUP BY 1, 2) GROUP BY 1),
            nx AS (SELECT activity, max(c) AS top FROM (SELECT activity, next, count(*) AS c FROM ac GROUP BY 1, 2) GROUP BY 1),
            tr AS (SELECT activity, max(c) AS top FROM (SELECT activity, resource, count(*) AS c FROM ac
                   WHERE resource <> '' GROUP BY 1, 2) GROUP BY 1)
            SELECT per.activity, n, cases, resources, wait_sum, wait_median, pv.top, nx.top, tr.top
            FROM per JOIN pv USING (activity) JOIN nx USING (activity) LEFT JOIN tr USING (activity)""").fetchall()
        case_count = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        return {"rows": rows, "weeks": weeks, "total_wait": float(total_wait), "case_count": case_count}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows, weeks, total_wait = data.pop("rows"), data.pop("weeks"), data.pop("total_wait")
    eligible = [r for r in rows if r[2] >= MIN_CASES]
    busiest = max((r[1] / weeks for r in eligible), default=0.0)
    items = []
    for activity, n, cases, resources, wait_sum, wait_median, top_prev, top_next, top_resource in eligible:
        per_week = n / weeks
        volume = math.log1p(per_week) / math.log1p(busiest) if busiest > 0 else 0.0
        routine = (top_prev / n + top_next / n) / 2
        time_share = float(wait_sum) / total_wait if total_wait > 0 else 0.0
        repeats = n / cases
        score = 100 * (WEIGHTS["volume"] * volume + WEIGHTS["routine"] * routine + WEIGHTS["time"] * time_share)
        items.append({
            "activity": activity, "score": round(score, 1),
            "components": {"volume": round(volume, 3), "routine": round(routine, 3), "time": round(time_share, 3)},
            "occurrences": n, "cases": cases, "case_share": round(cases / data["case_count"], 3) if data["case_count"] else 0.0,
            "per_week": round(per_week, 2), "repeats_per_case": round(repeats, 2),
            "same_predecessor_share": round(top_prev / n, 3), "same_successor_share": round(top_next / n, 3),
            "routing_pattern": _pattern(routine, repeats),
            "median_wait_before_seconds": round(float(wait_median), 1) if wait_median is not None else None,
            "wait_before_hours_per_week": round(float(wait_sum) / 3600 / weeks, 2),
            "resources": resources,
            "top_resource_share": round(top_resource / n, 3) if top_resource else None,
        })
    items.sort(key=lambda item: (-item["score"], item["activity"]))
    return {"format": FORMAT, **{k: v for k, v in data.items() if k not in {"case_count"}},
            "cases": data["case_count"], "weeks_observed": round(weeks, 2), "weights": WEIGHTS,
            "min_cases": MIN_CASES, "activities_below_min_cases": len(rows) - len(eligible),
            "candidates": items[:limit],
            "routing_patterns": {"fixed": "same neighbours in at least 80% of occurrences and rarely repeated: rules may cover it",
                                 "mostly_fixed": "same neighbours in 60-80%: rules with exceptions",
                                 "variable": "routing varies: judgement is involved; an AI agent with human review may fit"},
            "caveat": ("A ranking of evidence in this log, not a recommendation. Waiting is the gap since the "
                       "previous event, not working time. Nothing here estimates savings or authorizes automation.")}


__all__ = ["candidates", "FORMAT", "WEIGHTS"]
