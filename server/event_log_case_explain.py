"""Explain one case: where its time went compared with the rest of the log.

For a chosen case, every step-to-step gap is compared with the median gap of
the same transition across all cases, so an analyst can see which steps made a
slow case slow. The explanation also points out steps that few cases take,
activities the case repeats, and how the case's total time compares with the
log's median and 90th percentile.

``slowest`` lists the slowest cases to pick from. Gaps are elapsed time between
recorded events, not measured work or verified waiting, and "typical" is the
median of this log, not a target.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-case-explanation.v1"
RARE_SHARE = 0.1
MIN_TRANSITION_CASES = 3
MAX_STEPS = 500


class ExplainSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=500)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


class SlowestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_type: str | None = Field(default=None, min_length=1, max_length=200)
    cohort: dict[str, Any] = Field(default_factory=dict)
    limit: StrictInt = Field(default=10, ge=1, le=100)


def _validate(model, spec: Any):
    try:
        return model.model_validate(spec if spec is not None else {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "case explanation settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None


def explain(log_id: str, spec: Any) -> dict[str, Any]:
    settings = _validate(ExplainSpec, spec)

    def run(conn):
        steps = conn.execute(f"""SELECT activity, ts, resource, event_id FROM ce WHERE case_id = ?
            ORDER BY ts, event_id LIMIT {MAX_STEPS + 1}""", [settings.case_id]).fetchall()
        if not steps:
            raise workspace.EventLogWorkspaceError("unknown case: it has no events under this case notion")
        cases, median, p90 = conn.execute("""SELECT count(*), quantile_cont(d, 0.5), quantile_cont(d, 0.9) FROM (
              SELECT epoch(max(ts) - min(ts)) AS d FROM ce GROUP BY case_id)""").fetchone()
        rank = conn.execute("""SELECT count(*) FROM (SELECT epoch(max(ts) - min(ts)) AS d FROM ce GROUP BY case_id)
            WHERE d > (SELECT epoch(max(ts) - min(ts)) FROM ce WHERE case_id = ?)""", [settings.case_id]).fetchone()[0]
        typical = {(a, b): (n, c, med) for a, b, n, c, med in conn.execute("""SELECT activity, nxt, count(*),
              count(DISTINCT case_id), quantile_cont(gap, 0.5) FROM (
                SELECT case_id, activity, lead(activity) OVER w AS nxt, epoch((lead(ts) OVER w) - ts) AS gap
                FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id))
            WHERE nxt IS NOT NULL GROUP BY 1, 2""").fetchall()}
        activity_cases = dict(conn.execute("SELECT activity, count(DISTINCT case_id) FROM ce GROUP BY 1").fetchall())
        return {"steps": steps, "cases": cases, "median": median, "p90": p90, "rank": rank,
                "typical": typical, "activity_cases": activity_cases}

    data = workspace._query(log_id, settings.case_type, run)
    steps = data["steps"][:MAX_STEPS]
    cases = data["cases"]
    duration = (steps[-1][1] - steps[0][1]).total_seconds()
    gaps = []
    for (a, t1, _r, _e), (b, t2, _r2, _e2) in zip(steps, steps[1:]):
        gap = (t2 - t1).total_seconds()
        n, c, med = data["typical"].get((a, b), (0, 0, None))
        # A transition too few cases take has no meaningful typical gap: all of its time is unexplained.
        if c < MIN_TRANSITION_CASES:
            med = None
        excess = gap if med is None else gap - med
        gaps.append({"from": a, "to": b, "gap_seconds": gap, "typical_gap_seconds": med,
                     "excess_seconds": excess, "cases_with_transition": c,
                     "transition_case_share": round(c / cases, 6) if cases else None,
                     "unusual_transition": bool(cases and c / cases < RARE_SHARE)})
    counts: dict[str, int] = {}
    for activity, *_ in steps:
        counts[activity] = counts.get(activity, 0) + 1
    total_excess = sum(g["excess_seconds"] for g in gaps if g["excess_seconds"] and g["excess_seconds"] > 0)
    biggest = sorted((g for g in gaps if g["excess_seconds"] and g["excess_seconds"] > 0),
                     key=lambda g: -g["excess_seconds"])[:5]
    for g in biggest:
        g_share = g["excess_seconds"] / total_excess if total_excess else 0
        g["share_of_excess"] = round(g_share, 6)
    return {
        "format": FORMAT,
        "case_id": settings.case_id,
        "case_notion": data["case_notion"],
        "events": len(steps),
        "steps_truncated": len(data["steps"]) > MAX_STEPS,
        "started_at": steps[0][1].isoformat() + "Z",
        "ended_at": steps[-1][1].isoformat() + "Z",
        "duration_seconds": duration,
        "log_median_seconds": data["median"], "log_p90_seconds": data["p90"],
        "slower_than_share_of_cases": round(1 - data["rank"] / cases, 6) if cases else None,
        "path": [s[0] for s in steps],
        "steps": [{"activity": a, "at": t.isoformat() + "Z", "resource": r or None} for a, t, r, _e in steps],
        "gaps": gaps,
        "biggest_excess": biggest,
        "time_above_typical_seconds": total_excess,
        "repeated_activities": [{"activity": a, "times": n} for a, n in sorted(counts.items()) if n > 1],
        "uncommon_activities": sorted(a for a in counts
                                      if cases and data["activity_cases"].get(a, 0) / cases < RARE_SHARE),
        "interpretation": ("Each gap is compared with the median gap of the same transition in this log; a "
                           "transition fewer than 3 cases take has no typical gap, so all of its time counts. Gaps are "
                           "elapsed time between recorded events, not measured work or verified waiting, and the "
                           "median is a description of this log, not a target."),
    }


def slowest(log_id: str, spec: Any = None) -> dict[str, Any]:
    settings = _validate(SlowestSpec, spec)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        rows = conn.execute(f"""SELECT case_id, epoch(max(ts) - min(ts)) AS d, count(*), min(ts)
            FROM ce GROUP BY case_id ORDER BY d DESC, case_id LIMIT {settings.limit}""").fetchall()
        return {"cases": [{"case_id": c, "duration_seconds": d, "events": n, "started_at": s.isoformat() + "Z"}
                          for c, d, n, s in rows]}
    return {"format": FORMAT, **workspace._query(log_id, settings.case_type, run)}
