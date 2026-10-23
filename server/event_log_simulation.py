"""What-if simulation of process designs, calibrated from an imported event log.

A discrete-event simulation of cases flowing through activities, each with a
pool of interchangeable resources served first come, first served. The
baseline uses parameters measured from the log; a scenario overrides arrivals,
routing, durations or resources. Both runs share random numbers, so the
difference reflects the changed assumptions. Every result is a model output
under stated assumptions, not a forecast or a measured outcome.
"""
from __future__ import annotations

import heapq
import math
import random
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

_SEP = workspace._SEP
FORMAT = "openworkgraph.simulation-scenario.v1"
MAX_CASES = 20000
MAX_ACTIVITIES = 100
MAX_PATHS = 20
MAX_PATH_LENGTH = 100
MAX_RESOURCES = 1000
WARM_UP_SHARE = 0.1
ASSUMPTIONS = [
    "Cases arrive as a Poisson process at the stated rate.",
    "Each activity has a pool of interchangeable resources that serve one case at a time, first come, first served.",
    "Calibrated activity times are the median gap before each activity in the log; those gaps include any waiting, so the baseline can overstate processing time.",
    "Calibrated resources are the distinct resources observed per activity (1 when none is recorded).",
    "Resources work around the clock; no calendars, breaks, batching, priorities or abandonment are modeled.",
    "The first 10% of simulated cases are a warm-up and excluded from the statistics.",
]


def calibrate(log_id: str, case_type: str | None = None, max_paths: int = 10) -> dict[str, Any]:
    """Measure arrival rate, routing, activity times and resources from the log."""
    max_paths = max(1, min(int(max_paths), MAX_PATHS))

    def run(conn):
        cases, first, last = conn.execute("""SELECT count(*), min(start), max(start) FROM (
            SELECT min(ts) AS start FROM ce GROUP BY case_id)""").fetchone()
        span_hours = (last - first).total_seconds() / 3600 if cases and last > first else 0
        arrivals = round((cases - 1) / span_hours, 6) if span_hours > 0 else None
        rows = conn.execute(f"""SELECT activity, quantile_cont(gap, 0.5), count(*), count(DISTINCT nullif(resource, ''))
            FROM (SELECT activity, resource,
                    date_diff('second', lag(ts) OVER w, ts) AS gap
                  FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id))
            GROUP BY activity ORDER BY count(*) DESC LIMIT {MAX_ACTIVITIES}""").fetchall()
        variants = conn.execute(f"""SELECT path, count(*) AS n FROM (
              SELECT string_agg(activity, '{_SEP}' ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id)
            GROUP BY path ORDER BY n DESC, path LIMIT {max_paths}""").fetchall()
        covered = sum(n for _, n in variants)
        return {
            "format": FORMAT,
            "arrivals_per_hour": arrivals,
            "paths": [{"activities": p.split(_SEP)[:MAX_PATH_LENGTH], "weight": round(n / covered, 6)}
                      for p, n in variants],
            "paths_cover_cases_share": round(covered / cases, 6) if cases else 0,
            "activities": {a: {"minutes": round((g or 0) / 60, 3), "resources": max(1, r),
                               "time_source": "median observed gap" if g is not None else "first activity; no gap"}
                           for a, g, _, r in rows},
            "distribution": "exponential",
        }
    return workspace._query(log_id, case_type, run)


def _number(value: Any, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise EventLogWorkspaceError(f"{what} must be a number between {low:g} and {high:g}")
    return float(value)


def parse_scenario(spec: Any, base: dict[str, Any]) -> dict[str, Any]:
    """Merge a user scenario over the calibrated baseline."""
    if not isinstance(spec, dict) or spec.get("format", FORMAT) != FORMAT:
        raise EventLogWorkspaceError(f"scenario must use format {FORMAT}")
    unknown = set(spec) - {"format", "label", "arrivals_per_hour", "paths", "activities", "distribution", "cases", "seed"}
    if unknown:
        raise EventLogWorkspaceError("unknown scenario fields: " + ", ".join(sorted(unknown))[:200])
    arrivals = spec.get("arrivals_per_hour", base["arrivals_per_hour"])
    if arrivals is None:
        raise EventLogWorkspaceError("the log has too few cases to measure arrivals; enter arrivals_per_hour")
    arrivals = _number(arrivals, 1e-6, 1e6, "arrivals_per_hour")
    paths = spec.get("paths", base["paths"])
    if not isinstance(paths, list) or not 0 < len(paths) <= MAX_PATHS:
        raise EventLogWorkspaceError(f"scenario needs 1 to {MAX_PATHS} paths")
    routing = []
    for path in paths:
        if not isinstance(path, dict) or not isinstance(path.get("activities"), list):
            raise EventLogWorkspaceError("each path needs activities and a weight")
        steps = path["activities"]
        if not 0 < len(steps) <= MAX_PATH_LENGTH or not all(isinstance(a, str) and 0 < len(a) <= 200 for a in steps):
            raise EventLogWorkspaceError(f"each path needs 1 to {MAX_PATH_LENGTH} short activity names")
        routing.append((steps, _number(path.get("weight", 1), 0, 1e6, "path weight")))
    total = sum(w for _, w in routing)
    if total <= 0:
        raise EventLogWorkspaceError("path weights must not all be zero")
    overrides = spec.get("activities", {})
    if not isinstance(overrides, dict) or len(overrides) > MAX_ACTIVITIES:
        raise EventLogWorkspaceError("activities must map activity names to minutes and resources")
    params: dict[str, dict[str, float]] = {}
    for name in {a for steps, _ in routing for a in steps}:
        known = base["activities"].get(name, {})
        given = overrides.get(name, {})
        if not isinstance(given, dict) or set(given) - {"minutes", "resources"}:
            raise EventLogWorkspaceError("each activity takes minutes and resources")
        minutes = given.get("minutes", known.get("minutes"))
        if minutes is None:
            raise EventLogWorkspaceError("enter minutes for activities not seen in the log")
        resources = given.get("resources", known.get("resources", 1))
        resources = _number(resources, 1, MAX_RESOURCES, "resources")
        if resources != int(resources):
            raise EventLogWorkspaceError("resources must be a whole number")
        params[name] = {"minutes": _number(minutes, 0, 525600, "minutes"), "resources": int(resources)}
    if len(params) > MAX_ACTIVITIES:
        raise EventLogWorkspaceError(f"at most {MAX_ACTIVITIES} distinct activities")
    distribution = spec.get("distribution", base.get("distribution", "exponential"))
    if distribution not in ("exponential", "fixed"):
        raise EventLogWorkspaceError("distribution must be exponential or fixed")
    cases = int(_number(spec.get("cases", 2000), 10, MAX_CASES, "cases"))
    seed = int(_number(spec.get("seed", 1), 0, 2 ** 31, "seed"))
    label = spec.get("label") if isinstance(spec.get("label"), str) else ""
    return {"label": label[:80], "arrivals_per_hour": arrivals, "paths": [(s, w / total) for s, w in routing],
            "activities": params, "distribution": distribution, "cases": cases, "seed": seed}


def _percentile(ordered: list[float], q: float) -> float | None:
    if not ordered:
        return None
    k = (len(ordered) - 1) * q
    lo = math.floor(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def simulate(model: dict[str, Any]) -> dict[str, Any]:
    """Run one deterministic simulation; times are in seconds."""
    rng_arrival = random.Random(model["seed"])
    rng_route = random.Random(model["seed"] + 1)
    rng_service = random.Random(model["seed"] + 2)
    rate = model["arrivals_per_hour"] / 3600
    paths, cumulative, acc = [p for p, _ in model["paths"]], [], 0.0
    for _, weight in model["paths"]:
        acc += weight
        cumulative.append(acc)
    params, fixed = model["activities"], model["distribution"] == "fixed"
    free = {a: p["resources"] for a, p in params.items()}
    queues: dict[str, list[tuple[float, int]]] = {a: [] for a in params}
    busy = {a: 0.0 for a in params}
    waits = {a: [] for a in params}
    events: list[tuple[float, int, int, int]] = []  # (time, order, case, step) for arrivals at a step
    seq = 0
    start, finish, route, step_arrival = {}, {}, {}, {}
    clock = 0.0
    for case in range(model["cases"]):
        clock += rng_arrival.expovariate(rate)
        draw = rng_route.random()
        route[case] = paths[next((i for i, c in enumerate(cumulative) if draw <= c), len(paths) - 1)]
        start[case] = clock
        heapq.heappush(events, (clock, seq, case, 0)); seq += 1
    completions: list[tuple[float, int, int, int, str]] = []  # (time, order, case, step, activity)

    def service(activity: str) -> float:
        mean = params[activity]["minutes"] * 60
        return mean if fixed or mean == 0 else rng_service.expovariate(1 / mean)

    def begin(now: float, case: int, step: int, activity: str) -> None:
        nonlocal seq
        duration = service(activity)
        busy[activity] += duration
        waits[activity].append(now - step_arrival[(case, step)])
        heapq.heappush(completions, (now + duration, seq, case, step, activity)); seq += 1

    while events or completions:
        if completions and (not events or completions[0][0] <= events[0][0]):
            now, _, case, step, activity = heapq.heappop(completions)
            if queues[activity]:
                _, (waiting_case, waiting_step) = heapq.heappop(queues[activity])
                begin(now, waiting_case, waiting_step, activity)
            else:
                free[activity] += 1
            if step + 1 < len(route[case]):
                heapq.heappush(events, (now, seq, case, step + 1)); seq += 1
            else:
                finish[case] = now
            continue
        now, order, case, step = heapq.heappop(events)
        activity = route[case][step]
        step_arrival[(case, step)] = now
        if free[activity]:
            free[activity] -= 1
            begin(now, case, step, activity)
        else:
            heapq.heappush(queues[activity], (order, (case, step)))
    warm = int(model["cases"] * WARM_UP_SHARE)
    measured = [c for c in range(warm, model["cases"])]
    cycle = sorted(finish[c] - start[c] for c in measured)
    horizon = max(finish.values()) - start[0] if finish else 0
    window = (finish[measured[-1]] - start[measured[0]]) if measured else 0
    activity_rows = []
    for activity, p in sorted(params.items()):
        ordered = sorted(waits[activity])
        utilization = busy[activity] / (p["resources"] * horizon) if horizon else 0
        offered = model["arrivals_per_hour"] / 3600 * p["minutes"] * 60 * sum(
            w * steps.count(activity) for steps, w in model["paths"]) / p["resources"]
        activity_rows.append({"activity": activity, "minutes": p["minutes"], "resources": p["resources"],
                              "utilization": round(utilization, 4), "offered_load": round(offered, 4),
                              "mean_wait_seconds": round(sum(ordered) / len(ordered), 3) if ordered else 0,
                              "p90_wait_seconds": _percentile(ordered, 0.9) or 0,
                              "overloaded": offered >= 1})
    return {
        "cases_simulated": model["cases"],
        "cases_measured": len(cycle),
        "cycle_time_seconds": {"p50": _percentile(cycle, 0.5), "p90": _percentile(cycle, 0.9),
                               "mean": sum(cycle) / len(cycle) if cycle else None},
        "completed_per_day": round(len(cycle) / (window / 86400), 3) if window > 0 else None,
        "activities": activity_rows,
        "unstable": any(r["overloaded"] for r in activity_rows),
    }


def what_if(log_id: str, scenario: Any, case_type: str | None = None) -> dict[str, Any]:
    base = calibrate(log_id, case_type)
    baseline_model = parse_scenario({"cases": scenario.get("cases", 2000) if isinstance(scenario, dict) else 2000,
                                     "seed": scenario.get("seed", 1) if isinstance(scenario, dict) else 1}, base)
    scenario_model = parse_scenario(scenario, base)
    baseline, changed = simulate(baseline_model), simulate(scenario_model)
    b, s = baseline["cycle_time_seconds"]["p50"], changed["cycle_time_seconds"]["p50"]
    return {
        "log": base["log"], "case_notion": base["case_notion"],
        "calibration": {k: base[k] for k in ("arrivals_per_hour", "paths", "paths_cover_cases_share", "activities")},
        "scenario": {"label": scenario_model["label"], "arrivals_per_hour": scenario_model["arrivals_per_hour"],
                     "paths": [{"activities": p, "weight": round(w, 6)} for p, w in scenario_model["paths"]],
                     "activities": scenario_model["activities"], "distribution": scenario_model["distribution"],
                     "cases": scenario_model["cases"], "seed": scenario_model["seed"]},
        "baseline": baseline,
        "result": changed,
        "median_cycle_time_change_seconds": (s - b) if b is not None and s is not None else None,
        "arrivals_and_routing_share_random_numbers": True,
        "assumptions": ASSUMPTIONS,
        "simulated_not_observed": True,
        "forecast_or_guarantee": False,
        "execution_authorized": False,
    }
