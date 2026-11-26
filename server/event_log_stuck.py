"""Open cases that have been idle much longer than usual after their latest activity.

For every activity, the log's history gives how long cases waited after it before their
next event. An open case (its latest activity is not an end activity) is **stuck** when the
time since its latest event, measured to the end of the log, exceeds the chosen percentile
(default the 90th) of those waits. A percentile is used only with at least ``min_support``
observed waits after that activity; cases at an activity with less history are counted as
not judged. The case filter selects the open cases to check. Wait history and the cutoff use
the selected case notion.

Without end activities, every case whose latest activity is ever followed by another event
elsewhere in the log is treated as open. A stuck case is a prompt to look, not a finding that
anyone did anything wrong; the export may simply end before the next step.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace


class StuckSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    end_activities: list[str] = Field(default_factory=list, max_length=20)
    percentile: StrictFloat | StrictInt = Field(default=0.9, ge=0.5, le=0.99)
    min_support: StrictInt = Field(default=20, ge=5, le=10_000)
    limit: StrictInt = Field(default=50, ge=1, le=500)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


def _parse(spec: Any) -> StuckSpec:
    try:
        parsed = StuckSpec.model_validate({} if spec is None else spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "stuck-case settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    if any(not isinstance(a, str) or not a.strip() or len(a) > 200 for a in parsed.end_activities):
        raise workspace.EventLogWorkspaceError("end activities must be non-empty names")
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def stuck_cases(log_id: str, spec: Any = None, *, all_cases: bool = False) -> dict[str, Any]:
    """``all_cases`` returns every stuck case (for the CSV worklist) instead of the first ``limit``."""
    settings = _parse(spec)
    selected = cohorts.parse_cohort(settings.cohort)

    def run(conn):
        as_of = conn.execute("SELECT max(ts) FROM ce").fetchone()[0]
        if as_of is None:
            return None, []
        conn.execute("""CREATE OR REPLACE TEMP TABLE st_gaps AS SELECT activity,
              epoch(lead(ts) OVER (PARTITION BY case_id ORDER BY ts, event_id) - ts) AS gap
            FROM ce""")
        conn.execute("""CREATE OR REPLACE TEMP TABLE st_norm AS SELECT activity, count(gap) AS n,
              quantile_cont(gap, ?) AS threshold, quantile_cont(gap, 0.5) AS median_gap
            FROM st_gaps WHERE gap IS NOT NULL GROUP BY activity""", [float(settings.percentile)])
        cohorts.restrict(conn, selected, log_id)
        ends = settings.end_activities
        follow = "TRUE" if ends else "a.activity IN (SELECT activity FROM st_norm)"
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE st_open AS SELECT a.case_id, a.activity, a.last_ts,
              epoch(?::TIMESTAMP - a.last_ts) AS idle, a.events, a.started
            FROM (SELECT case_id, arg_max(activity, struct_pack(t := ts, e := event_id)) AS activity,
                         max(ts) AS last_ts, min(ts) AS started, count(*) AS events FROM ce GROUP BY case_id) a
            WHERE {'NOT list_contains(?, a.activity)' if ends else 'TRUE'} AND {follow}""",
                     [as_of, ends] if ends else [as_of])
        rows = conn.execute("""SELECT o.case_id, o.activity, o.idle, o.events, epoch(?::TIMESTAMP - o.started),
              n.n, n.threshold, n.median_gap
            FROM st_open o LEFT JOIN st_norm n USING (activity)""", [as_of]).fetchall()
        return as_of, rows
    result = workspace._query(log_id, settings.case_type, lambda conn: {"_run": run(conn)})
    as_of, rows = result.pop("_run")
    judged, unjudged, stuck = 0, 0, []
    by_activity: dict[str, dict[str, Any]] = {}
    for case_id, activity, idle, events, age, n, threshold, median_gap in rows:
        slot = by_activity.setdefault(activity, {"activity": activity, "open_cases": 0, "stuck_cases": 0,
                                                 "usual_wait_seconds": median_gap, "threshold_seconds": threshold,
                                                 "observed_waits": n or 0, "judged": bool(n and n >= settings.min_support)})
        slot["open_cases"] += 1
        if not n or n < settings.min_support or threshold is None:
            unjudged += 1
            continue
        judged += 1
        if idle > threshold:
            slot["stuck_cases"] += 1
            stuck.append({"case_id": case_id, "latest_activity": activity, "idle_seconds": round(idle, 3),
                          "threshold_seconds": round(threshold, 3), "usual_wait_seconds": round(median_gap, 3),
                          "times_usual_threshold": round(idle / threshold, 2) if threshold else None,
                          "events_so_far": events, "age_seconds": round(age, 3)})
    stuck.sort(key=lambda c: (c["times_usual_threshold"] is not None,
                             -(c["times_usual_threshold"] if c["times_usual_threshold"] is not None else c["idle_seconds"]),
                             c["case_id"]))
    return {**result, "settings": settings.model_dump(exclude={"cohort", "case_type"}),
            "cohort": cohorts.describe(selected),
            "as_of": as_of.isoformat() + "Z" if as_of else None,
            "open_cases": len(rows), "judged_open_cases": judged, "not_judged_open_cases": unjudged,
            "stuck_cases": len(stuck), "cases": stuck if all_cases else stuck[:settings.limit],
            "cases_truncated": not all_cases and len(stuck) > settings.limit,
            "by_activity": sorted(by_activity.values(), key=lambda a: (-a["stuck_cases"], -a["open_cases"], a["activity"])),
            "interpretation": ("Open cases idle longer than usual after their latest activity, measured to the end of "
                               "the log. A prompt to look, not a finding that anyone did anything wrong; the export "
                               "may end before the next step.")}


CSV_COLUMNS = ["case_id", "latest_activity", "idle_seconds", "usual_wait_seconds", "stuck_after_seconds",
               "times_stuck_threshold", "events_so_far", "case_age_seconds", "as_of"]


def stuck_cases_csv(log_id: str, spec: Any = None) -> tuple[str, dict[str, Any]]:
    """Every stuck case as a CSV worklist, most overdue first, with formula-like cells neutralized."""
    import csv
    import io
    from .event_log_variant_export import _cell
    result = stuck_cases(log_id, spec, all_cases=True)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for c in result["cases"]:
        writer.writerow([_cell(c["case_id"]), _cell(c["latest_activity"]), c["idle_seconds"], c["usual_wait_seconds"],
                         c["threshold_seconds"], c["times_usual_threshold"], c["events_so_far"], c["age_seconds"],
                         result["as_of"]])
    return out.getvalue(), {"stuck_cases": result["stuck_cases"], "judged_open_cases": result["judged_open_cases"],
                            "not_judged_open_cases": result["not_judged_open_cases"], "as_of": result["as_of"]}


__all__ = ["stuck_cases", "stuck_cases_csv", "CSV_COLUMNS"]
