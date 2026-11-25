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
from . import event_log_impact_time_profile as time_profile

MIN_CASES = 10                 # per period and cohort before an estimate is shown
MAX_SAMPLE = 5000              # cases per cell used for the bootstrap (seeded sample; counts stay exact)
METRICS = ("cycle_time", "rework", "sla_breach", "activity_count")
UNITS = {"cycle_time": "seconds", "rework": "share of cases", "sla_breach": "share of cases",
         "activity_count": "events per case"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeProfileSpec(_Strict):
    bin_days: StrictInt = Field(ge=1,le=90)
    baseline_bins: StrictInt = Field(ge=1,le=12)
    follow_up_bins: StrictInt = Field(ge=1,le=12)
    reference_bin: StrictInt = Field(default=-1,ge=-12,le=-1)
    minimum_cases: StrictInt = Field(default=10,ge=1,le=10000)


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
    time_profile: TimeProfileSpec | None = None

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
    profile=parsed.time_profile
    if profile is not None:
        if profile.reference_bin < -profile.baseline_bins:
            raise workspace.EventLogWorkspaceError("time profile reference must be a selected baseline bin")
        if parsed.window_days is not None and max(profile.baseline_bins,profile.follow_up_bins)*profile.bin_days>parsed.window_days:
            raise workspace.EventLogWorkspaceError("time profile bins must fit inside the selected main window")
        try:
            at=datetime.fromisoformat(parsed.intervention_at.replace("Z","+00:00"))
            at-timedelta(days=profile.baseline_bins*profile.bin_days)
            at+timedelta(days=profile.follow_up_bins*profile.bin_days)
        except OverflowError:
            raise workspace.EventLogWorkspaceError("time profile dates exceed the supported range") from None
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


def _case_period(started, ended, at, start, end, recent_from, exclude_spanning):
    if (start is not None and started < start) or (end is not None and started >= end):
        return "outside_window", False
    if started >= at:
        if recent_from is not None and started >= recent_from:
            return "recent_possibly_running", False
        return "follow_up", False
    spanning = ended >= at
    return ("spanning_intervention" if spanning and exclude_spanning else "baseline"), spanning


def _split(rows, at: datetime, start: datetime | None, end: datetime | None, recent_from: datetime | None,
           exclude_spanning: bool = False):
    """Values per period by case start, and counts of partly exposed and left-out cases."""
    before, after = [], []
    left = {"outside_window": 0, "recent_possibly_running": 0, "spanning_intervention": 0,
            "baseline_partly_after_intervention": 0}
    for _, started, ended, value in rows:
        period, spanning = _case_period(started, ended, at, start, end, recent_from, exclude_spanning)
        left["baseline_partly_after_intervention"] += spanning
        if period == "baseline":
            before.append(value)
        elif period == "follow_up":
            after.append(value)
        else:
            left[period] += 1
    return before, after, left


def _population(conn, log_id, parsed, at, window):
    _case_values(conn, parsed)
    rows = conn.execute("SELECT case_id, started, ended, value FROM impact_cases ORDER BY case_id").fetchall()
    if not rows:
        raise workspace.EventLogWorkspaceError("the log has no cases to compare")
    first, last = min(row[1] for row in rows), max(row[2] for row in rows)
    if not first < at <= last:
        raise workspace.EventLogWorkspaceError("the intervention date must fall inside the log's time span")
    recent = last - timedelta(days=parsed.exclude_recent_days) if parsed.exclude_recent_days else None
    start, end = (at - window, at + window) if window else (None, None)
    treated, control = _members(conn, parsed.treated, log_id), _members(conn, parsed.control, log_id)
    if treated is None:
        groups = {"all": rows}
    else:
        if control is None:
            control = {row[0] for row in rows} - treated
        if treated & control:
            raise workspace.EventLogWorkspaceError("treated and control cohorts must not share cases")
        groups = {"treated": [row for row in rows if row[0] in treated],
                  "control": [row for row in rows if row[0] in control]}
    return groups, first, start, end, recent, last


def cases(log_id: str, request: Any) -> dict[str, Any]:
    from .event_log_cases import _integer, _spec, _text
    request = _spec(request, {"analysis", "group", "period", "after", "limit", "relative_bin", "expected_profile_sha256"})
    parsed = parse(request.get("analysis"))
    group, period = request.get("group"), request.get("period")
    if group not in ("all", "treated", "control") or period not in ("baseline", "follow_up"):
        raise workspace.EventLogWorkspaceError("choose an impact group and period")
    if (parsed.treated is None) != (group == "all"):
        raise workspace.EventLogWorkspaceError("group does not match the analysis")
    index=request.get("relative_bin")
    pin=request.get("expected_profile_sha256")
    if index is not None:
        profile=parsed.time_profile
        if (type(index) is not int or profile is None or
                not -profile.baseline_bins<=index<profile.follow_up_bins or
                period!=("baseline" if index<0 else "follow_up")):
            raise workspace.EventLogWorkspaceError("choose a reviewed time profile bin and period")
        if not isinstance(pin,str) or len(pin)!=64 or any(c not in "0123456789abcdef" for c in pin):
            raise workspace.EventLogWorkspaceError("time profile evidence requires its exact review digest")
    elif pin is not None or "relative_bin" in request:
        raise workspace.EventLogWorkspaceError("select a time profile bin with its digest")
    after = _text(request["after"]) if request.get("after") is not None else None
    limit = _integer(request.get("limit", 30), 100)
    at = datetime.fromisoformat(parsed.intervention_at.replace("Z", "+00:00")).replace(tzinfo=None)
    window = timedelta(days=parsed.window_days) if parsed.window_days else None

    def run(conn):
        groups, first, start, end, recent, last = _population(conn, log_id, parsed, at, window)
        members=groups[group]
        if index is not None:
            report,inventory=time_profile.build(log_id,groups,at,start,end,recent,parsed,first,last,_case_period)
            if report["profile_sha256"]!=pin:
                raise workspace.EventLogWorkspaceError("time profile evidence changed; rerun impact analysis")
            members=inventory[group][index]
        total, page = 0, []
        for row in members:
            key, started, ended, _ = row
            bucket, _ = _case_period(started, ended, at, start, end, recent, parsed.exclude_spanning)
            if bucket != period:
                continue
            total += 1
            if len(page) <= limit and (after is None or key > after):
                page.append(row)
        return {**({"relative_bin":index,"profile_sha256":pin} if index is not None else {}),
                "selected_cases": total, "unit": UNITS[parsed.metric],
                "cases": [{"case_id": key, "value": value, "started_at": started.isoformat() + "Z",
                           "ended_at": ended.isoformat() + "Z"} for key, started, ended, value in page[:limit]],
                "next_after": page[limit - 1][0] if len(page) > limit else None}
    return workspace._query(log_id, parsed.case_type, run)


def analyze(log_id: str, spec: Any) -> dict[str, Any]:
    parsed = parse(spec)
    at = datetime.fromisoformat(parsed.intervention_at.replace("Z", "+00:00")).replace(tzinfo=None)
    window = timedelta(days=parsed.window_days) if parsed.window_days else None

    def run(conn):
        groups, first, start, end, recent_from, last = _population(conn, log_id, parsed, at, window)
        periods, excluded = {}, {}
        for name, members in groups.items():
            before, after, left = _split(members, at, start, end, recent_from, parsed.exclude_spanning)
            periods[name] = (before, after)
            excluded[name] = left
        main = "all" if parsed.treated is None else "treated"
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
        if parsed.treated is not None:
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
        if parsed.time_profile is not None:
            result["time_profile"]=time_profile.build(log_id,groups,at,start,end,recent_from,parsed,first,last,_case_period)[0]
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
