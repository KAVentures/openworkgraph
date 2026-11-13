"""When work happens: events and case arrivals by local weekday and hour.

Timestamps are stored in UTC. This report converts them to a chosen IANA time zone
(daylight saving included) and counts, in a 7 x 24 grid, the events (optionally of one
activity) and the case arrivals (first events). It also gives the share of events outside
chosen working hours. Counts describe when events were *recorded*; system-generated events
can cluster at batch times, and recorded time is not proof of when people worked.
"""
from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class PatternSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_zone: str = Field(default="UTC", min_length=1, max_length=64)
    activity: str | None = Field(default=None, min_length=1, max_length=200)
    work_start_hour: StrictInt = Field(default=8, ge=0, le=23)
    work_end_hour: StrictInt = Field(default=18, ge=1, le=24)
    work_days: list[StrictInt] = Field(default_factory=lambda: [1, 2, 3, 4, 5], max_length=7)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _check(self):
        if self.work_end_hour <= self.work_start_hour:
            raise ValueError("work_end_hour must be after work_start_hour")
        if any(d < 1 or d > 7 for d in self.work_days) or len(set(self.work_days)) != len(self.work_days):
            raise ValueError("work_days are distinct ISO weekdays 1-7")
        return self


def _parse(spec: Any) -> PatternSpec:
    try:
        parsed = PatternSpec.model_validate(spec or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "time-pattern settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    try:
        ZoneInfo(parsed.time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise workspace.EventLogWorkspaceError("unknown time zone") from None
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def time_patterns(log_id: str, spec: Any = None) -> dict[str, Any]:
    settings = _parse(spec)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        local = "timezone(?, ts::TIMESTAMPTZ)"
        conn.execute("SET TimeZone = 'UTC'")
        where, params = ("WHERE activity = ?", [settings.activity]) if settings.activity else ("", [])
        events = conn.execute(f"""SELECT isodow({local}) AS d, hour({local}) AS h, count(*) FROM
              (SELECT DISTINCT event_id, ts, activity FROM ce) {where} GROUP BY 1, 2""",
                              [settings.time_zone, settings.time_zone, *params]).fetchall()
        arrivals = conn.execute(f"""SELECT isodow({local}) AS d, hour({local}) AS h, count(*) FROM
              (SELECT case_id, min(ts) AS ts FROM ce GROUP BY case_id) GROUP BY 1, 2""",
                                [settings.time_zone, settings.time_zone]).fetchall()
        return events, arrivals
    result = workspace._query(log_id, settings.case_type, lambda conn: {"_run": run(conn)})
    events, arrivals = result.pop("_run")

    def grid(rows):
        g = [[0] * 24 for _ in range(7)]
        for d, h, n in rows:
            g[int(d) - 1][int(h)] += int(n)
        return g
    ev, ar = grid(events), grid(arrivals)
    total = sum(map(sum, ev))
    inside = sum(ev[d - 1][h] for d in settings.work_days for h in range(settings.work_start_hour, settings.work_end_hour))
    busiest = sorted(((ev[d][h], d, h) for d in range(7) for h in range(24) if ev[d][h]), reverse=True)[:5]
    return {**result, "settings": settings.model_dump(exclude={"cohort", "case_type"}),
            "cohort": cohorts.describe(cohorts.parse_cohort(settings.cohort)),
            "weekdays": list(WEEKDAYS), "events": ev, "case_arrivals": ar, "total_events": total,
            "total_case_arrivals": sum(map(sum, ar)),
            "outside_working_hours_share": round((total - inside) / total, 4) if total else None,
            "busiest": [{"weekday": WEEKDAYS[d], "hour": h, "events": n} for n, d, h in busiest],
            "interpretation": ("When events were recorded, in the chosen time zone. System-generated events can "
                               "cluster at batch times, and recorded time is not proof of when people worked.")}


__all__ = ["time_patterns"]
