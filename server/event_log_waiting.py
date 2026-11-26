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
from dataclasses import dataclass
from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_ACTIVITIES = 100
MAX_TRANSITIONS = 25
_ATTRIBUTE = re.compile(r"^[^\"\\\x00-\x1f]{1,120}$")
_OFFSET = r"([zZ]|[+-][0-9]{2}:?[0-9]{2})$"
MODES = ("auto", "lifecycle", "end_attribute")


@dataclass(frozen=True)
class _HotspotPage:
    source: str
    destination: str
    after: str | None
    limit: int
    expected_evidence_sha256: str | None = None


def _hotspot_cases(conn, page: _HotspotPage, clock: dict[str, Any] | None) -> dict[str, Any]:
    table = 'working_seq' if clock else 'seq2'
    scheduled = ', sum(working_wait) AS scheduled, max(working_wait) AS longest_scheduled' if clock else ''
    conn.execute(f"""CREATE TEMP TABLE waiting_case_evidence AS
        SELECT case_id, count(*) AS instances, sum(wait) AS waiting, max(wait) AS longest{scheduled}
        FROM {table} WHERE wait IS NOT NULL AND prev_activity = ? AND activity = ? GROUP BY case_id""",
                 [page.source, page.destination])
    scheduled_total = ', coalesce(sum(scheduled), 0)' if clock else ''
    total, occurrences, waiting_seconds, *working = conn.execute(
        f"SELECT count(*), coalesce(sum(instances), 0), coalesce(sum(waiting), 0){scheduled_total} FROM waiting_case_evidence"
    ).fetchone()
    where, params = (" WHERE case_id > ?", [page.after]) if page.after is not None else ("", [])
    rows = conn.execute("SELECT * FROM waiting_case_evidence" + where + " ORDER BY case_id LIMIT ?",
                        [*params, page.limit + 1]).fetchall()
    return {
        "from": page.source, "to": page.destination,
        "total_cases": total, "total_instances": occurrences, "total_waiting_seconds": waiting_seconds,
        **({"total_scheduled_waiting_seconds": working[0], "evidence_sha256": clock['evidence_sha256']} if clock else {}),
        "next_after": rows[page.limit - 1][0] if len(rows) > page.limit else None,
        "cases": [{"case_id": key, "instances": count, "waiting_seconds": waiting, "longest_wait_seconds": longest,
                   **({"scheduled_waiting_seconds": schedule[0], "longest_scheduled_wait_seconds": schedule[1]} if clock else {})}
                  for key, count, waiting, longest, *schedule in rows[:page.limit]],
    }


def _path(name: str) -> str:
    if not isinstance(name, str) or not _ATTRIBUTE.fullmatch(name):
        raise workspace.EventLogWorkspaceError("Attribute names are 1–120 characters without quotes or backslashes")
    return '$."' + name + '"'


def waiting_time(log_id: str, case_type: str | None = None, *, mode: str = "auto",
                 lifecycle_attribute: str = "lifecycle:transition", end_attribute: str = "",
                 cohort: dict[str, Any] | None = None, working_calendar: dict[str, Any] | None = None,
                 _hotspot: _HotspotPage | None = None) -> dict[str, Any]:
    if mode not in MODES:
        raise workspace.EventLogWorkspaceError("mode must be auto, lifecycle or end_attribute")
    lifecycle_path = _path(lifecycle_attribute)
    end_path = _path(end_attribute) if end_attribute else None
    if mode == "end_attribute" and not end_path:
        raise workspace.EventLogWorkspaceError("Name the attribute that holds each event's end time")
    chosen = cohorts.parse_cohort(cohort)
    from . import event_log_calendar_metrics as calendars
    calendar = calendars.parse(working_calendar) if working_calendar is not None else None

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
            # An earlier k-th completion is invalid and does not close its start.
            open_starts, unmatched_starts = conn.execute("""WITH s AS (
                SELECT case_id, activity, ts, row_number() OVER (
                    PARTITION BY case_id, activity ORDER BY ts, event_id) AS k FROM lc WHERE tr = 'start'),
                c AS (SELECT case_id, activity, ts, row_number() OVER (
                    PARTITION BY case_id, activity ORDER BY ts, event_id) AS k FROM lc WHERE tr = 'complete'),
                unmatched AS (SELECT s.case_id, s.activity FROM s LEFT JOIN c
                    ON s.case_id = c.case_id AND s.activity = c.activity AND s.k = c.k
                    WHERE c.ts IS NULL OR c.ts < s.ts)
                SELECT count(*), coalesce(sum(n), 0) FROM (
                    SELECT count(*) AS n FROM unmatched GROUP BY case_id, activity)""").fetchone()
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
        clock = calendars.prepare(conn, calendar) if calendar is not None else None
        if clock is not None:
            clock['case_filter_sha256'] = calendars.digest(base['cohort'])
            clock['instance_settings_sha256'] = calendars.digest({**base['settings'], 'mode_used': used})
            if clock['available']:
                clock['evidence_sha256'] = calendars.evidence_digest(conn, log_id=log_id,
                    case_notion=workspace._case_type(conn, log_id, case_type), clock=clock)
        if _hotspot is not None:
            if clock is not None and (not clock['available'] or _hotspot.expected_evidence_sha256 != clock['evidence_sha256']):
                raise workspace.EventLogWorkspaceError("the scheduled waiting intervals changed; run the analysis again")
            return {**base, "available": True, **_hotspot_cases(conn, _hotspot, clock)}
        calendar_result = calendars.summarize(conn, clock) if clock is not None else None
        kinds = dict(conn.execute("SELECT kind, count(*) FROM inst GROUP BY kind").fetchall())
        total_wait = conn.execute("SELECT coalesce(sum(wait), 0) FROM seq2").fetchone()[0] or 0
        # Open lifecycle starts are absent from inst; include them in both coverage denominators.
        # Their waiting gap is not measured by seq2, even though their start timestamp is known.
        # Otherwise an activity with one completed pair and many unfinished starts appears 100% observed.
        open_by_activity = {}
        if used == 'lifecycle':
            # Use the same k-th pairing rule as unmatched_starts, including
            # completions timestamped before their corresponding start.
            open_by_activity = dict(conn.execute("""WITH s AS (
                SELECT case_id, activity, ts, row_number() OVER (
                    PARTITION BY case_id, activity ORDER BY ts, event_id) AS k FROM lc WHERE tr = 'start'),
                c AS (SELECT case_id, activity, ts, row_number() OVER (
                    PARTITION BY case_id, activity ORDER BY ts, event_id) AS k FROM lc WHERE tr = 'complete')
                SELECT s.activity, count(*) FROM s LEFT JOIN c
                    ON s.case_id = c.case_id AND s.activity = c.activity AND s.k = c.k
                WHERE c.ts IS NULL OR c.ts < s.ts GROUP BY s.activity""").fetchall())
        # Invalid start/completion pairs already contribute an inst row. They are
        # also unmatched starts, so subtract them from coverage eligibility once.
        # Only lifecycle invalid pairs are also counted as unmatched starts.
        # In end_attribute mode each invalid end belongs to one distinct event
        # and must remain in the eligible observation denominator.
        invalid_by_activity = (dict(conn.execute(
            "SELECT activity, count(*) FROM inst WHERE kind = 'invalid_pair' GROUP BY activity"
        ).fetchall()) if used == 'lifecycle' else {})
        activities = conn.execute(f"""SELECT activity, count(*), count(DISTINCT case_id), count(proc),
              quantile_cont(proc, 0.5), quantile_cont(proc, 0.9), avg(proc),
              count(wait), quantile_cont(wait, 0.5), quantile_cont(wait, 0.9), avg(wait), coalesce(sum(wait), 0),
              stddev_pop(proc), stddev_pop(wait), quantile_cont(wait, 0.1)
            FROM seq2 GROUP BY activity ORDER BY coalesce(sum(wait), 0) DESC, activity LIMIT {MAX_ACTIVITIES}""").fetchall()
        # A lifecycle activity with only unfinished starts has no row in inst/seq2.
        # Keep it visible instead of silently hiding a completely unobserved activity.
        # Count all cases contributing lifecycle evidence, including cases whose
        # only evidence for this activity is an unfinished start. Otherwise the
        # displayed case denominator understates the population being measured.
        lifecycle_case_counts = {}
        if used == 'lifecycle':
            lifecycle_case_counts = dict(conn.execute("""SELECT activity, count(DISTINCT case_id)
                FROM lc WHERE tr IN ('start', 'complete') OR tr = ''
                GROUP BY activity""").fetchall())
        # Keep the documented response cap even when the log has many activities
        # with starts but no completions. Rank those missing-duration activities
        # deterministically after the observed waiting-time hotspots.
        # Count the complete observed population in SQL instead of materializing
        # every distinct label in Python. Only the bounded result is displayed.
        observed_activity_total = conn.execute(
            "SELECT count(DISTINCT activity) FROM seq2"
        ).fetchone()[0]
        # A start-only label is open-only only when it is absent from *all*
        # observed instances, including those omitted by the top-N query.
        observed_open_labels = set()
        if open_by_activity:
            observed_open_labels = {row[0] for row in conn.execute(
                "SELECT DISTINCT activity FROM seq2 WHERE activity IN (SELECT unnest(?::VARCHAR[]))",
                [list(open_by_activity)],
            ).fetchall()}
        open_only = sorted(set(open_by_activity) - observed_open_labels)
        # Surface truncation explicitly: a top-100 table is not a complete cohort.
        activity_total = observed_activity_total + len(open_only)
        remaining = max(0, MAX_ACTIVITIES - len(activities))
        if remaining and open_only:
            activities = list(activities) + [
                (name, 0, int(lifecycle_case_counts.get(name, 0)), 0, None, None, None,
                 0, None, None, None, 0, None, None, None)
                for name in open_only[:remaining]
            ]
        # The hotspot list is capped; expose how many distinct observed transitions
        # were excluded so operators do not mistake a top-N view for the full cohort.
        transition_total = conn.execute("""SELECT count(*) FROM (
            SELECT prev_activity, activity FROM seq2 WHERE wait IS NOT NULL
            GROUP BY prev_activity, activity)""").fetchone()[0]
        transitions = conn.execute(f"""SELECT prev_activity, activity, count(*), quantile_cont(wait, 0.5), sum(wait)
            FROM seq2 WHERE wait IS NOT NULL GROUP BY prev_activity, activity
            ORDER BY sum(wait) DESC, prev_activity, activity LIMIT {MAX_TRANSITIONS}""").fetchall()
        flow = conn.execute("""SELECT count(*), quantile_cont(eff, 0.5), quantile_cont(eff, 0.1),
              quantile_cont(eff, 0.9), sum(busy), sum(dur) FROM (
              SELECT sum(busy) AS busy, epoch(max(en) - min(st)) AS dur, sum(busy) / epoch(max(en) - min(st)) AS eff
              FROM seq2 GROUP BY case_id HAVING count(proc) > 0 AND max(en) > min(st))""").fetchone()

        # Cohort-wide coverage must include activities omitted from the bounded table.
        # Invalid lifecycle pairs already appear in inst and unmatched_starts;
        # subtract them once, unlike invalid end-attribute observations.
        cohort_instances = conn.execute(
            "SELECT count(*), count(proc), count(wait) FROM seq2"
        ).fetchone()
        cohort_eligible = (cohort_instances[0] + int(unmatched_starts)
                           - (kinds.get("invalid_pair", 0) if used == "lifecycle" else 0))
        cohort_coverage = {
            "eligible": cohort_eligible,
            "processing_observed": cohort_instances[1],
            "waiting_observed": cohort_instances[2],
            "processing_share": (cohort_instances[1] / cohort_eligible if cohort_eligible else None),
            "waiting_share": (cohort_instances[2] / cohort_eligible if cohort_eligible else None),
        }

        def stats(median, p90, mean):
            return {"median_seconds": median, "p90_seconds": p90, "mean_seconds": mean}
        return {
            **base, "available": True, "events": events,
            "cohort_observation_coverage": cohort_coverage,
            "activity_result_limit": MAX_ACTIVITIES,
            "activity_total": activity_total,
            "activities_omitted": max(0, activity_total - len(activities)),
            **({"working_calendar":calendar_result} if calendar_result is not None else {}),
            "instances": {"total": sum(kinds.values()), "with_processing_time": kinds.get("paired", 0),
                          "complete_without_start": kinds.get("complete_only", 0),
                          "without_lifecycle_or_end": kinds.get("no_lifecycle", 0) + kinds.get("no_end_time", 0),
                          "end_before_start": kinds.get("invalid_pair", 0),
                          "unmatched_starts": int(unmatched_starts), "activities_with_open_starts": int(open_starts),
                          "ignored_lifecycle_events": int(ignored)},
            "activities": [{
                "activity": a, "instances": n, "cases": lifecycle_case_counts.get(a, cases), "instances_with_processing_time": known,
                "processing_observation_share": known / (n + open_by_activity.get(a, 0) - invalid_by_activity.get(a, 0)) if n + open_by_activity.get(a, 0) - invalid_by_activity.get(a, 0) else None,
                "open_starts": int(open_by_activity.get(a, 0)),
                "processing_observations": {"observed": known, "eligible": n + open_by_activity.get(a, 0) - invalid_by_activity.get(a, 0)},
                "waiting_observations": {"observed": waited, "eligible": n + open_by_activity.get(a, 0) - invalid_by_activity.get(a, 0)},
                "waiting_observation_share": waited / (n + open_by_activity.get(a, 0) - invalid_by_activity.get(a, 0)) if n + open_by_activity.get(a, 0) - invalid_by_activity.get(a, 0) else None,
                "processing": {**stats(p50, p90, mean), "population_stddev_seconds": pstd},
                "waiting_before": {**stats(w50, w90, wmean), "p10_seconds": w10,
                                   "population_stddev_seconds": wstd, "instances": waited,
                                                                       "total_seconds": wsum},
                "share_of_all_waiting": wsum / total_wait if total_wait else None,
            } for a, n, cases, known, p50, p90, mean, waited, w50, w90, wmean, wsum, pstd, wstd, w10 in activities],
            "waiting_hotspot_result_limit": MAX_TRANSITIONS,
            "waiting_hotspot_total": transition_total,
            "waiting_hotspots_omitted": max(0, transition_total - len(transitions)),
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


def waiting_hotspot_cases(log_id: str, case_type: str | None = None, *, source: str, destination: str,
                          after: str | None = None, limit: int = 50, mode: str = "auto",
                          lifecycle_attribute: str = "lifecycle:transition", end_attribute: str = "",
                          cohort: dict[str, Any] | None = None, working_calendar: dict[str, Any] | None = None,
                          expected_evidence_sha256: str | None = None) -> dict[str, Any]:
    if any(not isinstance(value, str) or not value or len(value) > 200 for value in (source, destination)):
        raise workspace.EventLogWorkspaceError("choose the exact source and destination activity labels")
    if after is not None and (not isinstance(after, str) or not after or len(after) > 200):
        raise workspace.EventLogWorkspaceError("invalid case key cursor")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise workspace.EventLogWorkspaceError("page size must be a whole number from 1 to 100")
    if working_calendar is not None and (not isinstance(expected_evidence_sha256, str) or
                                         not re.fullmatch(r"[0-9a-f]{64}", expected_evidence_sha256)):
        raise workspace.EventLogWorkspaceError("choose the evidence digest from the scheduled waiting report")
    return waiting_time(log_id, case_type, mode=mode, lifecycle_attribute=lifecycle_attribute, end_attribute=end_attribute,
                        cohort=cohort, working_calendar=working_calendar,
                        _hotspot=_HotspotPage(source, destination, after, limit, expected_evidence_sha256))


__all__ = ["waiting_time", "waiting_hotspot_cases", "MODES"]
