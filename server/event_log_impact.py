"""Before/after intervention impact on saved event logs (#370 R16).

Opportunity scenarios and benefit plans state *modeled* value. This measures what the log shows changed after an
intervention went live, for example an automation or a new approval rule:

- **periods:** cases are assigned by their start: before the intervention is the baseline, on or after it the
  follow-up. Baseline cases still running at the intervention are counted as partly exposed. They stay in the
  baseline by default, because dropping them removes the longest baseline cases and biases it short; they can be
  left out on request. An optional window keeps both periods to the same length, and the most recent cases can
  be left out because they may still be running when the log ends;
- **metrics per case:** cycle time, rework (an activity repeated), SLA breach, or the count of one activity;
- **estimates:** the mean (or share) and median in each period and their difference, with percentile bootstrap
  95% intervals from seeded resampling of cases;
- **control cohort:** with a treated cohort (the cases the intervention applies to) the change is compared with
  the change in control cases over the same periods: a difference-in-differences that removes trends both
  cohorts share;
- **placebo check:** the same estimate at a false intervention date inside the baseline. An interval that excludes
  zero there means the metric was already moving before the intervention.

Results are associations between periods. Other changes in the same weeks (volume, staffing, seasonality,
missing events) can cause or hide a difference; a control removes only trends both cohorts share. They are not
causal proof, verified savings or a measure of effort; cycle time is elapsed time, not work time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MIN_CASES = 10                 # per period and cohort before an estimate is shown
MAX_SAMPLE = 5000              # cases per cell used for the bootstrap (seeded sample; counts stay exact)
METRICS = ("cycle_time", "rework", "sla_breach", "activity_count")
UNITS = {"cycle_time": "seconds", "rework": "share of cases", "sla_breach": "share of cases",
         "activity_count": "events per case"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImpactSpec(_Strict):
    intervention_at: Annotated[str, Field(min_length=10, max_length=40)]
    metric: Literal["cycle_time", "rework", "sla_breach", "activity_count"] = "cycle_time"
    activity: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    sla_seconds: StrictInt | None = Field(default=None, ge=1, le=10 ** 9)
    window_days: StrictInt | None = Field(default=None, ge=1, le=3650)
    exclude_recent_days: StrictInt = Field(default=0, ge=0, le=3650)
    exclude_spanning: StrictBool = False
    treated: dict[str, Any] | None = None
    control: dict[str, Any] | None = None
    case_type: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    rounds: StrictInt = Field(default=400, ge=100, le=2000)
    seed: StrictInt = Field(default=20261101, ge=0, le=2 ** 31)

    @field_validator("intervention_at")
    @classmethod
    def _aware(cls, value: str) -> str:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("intervention_at must be an ISO 8601 date and time") from None
        if moment.tzinfo is None:
            raise ValueError("intervention_at needs a UTC offset, for example 2024-03-01T00:00:00Z")
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse(spec: Any) -> ImpactSpec:
    try:
        parsed = ImpactSpec.model_validate(spec)
    except ValidationError as error:
        first = error.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "impact analysis: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"].removeprefix("Value error, ")) from None
    if parsed.metric == "activity_count" and parsed.activity is None:
        raise workspace.EventLogWorkspaceError("activity_count needs the activity to count")
    if parsed.metric != "activity_count" and parsed.activity is not None:
        raise workspace.EventLogWorkspaceError("activity applies only to activity_count")
    if parsed.metric == "sla_breach" and parsed.sla_seconds is None:
        raise workspace.EventLogWorkspaceError("sla_breach needs sla_seconds")
    if parsed.metric != "sla_breach" and parsed.sla_seconds is not None:
        raise workspace.EventLogWorkspaceError("sla_seconds applies only to sla_breach")
    if parsed.control is not None and parsed.treated is None:
        raise workspace.EventLogWorkspaceError("a control cohort needs a treated cohort")
    for cohort in (parsed.treated, parsed.control):
        if cohort is not None:
            cohorts.parse_cohort(cohort)
    return parsed


# --- statistics -----------------------------------------------------------------

def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _sample(values: list[float], rng: random.Random) -> list[float]:
    values = sorted(values)          # fetch order must not change seeded draws
    return values if len(values) <= MAX_SAMPLE else sorted(rng.sample(values, MAX_SAMPLE))


def _interval(draws: list[float]) -> list[float]:
    draws.sort()
    n = len(draws) - 1
    return [round(draws[int(0.025 * n)], 6), round(draws[int(round(0.975 * n))], 6)]


def _summary(values: list[float]) -> dict[str, Any]:
    return {"cases": len(values), "mean": round(_mean(values), 6) if values else None,
            "median": round(_median(values), 6) if values else None}


def _change(before: list[float], after: list[float], rounds: int, seed: int) -> dict[str, Any]:
    """after − before for mean and median, with bootstrap intervals."""
    if len(before) < MIN_CASES or len(after) < MIN_CASES:
        return {"estimated": False, "reason": f"each period needs at least {MIN_CASES} cases"}
    rng = random.Random(seed)
    a, b = _sample(before, rng), _sample(after, rng)
    means, medians = [], []
    for _ in range(rounds):
        x, y = rng.choices(a, k=len(a)), rng.choices(b, k=len(b))
        means.append(_mean(y) - _mean(x))
        medians.append(_median(y) - _median(x))
    mean_change = _mean(after) - _mean(before)
    return {"estimated": True,
            "mean_change": round(mean_change, 6), "mean_change_ci95": _interval(means),
            "relative_mean_change": round(mean_change / _mean(before), 6) if _mean(before) else None,
            "median_change": round(_median(after) - _median(before), 6), "median_change_ci95": _interval(medians)}


def _did(cells: dict[str, list[float]], rounds: int, seed: int) -> dict[str, Any]:
    """(treated after − before) − (control after − before) of means."""
    short = [k for k, v in cells.items() if len(v) < MIN_CASES]
    if short:
        return {"estimated": False, "reason": f"each cohort and period needs at least {MIN_CASES} cases",
                "short_cells": short}
    rng = random.Random(seed)
    sampled = {k: _sample(v, rng) for k, v in cells.items()}
    estimate = lambda m: (m["treated_after"] - m["treated_before"]) - (m["control_after"] - m["control_before"])
    draws = [estimate({k: _mean(rng.choices(v, k=len(v))) for k, v in sampled.items()}) for _ in range(rounds)]
    point = estimate({k: _mean(v) for k, v in cells.items()})
    return {"estimated": True, "difference_in_differences": round(point, 6), "ci95": _interval(draws)}


def _excludes_zero(interval: list[float]) -> bool:
    return interval[0] > 0 or interval[1] < 0


# --- data -------------------------------------------------------------------------

def _case_values(conn, spec: ImpactSpec) -> None:
    if spec.metric == "cycle_time":
        value = "epoch(max(ts) - min(ts))"
    elif spec.metric == "rework":
        value = "CASE WHEN count(*) > count(DISTINCT activity) THEN 1.0 ELSE 0.0 END"
    elif spec.metric == "sla_breach":
        value = f"CASE WHEN epoch(max(ts) - min(ts)) > {int(spec.sla_seconds)} THEN 1.0 ELSE 0.0 END"
    else:
        value = "CAST(count(*) FILTER (WHERE activity = $activity) AS DOUBLE)"
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE impact_cases AS
        SELECT case_id, min(ts) AS started, max(ts) AS ended, {value} AS value FROM ce GROUP BY case_id""",
                 {"activity": spec.activity} if spec.metric == "activity_count" else {})


def _members(conn, cohort: dict[str, Any] | None, log_id: str) -> set[str] | None:
    if cohort is None:
        return None
    cohorts.restrict(conn, cohorts.parse_cohort(cohort), log_id, table="impact_cohort")
    return {r[0] for r in conn.execute("SELECT DISTINCT case_id FROM impact_cohort").fetchall()}


def _split(rows, at: datetime, start: datetime | None, end: datetime | None, recent_from: datetime | None,
           exclude_spanning: bool = False):
    """Values per period by case start, and counts of partly exposed and left-out cases."""
    before, after = [], []
    left = {"outside_window": 0, "recent_possibly_running": 0, "spanning_intervention": 0}
    spanning = 0
    for _, started, ended, value in rows:
        if (start is not None and started < start) or (end is not None and started >= end):
            left["outside_window"] += 1
        elif started >= at:
            if recent_from is not None and started >= recent_from:
                left["recent_possibly_running"] += 1
            else:
                after.append(value)
        elif ended >= at:
            spanning += 1
            if exclude_spanning:
                left["spanning_intervention"] += 1
            else:
                before.append(value)
        else:
            before.append(value)
    return before, after, {**left, "baseline_partly_after_intervention": spanning}


def analyze(log_id: str, spec: Any) -> dict[str, Any]:
    parsed = parse(spec)
    at = datetime.fromisoformat(parsed.intervention_at.replace("Z", "+00:00")).replace(tzinfo=None)
    window = timedelta(days=parsed.window_days) if parsed.window_days else None

    def run(conn):
        _case_values(conn, parsed)
        rows = conn.execute("SELECT case_id, started, ended, value FROM impact_cases ORDER BY case_id").fetchall()
        if not rows:
            raise workspace.EventLogWorkspaceError("the log has no cases to compare")
        first, last = min(r[1] for r in rows), max(r[2] for r in rows)
        if not first < at <= last:
            raise workspace.EventLogWorkspaceError("the intervention date must fall inside the log's time span")
        recent_from = last - timedelta(days=parsed.exclude_recent_days) if parsed.exclude_recent_days else None
        start, end = (at - window, at + window) if window else (None, None)
        treated, control = _members(conn, parsed.treated, log_id), _members(conn, parsed.control, log_id)
        if treated is not None and control is None:
            control = {r[0] for r in rows} - treated
        if treated is not None and treated & control:
            raise workspace.EventLogWorkspaceError("treated and control cohorts must not share cases")
        groups = {"all": rows} if treated is None else {
            "treated": [r for r in rows if r[0] in treated], "control": [r for r in rows if r[0] in control]}
        periods, excluded = {}, {}
        for name, members in groups.items():
            before, after, left = _split(members, at, start, end, recent_from, parsed.exclude_spanning)
            periods[name] = (before, after)
            excluded[name] = left
        main = "all" if treated is None else "treated"
        before, after = periods[main]
        result: dict[str, Any] = {
            "metric": parsed.metric, "unit": UNITS[parsed.metric],
            **({"activity": parsed.activity} if parsed.activity else {}),
            **({"sla_seconds": parsed.sla_seconds} if parsed.sla_seconds else {}),
            "intervention_at": parsed.intervention_at,
            "periods": {"baseline": {"from": start.isoformat() + "Z" if start else first.isoformat() + "Z",
                                     "before": parsed.intervention_at},
                        "follow_up": {"from": parsed.intervention_at,
                                      "before": end.isoformat() + "Z" if end else None}},
            "groups": {name: {"baseline": _summary(b), "follow_up": _summary(a), "left_out": excluded[name]}
                       for name, (b, a) in periods.items()},
            "change": _change(before, after, parsed.rounds, parsed.seed),
        }
        if treated is not None:
            cb, ca = periods["control"]
            result["control_adjusted"] = _did({"treated_before": before, "treated_after": after,
                                               "control_before": cb, "control_after": ca}, parsed.rounds, parsed.seed)
            result["cohorts"] = {"treated": cohorts.describe(cohorts.parse_cohort(parsed.treated)),
                                 "control": cohorts.describe(cohorts.parse_cohort(parsed.control))
                                 if parsed.control is not None else "every case not in the treated cohort"}
        # Placebo: the midpoint of the baseline as a false intervention date, using baseline cases only.
        # A placebo period must end before the intervention, so only baseline cases that finished before it count.
        baseline_rows = [r for r in groups[main] if r[2] < at and (start is None or r[1] >= start)]
        if baseline_rows:
            origin = start or min(r[1] for r in baseline_rows)
            placebo_at = origin + (at - origin) / 2
            pb, pa, _ = _split(baseline_rows, placebo_at, None, None, None)
            placebo = _change(pb, pa, parsed.rounds, parsed.seed + 1)
            placebo["placebo_at"] = placebo_at.isoformat() + "Z"
            placebo["pre_trend_detected"] = placebo["estimated"] and _excludes_zero(placebo["mean_change_ci95"])
        else:
            placebo = {"estimated": False, "reason": "no baseline cases"}
        result["placebo"] = placebo
        change = result["change"]
        result["reading"] = _reading(parsed, change, result.get("control_adjusted"), placebo)
        result.update({
            "bootstrap": {"rounds": parsed.rounds, "seed": parsed.seed, "max_cases_per_cell": MAX_SAMPLE},
            "interpretation": ("Observed change between periods in this log. Other changes in the same weeks (volume, "
                               "staffing, seasonality, missing events) can cause or hide a difference; a control "
                               "cohort removes only trends both cohorts share. Not causal proof, verified savings "
                               "or effort: cycle time is elapsed time."),
            "causal_effect_verified": False, "realized_savings_verified": False,
        })
        return result
    return workspace._query(log_id, parsed.case_type, run)


def _reading(spec: ImpactSpec, change: dict[str, Any], did: dict[str, Any] | None, placebo: dict[str, Any]) -> str:
    if not change["estimated"]:
        return "Too few cases on one side of the intervention to compare."
    moved = _excludes_zero(change["mean_change_ci95"])
    direction = "down" if change["mean_change"] < 0 else "up"
    text = (f"The mean {spec.metric.replace('_', ' ')} went {direction} after the intervention, and the 95% interval "
            "excludes zero." if moved else
            f"The mean {spec.metric.replace('_', ' ')} changed by an amount the 95% interval cannot tell from no change.")
    if did is not None:
        text += (" Compared with the control cohort, the difference remains." if did["estimated"] and
                 _excludes_zero(did["ci95"]) else
                 " Compared with the control cohort, the difference is not clear." if did["estimated"] else
                 " The control comparison needs more cases.")
    if placebo.get("pre_trend_detected"):
        text += " The metric was already changing before the intervention (placebo check), so read this with caution."
    return text


__all__ = ["analyze", "parse"]
