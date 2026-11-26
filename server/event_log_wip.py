"""Work in progress over time: arrivals, completions and open cases per day or week.

A case *arrives* at its first event. It *completes* at its last event if that event is one of
the given end activities; without end activities, every case completes at its last event,
and only cases whose last event is after the end of the log's history can stay open.
At the end of each bucket the report counts the cases still open.

Little's law checks the picture: over the window, the average number of open cases should
be about the arrival rate times the mean cycle time of completed cases. A large gap usually
means the log starts or ends mid-flow, or that many cases never reach an end activity.

All of this describes the supplied log. It is not a capacity plan or a rating of anyone.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_BUCKETS = 400


class WipSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: Literal["day", "week"] = "day"
    end_activities: list[str] = Field(default_factory=list, max_length=20)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


class WipCaseSpec(WipSpec):
    bucket_start: str = Field(min_length=10, max_length=10)
    group: Literal["arrivals", "completions", "open_at_end"]
    after: str | None = Field(default=None, min_length=1, max_length=200)
    limit: StrictInt = Field(default=50, ge=1, le=100)


_Settings = TypeVar("_Settings", bound=WipSpec)


def _parse(spec: Any, model: type[_Settings]) -> _Settings:
    try:
        parsed = model.model_validate(spec or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "work-in-progress settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    if any(not isinstance(a, str) or not a or len(a) > 200 for a in parsed.end_activities):
        raise workspace.EventLogWorkspaceError("end activities must be non-empty names")
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def _prepare(conn, log_id: str, settings: WipSpec):
    cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
    conn.execute("""CREATE TEMP TABLE wip_cases AS SELECT case_id, min(ts) AS started, max(ts) AS ended,
        list_contains(?, arg_max(activity, struct_pack(t := ts, e := event_id))) OR ? AS done
        FROM ce GROUP BY case_id""", [settings.end_activities, not settings.end_activities])
    first, last = conn.execute("SELECT min(started), max(ended) FROM wip_cases").fetchone()
    if first is None:
        return None
    unit = settings.bucket
    start, final = conn.execute(f"SELECT date_trunc('{unit}', ?::TIMESTAMP), date_trunc('{unit}', ?::TIMESTAMP)",
                                [first, last]).fetchone()
    step = timedelta(days=1 if unit == "day" else 7)
    count = int((final - start) / step) + 1
    if count > MAX_BUCKETS:
        hint = "try weeks or a cohort" if unit == "day" else "narrow it with a cohort, for example a start-date range"
        raise workspace.EventLogWorkspaceError(f"the log spans {count} {unit}s; at most {MAX_BUCKETS} buckets, {hint}")
    return first, last, start, final, step


def work_in_progress(log_id: str, spec: Any = None) -> dict[str, Any]:
    settings = _parse(spec, WipSpec)

    def run(conn):
        window = _prepare(conn, log_id, settings)
        if window is None:
            return {"buckets": [], "cases": 0}
        first, last, start, final, step = window
        unit = settings.bucket
        conn.execute("CREATE OR REPLACE TEMP TABLE wip_b AS SELECT range AS b_start, range + ?::INTERVAL AS b_end "
                     "FROM range(?::TIMESTAMP, ?::TIMESTAMP + ?::INTERVAL, ?::INTERVAL)",
                     [step, start, final, step, step])
        # Grouped counts and running totals: linear in cases, not buckets x cases.
        rows = conn.execute(f"""WITH a AS (SELECT date_trunc('{unit}', started) AS b, count(*) AS n FROM wip_cases GROUP BY 1),
                 c AS (SELECT date_trunc('{unit}', ended) AS b, count(*) AS n FROM wip_cases WHERE done GROUP BY 1)
            SELECT w.b_start, coalesce(a.n, 0) AS arrivals, coalesce(c.n, 0) AS completions,
                   sum(coalesce(a.n, 0) - coalesce(c.n, 0)) OVER (ORDER BY w.b_start) AS open_at_end
            FROM wip_b w LEFT JOIN a ON a.b = w.b_start LEFT JOIN c ON c.b = w.b_start ORDER BY w.b_start""").fetchall()
        cases, completed, never_done, mean_cycle = conn.execute("""SELECT count(*), count(*) FILTER (WHERE done),
              count(*) FILTER (WHERE NOT done), avg(epoch(ended - started)) FILTER (WHERE done) FROM wip_cases""").fetchone()
        window_seconds = max(1.0, (last - first).total_seconds())
        # Time-average open cases from each case's open interval, clipped to the log's span.
        open_seconds = conn.execute("""SELECT sum(epoch(CASE WHEN done THEN ended ELSE ?::TIMESTAMP END - started))
            FROM wip_cases""", [last]).fetchone()[0] or 0.0
        avg_wip = open_seconds / window_seconds
        arrival_rate = cases / window_seconds
        predicted = arrival_rate * mean_cycle if mean_cycle is not None else None
        return {
            "settings": settings.model_dump(exclude={"cohort", "case_type"}),
            "cohort": cohorts.describe(cohorts.parse_cohort(settings.cohort)),
            "cases": cases, "completed_cases": completed, "never_completed_cases": never_done,
            "first_event_at": first.isoformat() + "Z", "last_event_at": last.isoformat() + "Z",
            "buckets": [{"start": b.isoformat() + "Z", "arrivals": a, "completions": c, "open_at_end": int(o)}
                        for b, a, c, o in rows],
            "littles_law": {
                "average_open_cases": round(avg_wip, 3),
                "arrival_rate_per_day": round(arrival_rate * 86400, 4),
                "mean_cycle_time_seconds": round(mean_cycle, 3) if mean_cycle is not None else None,
                "arrival_rate_times_cycle_time": round(predicted, 3) if predicted is not None else None,
                "ratio": round(avg_wip / predicted, 3) if predicted else None,
                "note": ("Close to 1 when the log covers whole cases in a steady period. Far from 1 usually means "
                         "cases started before or end after the log, or many never reach an end activity."),
            },
            "completion_rule": ("last event is an end activity" if settings.end_activities
                                else "every case completes at its last event"),
            "interpretation": ("Counts of cases in the supplied log over time. Open cases at the end of the log may "
                               "simply be unfinished in the export. Not a capacity plan or a rating of anyone."),
        }
    return workspace._query(log_id, settings.case_type, run)


def bucket_cases(log_id: str, spec: Any) -> dict[str, Any]:
    """Case evidence for a single arrival, completion, or open-at-end count."""
    settings = _parse(spec, WipCaseSpec)
    try:
        day = date.fromisoformat(settings.bucket_start)
        if day.isoformat() != settings.bucket_start or (settings.bucket == "week" and day.weekday() != 0):
            raise ValueError
    except ValueError:
        raise workspace.EventLogWorkspaceError("bucket_start must be a UTC date, starting on Monday for weekly buckets") from None
    boundary = datetime.combine(day, time.min)
    predicate = {
        "arrivals": "started >= b.start AND started < b.stop",
        "completions": "done AND ended >= b.start AND ended < b.stop",
        "open_at_end": "started < b.stop AND (NOT done OR ended >= b.stop)",
    }[settings.group]

    def run(conn):
        window = _prepare(conn, log_id, settings)
        if window is None or not window[2] <= boundary <= window[3]:
            raise workspace.EventLogWorkspaceError("the selected bucket is outside the report's time range")
        step = window[4]
        conn.execute(f"""CREATE TEMP TABLE wip_selection AS
            WITH b AS (SELECT ?::TIMESTAMP AS start, ?::TIMESTAMP + ?::INTERVAL AS stop)
            SELECT case_id, started, CASE WHEN done AND ended < b.stop THEN ended END AS completed_at
            FROM wip_cases CROSS JOIN b WHERE {predicate}""", [boundary, boundary, step])
        total = conn.execute("SELECT count(*) FROM wip_selection").fetchone()[0]
        rows = conn.execute("""SELECT case_id, started, completed_at FROM wip_selection
            WHERE ? IS NULL OR case_id > ? ORDER BY case_id LIMIT ?""",
                            [settings.after, settings.after, settings.limit + 1]).fetchall()
        return {"total": total, "rows": rows}

    data = workspace._query(log_id, settings.case_type, run)
    rows = data["rows"]
    return {"bucket_start": settings.bucket_start, "group": settings.group, "case_notion": data["case_notion"],
            "total": data["total"],
            "cases": [{"case_id": key, "started_at": started.isoformat() + "Z",
                       "completed_at": completed.isoformat() + "Z" if completed is not None else None}
                      for key, started, completed in rows[:settings.limit]],
            "next_after": rows[settings.limit - 1][0] if len(rows) > settings.limit else None}


__all__ = ["work_in_progress", "bucket_cases"]
