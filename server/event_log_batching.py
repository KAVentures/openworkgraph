"""Detect batch processing in a saved event log (R14 calibration).

Some work is done in batches: a clerk approves twenty invoices at once, a job posts every
order at 18:00. Within one activity, events by the same resource that follow each other
within ``gap_seconds`` form a run; a run of at least ``min_batch_size`` events is a batch.
A batch whose events share one timestamp is *simultaneous*, otherwise *sequential* (one
after another in quick succession).

For each activity the report gives the share of its events in batches, the number and
sizes of batches, and how long cases waited from their previous event to a batched event
compared with an unbatched one, a rough sign of time spent waiting to be batched. It
proposes a simulation batch setting (median batch size, 90th-percentile wait) for review;
nothing is applied to a simulation automatically.

Batches are inferred from timestamps and resources. They are not proof that work was
deliberately batched, and resources are pseudonymous labels, never ratings of people.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_ACTIVITY_ROWS = 100


class BatchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gap_seconds: StrictInt = Field(default=60, ge=0, le=86_400)
    min_batch_size: StrictInt = Field(default=3, ge=2, le=1000)
    by_resource: StrictBool = True
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


def _parse(spec: Any) -> BatchSpec:
    try:
        parsed = BatchSpec.model_validate(spec or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "batch settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def batching(log_id: str, spec: Any = None) -> dict[str, Any]:
    settings = _parse(spec)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        resource = "coalesce(resource, '')" if settings.by_resource else "''"
        # One row per event (an event linked to several cases counts once), with its case's previous event time.
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE bt_events AS
            SELECT event_id, any_value(activity) AS activity, any_value({resource}) AS who, any_value(ts) AS ts,
                   max(prev_ts) AS prev_ts
            FROM (SELECT event_id, activity, resource, ts,
                         lag(ts) OVER (PARTITION BY case_id ORDER BY ts, event_id) AS prev_ts FROM ce)
            GROUP BY event_id""")
        conn.execute("""CREATE OR REPLACE TEMP TABLE bt_runs AS
            SELECT *, sum(new_run) OVER (PARTITION BY activity, who ORDER BY ts, event_id) AS run_id FROM (
              SELECT *, CASE WHEN lag(ts) OVER (PARTITION BY activity, who ORDER BY ts, event_id) IS NULL
                              OR epoch(ts - lag(ts) OVER (PARTITION BY activity, who ORDER BY ts, event_id)) > ?
                        THEN 1 ELSE 0 END AS new_run
              FROM bt_events)""", [settings.gap_seconds])
        conn.execute("""CREATE OR REPLACE TEMP TABLE bt_batches AS
            SELECT activity, who, run_id, count(*) AS size, min(ts) = max(ts) AS simultaneous,
                   epoch(max(ts) - min(ts)) AS span FROM bt_runs GROUP BY activity, who, run_id""")
        conn.execute("""CREATE OR REPLACE TEMP TABLE bt_flag AS
            SELECT r.*, b.size, b.size >= ? AS batched, b.simultaneous,
                   CASE WHEN r.prev_ts IS NOT NULL THEN epoch(r.ts - r.prev_ts) END AS wait
            FROM bt_runs r JOIN bt_batches b USING (activity, who, run_id)""", [settings.min_batch_size])
        rows = conn.execute("""SELECT activity, count(*) AS events,
              count(*) FILTER (WHERE batched) AS batched_events,
              count(DISTINCT (who, run_id)) FILTER (WHERE batched) AS batches,
              count(DISTINCT (who, run_id)) FILTER (WHERE batched AND simultaneous) AS simultaneous_batches,
              quantile_cont(wait, 0.5) FILTER (WHERE batched) AS wait_batched,
              quantile_cont(wait, 0.9) FILTER (WHERE batched) AS wait_batched_p90,
              quantile_cont(wait, 0.5) FILTER (WHERE NOT batched) AS wait_unbatched,
              count(DISTINCT who) FILTER (WHERE batched) AS resources
            FROM bt_flag GROUP BY activity ORDER BY batched_events DESC, activity""").fetchall()
        sizes = {a: (n, p50, p90, mx, span) for a, n, p50, p90, mx, span in conn.execute(
            """SELECT activity, count(*), quantile_cont(size, 0.5), quantile_cont(size, 0.9), max(size),
                      quantile_cont(span, 0.5)
               FROM bt_batches WHERE size >= ? GROUP BY activity""", [settings.min_batch_size]).fetchall()}
        total_events = conn.execute("SELECT count(*) FROM bt_events").fetchone()[0]
        has_resources = conn.execute("SELECT count(*) FILTER (WHERE resource IS NOT NULL AND resource <> '') FROM ce").fetchone()[0] > 0
        activities = []
        for activity, events, batched, batches, simultaneous, wb, wb90, wu, resources in rows:
            size = sizes.get(activity)
            item = {"activity": activity, "events": events, "batched_events": batched,
                    "batched_share": round(batched / events, 4) if events else 0.0,
                    "batches": batches, "simultaneous_batches": simultaneous,
                    "sequential_batches": batches - simultaneous, "resources_batching": resources,
                    "batch_size": None if size is None else {"median": size[1], "p90": size[2], "max": size[3]},
                    "median_batch_span_seconds": None if size is None else size[4],
                    "median_wait_before_batched_seconds": wb, "median_wait_before_unbatched_seconds": wu,
                    "simulation_batch_proposal": None}
            if size is not None and batched:
                proposal = {"size": max(settings.min_batch_size, int(round(size[1])))}
                if wb90 is not None and wb90 > 0:
                    proposal["max_wait_minutes"] = round(max(1.0, wb90 / 60), 1)
                item["simulation_batch_proposal"] = proposal
            activities.append(item)
        return {"settings": settings.model_dump(exclude={"cohort", "case_type"}),
                "cohort": cohorts.describe(cohorts.parse_cohort(settings.cohort)),
                "events": total_events, "batched_events": sum(a["batched_events"] for a in activities),
                "activities": activities[:MAX_ACTIVITY_ROWS], "activities_truncated": len(activities) > MAX_ACTIVITY_ROWS,
                "resources_recorded": has_resources,
                "grouping": "activity and resource" if settings.by_resource else "activity only",
                "proposals_applied": False,
                "interpretation": ("Batches are inferred from timestamps and resources: events of one activity by one "
                                   "resource within the gap. They are not proof that work was deliberately batched. "
                                   "Wait is the time since the case's previous event. Simulation proposals are for "
                                   "review and are never applied automatically. Resources are pseudonymous labels, "
                                   "not ratings of people.")}
    return workspace._query(log_id, settings.case_type, run)


__all__ = ["batching"]
