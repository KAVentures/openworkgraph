"""Processing and waiting time per activity (roadmap #326 H1).

Like Disco, ProM or Celonis with start/complete lifecycle data, an *activity
instance* has a start and an end:

- ``lifecycle`` mode pairs, per case and activity, the k-th ``start`` event with
  the k-th ``complete`` event of a lifecycle attribute (XES
  ``lifecycle:transition`` by default, or a CSV column of that name). A
  ``complete`` without a start, or an event with no lifecycle value, is an
  instant whose processing time is unknown. Other lifecycle values (schedule,
  suspend, …) are counted and ignored.
- ``end_attribute`` mode reads an end time with an explicit UTC offset from an
  event attribute; the event's own time is the start.

Processing time is end − start. Waiting time before an instance is the gap
between the latest end of the case's earlier instances and this start (zero
when they overlap). Flow efficiency is the share of a case's elapsed time
covered by at least one instance. All values are recorded timestamps: waiting
includes off-hours and unrecorded work, and nothing is attributed to a person.
"""
from __future__ import annotations

import re
from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_ACTIVITIES = 100
MAX_TRANSITIONS = 25
_ATTRIBUTE = re.compile(r"^[^\"\\\x00-\x1f]{1,120}$")
_OFFSET = r"([zZ]|[+-][0-9]{2}:?[0-9]{2})$"
MODES = ("auto", "lifecycle", "end_attribute")


def _path(name: str) -> str:
    if not _ATTRIBUTE.match(name or ""):
        raise workspace.EventLogWorkspaceError("Attribute names are 1–120 characters without quotes or backslashes")
    return '$."' + name + '"'


def waiting_time(log_id: str, case_type: str | None = None, *, mode: str = "auto",
                 lifecycle_attribute: str = "lifecycle:transition", end_attribute: str = "",
                 cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if mode not in MODES:
        raise workspace.EventLogWorkspaceError("mode must be auto, lifecycle or end_attribute")
    lifecycle_path = _path(lifecycle_attribute)
    end_path = _path(end_attribute) if end_attribute else None
    if mode == "end_attribute" and not end_path:
        raise workspace.EventLogWorkspaceError("Name the attribute that holds each event's end time")
    chosen = cohorts.parse_cohort(cohort)

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        end_text = "json_extract_string(attributes, ?)" if end_path else "NULL::VARCHAR"
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE lc AS SELECT case_id, activity, ts, event_id,
            lower(trim(coalesce(json_extract_string(attributes, ?), ''))) AS tr, {end_text} AS end_text FROM ce""",
                     [lifecycle_path] + ([end_path] if end_path else []))
        events, lifecycle_events = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE tr IN ('start', 'complete')) FROM lc").fetchone()
        used = mode
        if mode == "auto":
            used = "lifecycle" if lifecycle_events else ("end_attribute" if end_path else None)
        base = {"settings": {"mode": mode, "lifecycle_attribute": lifecycle_attribute,
                             "end_attribute": end_attribute or None},
                "cohort": cohorts.describe(chosen), "mode_used": used}
        if used is None or (used == "lifecycle" and not lifecycle_events):
            keys = [row[0] for row in conn.execute("""SELECT k, count(*) AS n FROM (
                SELECT unnest(json_keys(attributes)) AS k FROM (SELECT attributes FROM ce LIMIT 10000))
                GROUP BY k ORDER BY n DESC, k LIMIT 30""").fetchall()]
            return {**base, "available": False, "events": events, "attribute_names": keys,
                    "reason": (f"No start or complete values were found in the {lifecycle_attribute!r} attribute. "
                               "Choose the attribute holding each event's lifecycle, or one holding its end time "
                               "with a UTC offset.")}
        if used == "lifecycle":
            conn.execute("""CREATE OR REPLACE TEMP TABLE inst AS
                WITH s AS (SELECT case_id, activity, ts, row_number() OVER (
                        PARTITION BY case_id, activity ORDER BY ts, event_id) AS k FROM lc WHERE tr = 'start'),
                     c AS (SELECT case_id, activity, ts, row_number() OVER (
                        PARTITION BY case_id, activity ORDER BY ts, event_id) AS k FROM lc WHERE tr = 'complete'),
                     paired AS (SELECT coalesce(s.case_id, c.case_id) AS case_id,
                        coalesce(s.activity, c.activity) AS activity, s.ts AS st, c.ts AS en FROM s
                        FULL JOIN c ON s.case_id = c.case_id AND s.activity = c.activity AND s.k = c.k)
                SELECT case_id, activity,
                  CASE WHEN st IS NULL OR en < st THEN en ELSE st END AS st, en,
                  CASE WHEN st IS NULL OR en < st THEN NULL ELSE epoch(en - st) END AS proc,
                  CASE WHEN st IS NULL THEN 'complete_only' WHEN en < st THEN 'invalid_pair' ELSE 'paired' END AS kind
                FROM paired WHERE en IS NOT NULL
                UNION ALL SELECT case_id, activity, ts, ts, NULL, 'no_lifecycle' FROM lc WHERE tr = ''""")
            # Starts without a k-th complete are still running (or lost their completion).
            open_starts, unmatched_starts = conn.execute("""SELECT count(*), coalesce(sum(d), 0) FROM (
                SELECT count(*) FILTER (WHERE tr = 'start') - count(*) FILTER (WHERE tr = 'complete') AS d
                FROM lc GROUP BY case_id, activity) WHERE d > 0""").fetchone()
            ignored = conn.execute("SELECT count(*) FROM lc WHERE tr NOT IN ('', 'start', 'complete')").fetchone()[0]
        else:
            conn.execute(f"""CREATE OR REPLACE TEMP TABLE inst AS
                WITH parsed AS (SELECT case_id, activity, ts AS st,
                    CASE WHEN regexp_matches(trim(coalesce(end_text, '')), '{_OFFSET}')
                         THEN timezone('UTC', TRY_CAST(trim(end_text) AS TIMESTAMPTZ)) END AS en FROM lc)
                SELECT case_id, activity, st, CASE WHEN en >= st THEN en ELSE st END AS en,
                  CASE WHEN en >= st THEN epoch(en - st) END AS proc,
                  CASE WHEN en >= st THEN 'paired' WHEN en IS NULL THEN 'no_end_time' ELSE 'invalid_pair' END AS kind
                FROM parsed""")
            open_starts = unmatched_starts = ignored = 0
        conn.execute("""CREATE OR REPLACE TEMP TABLE seq AS SELECT *,
              max(en) OVER prior_instances AS prev_end,
              arg_max(activity, struct_pack(ends_at := en, starts_at := st, label := activity))
                OVER prior_instances AS prev_activity
            FROM inst WINDOW prior_instances AS (PARTITION BY case_id ORDER BY st, en, activity
                            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)""")
        conn.execute("""CREATE OR REPLACE TEMP TABLE seq2 AS SELECT *,
              CASE WHEN prev_end IS NULL THEN NULL ELSE greatest(0, epoch(st - prev_end)) END AS wait,
              CASE WHEN proc IS NULL THEN 0 ELSE greatest(0, epoch(en - greatest(st, coalesce(prev_end, st)))) END AS busy
            FROM seq""")
        kinds = dict(conn.execute("SELECT kind, count(*) FROM inst GROUP BY kind").fetchall())
        total_wait = conn.execute("SELECT coalesce(sum(wait), 0) FROM seq2").fetchone()[0] or 0
        activities = conn.execute(f"""SELECT activity, count(*), count(DISTINCT case_id), count(proc),
              quantile_cont(proc, 0.5), quantile_cont(proc, 0.9), avg(proc),
              count(wait), quantile_cont(wait, 0.5), quantile_cont(wait, 0.9), avg(wait), coalesce(sum(wait), 0)
            FROM seq2 GROUP BY activity ORDER BY coalesce(sum(wait), 0) DESC, activity LIMIT {MAX_ACTIVITIES}""").fetchall()
        transitions = conn.execute(f"""SELECT prev_activity, activity, count(*), quantile_cont(wait, 0.5), sum(wait)
            FROM seq2 WHERE wait IS NOT NULL GROUP BY prev_activity, activity
            ORDER BY sum(wait) DESC, prev_activity, activity LIMIT {MAX_TRANSITIONS}""").fetchall()
        flow = conn.execute("""SELECT count(*), quantile_cont(eff, 0.5), quantile_cont(eff, 0.1),
              quantile_cont(eff, 0.9), sum(busy), sum(dur) FROM (
              SELECT sum(busy) AS busy, epoch(max(en) - min(st)) AS dur, sum(busy) / epoch(max(en) - min(st)) AS eff
              FROM seq2 GROUP BY case_id HAVING count(proc) > 0 AND max(en) > min(st))""").fetchone()

        def stats(median, p90, mean):
            return {"median_seconds": median, "p90_seconds": p90, "mean_seconds": mean}
        return {
            **base, "available": True, "events": events,
            "instances": {"total": sum(kinds.values()), "with_processing_time": kinds.get("paired", 0),
                          "complete_without_start": kinds.get("complete_only", 0),
                          "without_lifecycle_or_end": kinds.get("no_lifecycle", 0) + kinds.get("no_end_time", 0),
                          "end_before_start": kinds.get("invalid_pair", 0),
                          "unmatched_starts": int(unmatched_starts), "activities_with_open_starts": int(open_starts),
                          "ignored_lifecycle_events": int(ignored)},
            "activities": [{
                "activity": a, "instances": n, "cases": cases, "instances_with_processing_time": known,
                "processing": stats(p50, p90, mean), "waiting_before": {**stats(w50, w90, wmean), "instances": waited,
                                                                       "total_seconds": wsum},
                "share_of_all_waiting": wsum / total_wait if total_wait else None,
            } for a, n, cases, known, p50, p90, mean, waited, w50, w90, wmean, wsum in activities],
            "waiting_hotspots": [{"from": s, "to": d, "count": n, "median_waiting_seconds": med,
                                  "total_waiting_seconds": total,
                                  "share_of_all_waiting": total / total_wait if total_wait else None}
                                 for s, d, n, med, total in transitions],
            "flow_efficiency": {"cases": flow[0], "median": flow[1], "p10": flow[2], "p90": flow[3],
                                "busy_seconds": flow[4], "elapsed_seconds": flow[5]},
            "interpretation": ("Processing is the time between an activity instance's recorded start and end; waiting "
                               "is the time before an instance starts after the case's earlier work ended. Both come "
                               "from recorded timestamps, so waiting includes nights, weekends and unrecorded work. "
                               "Hotspot sources mark the earlier instance with the latest recorded end; tied ends "
                               "use the latest start, then the greatest activity label in lexical order as a representative. "
                               "A timestamp boundary does not establish a causal handoff. Nothing is attributed to a person."),
        }
    return workspace._query(log_id, case_type, run)


__all__ = ["waiting_time", "MODES"]
