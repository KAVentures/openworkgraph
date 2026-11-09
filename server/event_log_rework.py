"""Rework and loops in a saved event log.

Rework is an activity happening again in the same case. For every activity the report gives
the share of cases that repeat it and the extra occurrences. Loops are named by what happened
between two occurrences: a *self-loop* (A then A again), a *short loop* (A, B, A) or a longer
return to A. The most common loop patterns are listed with how many cases show them.

Cases with any rework are compared with cases without on throughput time and events per case,
with a bootstrap 95% interval for the difference in median throughput. Rework is associated
with longer cases here; this does not show that the rework caused the delay.
"""
from __future__ import annotations

from collections import Counter
import random
import statistics
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_PATTERN_LENGTH = 6
MAX_CASES = 200_000
BOOTSTRAP = 400


class ReworkSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: StrictInt = Field(default=20, ge=1, le=100)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


def _parse(spec: Any) -> ReworkSpec:
    try:
        parsed = ReworkSpec.model_validate(spec or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "rework settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def _median_ci(a: list[float], b: list[float]) -> list[float] | None:
    if len(a) < 5 or len(b) < 5:
        return None
    rng = random.Random(20240304)
    diffs = sorted(statistics.median(rng.choices(a, k=len(a))) - statistics.median(rng.choices(b, k=len(b)))
                   for _ in range(BOOTSTRAP))
    return [round(diffs[int(0.025 * BOOTSTRAP)], 3), round(diffs[int(0.975 * BOOTSTRAP) - 1], 3)]


def rework(log_id: str, spec: Any = None) -> dict[str, Any]:
    settings = _parse(spec)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        total = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        if total > MAX_CASES:
            raise workspace.EventLogWorkspaceError(f"rework analysis reads at most {MAX_CASES:,} cases; use a cohort")
        rows = conn.execute("""SELECT case_id, list(activity ORDER BY ts, event_id), epoch(max(ts) - min(ts))
            FROM ce GROUP BY case_id""").fetchall()
        return total, rows
    result = workspace._query(log_id, settings.case_type, lambda conn: {"_run": run(conn)})
    total, rows = result.pop("_run")
    repeat_cases: Counter = Counter()
    extra: Counter = Counter()
    present: Counter = Counter()
    patterns: Counter = Counter()
    kinds: Counter = Counter()
    with_rework, without = [], []
    events_with, events_without = [], []
    for _case, path, seconds in rows:
        counts = Counter(path)
        for activity, n in counts.items():
            present[activity] += 1
            if n > 1:
                repeat_cases[activity] += 1
                extra[activity] += n - 1
        last_seen: dict[str, int] = {}
        case_patterns, case_kinds = set(), set()
        for i, activity in enumerate(path):
            if activity in last_seen:
                between = path[last_seen[activity] + 1:i]
                kind = "self_loop" if not between else "short_loop" if len(between) == 1 else "longer_loop"
                case_kinds.add(kind)
                if len(between) + 2 <= MAX_PATTERN_LENGTH:
                    case_patterns.add(tuple([activity, *between, activity]))
            last_seen[activity] = i
        for p in case_patterns:
            patterns[p] += 1
        for k in case_kinds:
            kinds[k] += 1
        if any(n > 1 for n in counts.values()):
            with_rework.append(float(seconds))
            events_with.append(len(path))
        else:
            without.append(float(seconds))
            events_without.append(len(path))
    activities = sorted(({"activity": a, "cases_with_activity": present[a], "cases_repeating": repeat_cases[a],
                          "repeat_share_of_cases_with_activity": round(repeat_cases[a] / present[a], 4),
                          "extra_occurrences": extra[a]} for a in repeat_cases),
                        key=lambda r: (-r["cases_repeating"], r["activity"]))
    comparison = None
    if with_rework and without:
        mw, mo = statistics.median(with_rework), statistics.median(without)
        comparison = {"cases_with_rework": len(with_rework), "cases_without_rework": len(without),
                      "median_throughput_with_rework_seconds": round(mw, 3),
                      "median_throughput_without_rework_seconds": round(mo, 3),
                      "median_difference_seconds": round(mw - mo, 3),
                      "median_difference_ci95_seconds": _median_ci(with_rework, without),
                      "mean_events_with_rework": round(statistics.fmean(events_with), 3),
                      "mean_events_without_rework": round(statistics.fmean(events_without), 3)}
    return {**result, "settings": settings.model_dump(exclude={"cohort", "case_type"}),
            "cohort": cohorts.describe(cohorts.parse_cohort(settings.cohort)),
            "cases": total, "cases_with_rework": len(with_rework),
            "rework_rate": round(len(with_rework) / total, 4) if total else 0.0,
            "loop_kinds": {k: kinds.get(k, 0) for k in ("self_loop", "short_loop", "longer_loop")},
            "activities": activities[:settings.limit], "activities_truncated": len(activities) > settings.limit,
            "loop_patterns": [{"pattern": list(p), "cases": n} for p, n in
                              sorted(patterns.items(), key=lambda q: (-q[1], q[0]))[:settings.limit]],
            "comparison": comparison,
            "interpretation": ("Rework is an activity happening again in the same case. Cases with rework are "
                               "compared with cases without; an association with longer cases does not show that "
                               "the rework caused the delay.")}


__all__ = ["rework"]
