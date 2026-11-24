"""R14: bounded demand-by-design stress tests against one shared calibration.

Every cell repeats the same input design across a declared set of seeds. Target
coverage uses all post-warm-up arrivals, not a selected set of completions.
The tested grid is not an optimized, extrapolated or certified capacity plan.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from statistics import median
from threading import BoundedSemaphore
from typing import Any

from . import event_log_simulation as sim
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.simulation-capacity-grid.v1"
REPORT_FORMAT = "openworkgraph.simulation-capacity-report.v1"
MAX_DESIGNS = 4
MAX_RATES = 8
MAX_CELLS = 24
MAX_REPLICATIONS = 8
MAX_CASES = 3000
MAX_ROUTE_STEPS = 4_000_000
MAX_SIMULATED_CASES = 100_000
BASE_FIELDS = frozenset({"cases", "seed", "activity_time_basis", "distribution",
                         "calendar", "paths", "activities", "pools", "priorities"})
DESIGN_FIELDS = frozenset({"label", "activities", "pools", "priorities"})
TOP_FIELDS = frozenset({"format", "baseline", "designs", "arrival_rates_per_hour",
                        "replications", "target"})
_GUARD = BoundedSemaphore(1)

ASSUMPTIONS = [
    "One calibrated set of complete paths is used for every demand and design; any omitted cases remain omitted.",
    "The observed activity gaps can include waiting and are not independently measured service times.",
    "Each cell repeats the declared finite experiment with shared seeds across designs and demand levels. "
    "The same seed does not imply the same observed case or identical service draws when routes or schedules differ.",
    "Target success uses every post-warm-up arrival. Completed late and abandoned cases are known misses; "
    "unresolved arrivals bound the target share. An overloaded cell never qualifies as meeting the target.",
    "Reported cycle-time quantiles summarize completed cases only, and ranges across replications are descriptive, "
    "not confidence intervals or evidence of production reliability.",
    "Only explicitly tested demand levels are reported. No interpolation, monotonicity, recommendation, "
    "causal benefit, live execution or production capacity guarantee is inferred.",
]


def _integer(value: Any, low: int, high: int, name: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise EventLogWorkspaceError(f"{name} must be an integer from {low} to {high}")
    return value


def _number(value: Any, low: float, high: float, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not low <= value <= high or not math.isfinite(value)):
        raise EventLogWorkspaceError(f"{name} must be between {low:g} and {high:g}")
    return float(value)


def _label(value: Any) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 80
            or value != value.strip() or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)):
        raise EventLogWorkspaceError("Each design needs a short printable label")
    return value


def _activities(value: Any) -> dict:
    if not isinstance(value, dict) or len(value) > sim.MAX_ACTIVITIES:
        raise EventLogWorkspaceError("Activities must be a bounded mapping")
    for name, config in value.items():
        if not isinstance(name, str) or not 1 <= len(name) <= 200 or not isinstance(config, dict):
            raise EventLogWorkspaceError("Each activity override needs an exact name and settings object")
        if set(config) - {"minutes", "resources", "batch", "queue_timeout_minutes"}:
            raise EventLogWorkspaceError("Unsupported activity setting in capacity design")
    return value


def normalize(spec: Any) -> dict:
    if not isinstance(spec, dict) or set(spec) != TOP_FIELDS or spec["format"] != FORMAT:
        raise EventLogWorkspaceError("Provide a capacity grid format, baseline, designs, demand rates, replications and target")
    baseline = spec["baseline"]
    if not isinstance(baseline, dict) or set(baseline) - BASE_FIELDS:
        raise EventLogWorkspaceError("Unsupported shared baseline field")
    baseline = deepcopy(baseline)
    baseline["cases"] = _integer(baseline.get("cases", 400), 50, MAX_CASES, "cases")
    reps = _integer(spec["replications"], 2, MAX_REPLICATIONS, "replications")
    baseline["seed"] = _integer(baseline.get("seed", 1), 0, 2**31 - reps + 1, "seed")
    if baseline.get("distribution", "exponential") not in ("fixed", "exponential"):
        raise EventLogWorkspaceError("Capacity grids currently support fixed or exponential activity times")
    if "activities" in baseline:
        _activities(baseline["activities"])
    rates = spec["arrival_rates_per_hour"]
    if not isinstance(rates, list) or not 2 <= len(rates) <= MAX_RATES:
        raise EventLogWorkspaceError("Enter 2 to 8 explicit demand rates")
    rates = sorted(_number(rate, .001, 10000, "arrival rate per hour") for rate in rates)
    if len(set(rates)) != len(rates):
        raise EventLogWorkspaceError("Demand rates must be distinct")
    designs = spec["designs"]
    if not isinstance(designs, list) or not 1 <= len(designs) <= MAX_DESIGNS - 1:
        raise EventLogWorkspaceError("Enter one to three alternatives in addition to the baseline")
    seen = {"Baseline"}
    for design in designs:
        if not isinstance(design, dict) or set(design) - DESIGN_FIELDS:
            raise EventLogWorkspaceError("Unsupported alternative field")
        name = _label(design.get("label"))
        if name in seen:
            raise EventLogWorkspaceError("Design labels must be distinct, and Baseline is reserved")
        seen.add(name)
        if "activities" in design:
            _activities(design["activities"])
    cells = (1 + len(designs)) * len(rates)
    if cells > MAX_CELLS or cells * reps * baseline["cases"] > MAX_SIMULATED_CASES:
        raise EventLogWorkspaceError("Capacity grid exceeds the cell or simulated-case budget; reduce rates, designs, cases or seeds")
    if baseline.get("calendar") is not None and baseline["cases"] / rates[0] > 2 * 8760:
        raise EventLogWorkspaceError("Calendar grid expected arrival span exceeds two years; increase minimum demand or reduce cases")
    target = spec["target"]
    if not isinstance(target, dict) or set(target) != {"cycle_time_minutes", "minimum_completion_share"}:
        raise EventLogWorkspaceError("Provide an elapsed cycle-time target and minimum completion share")
    return {"format": FORMAT, "baseline": baseline, "designs": deepcopy(designs),
            "arrival_rates_per_hour": rates, "replications": reps,
            "target": {"cycle_time_minutes": _number(target["cycle_time_minutes"], .001, 525600, "target minutes"),
                       "minimum_completion_share": _number(target["minimum_completion_share"], .01, 1, "target completion share")}}


def _model_inputs(baseline: dict, design: dict, rate: float) -> dict:
    inputs = {**deepcopy(baseline), "arrivals_per_hour": rate, "label": design["label"]}
    if "activities" in design:
        overrides = inputs.setdefault("activities", {})
        for name, config in design["activities"].items():
            overrides[name] = {**overrides.get(name, {}), **config}
    for field in ("pools", "priorities"):
        if field in design:
            inputs[field] = deepcopy(design[field])
    return inputs


def _prepare(packet: dict, calibration: dict) -> list[tuple[str, float, dict]]:
    if not calibration["paths"]:
        raise EventLogWorkspaceError("No complete calibrated paths support this capacity review")
    planned = []
    for design in [{"label": "Baseline"}, *packet["designs"]]:
        for rate in packet["arrival_rates_per_hour"]:
            inputs = _model_inputs(packet["baseline"], design, rate)
            model = sim.parse_scenario(inputs, calibration)
            unknown = set(inputs.get("activities", {})) - set(model["activities"])
            if unknown:
                raise EventLogWorkspaceError("An activity override is absent from the selected paths")
            planned.append((design["label"], rate, model))
    work = sum(max(len(path) for path, weight in model["paths"] if weight > 0)
               * model["cases"] * packet["replications"] for _, _, model in planned)
    if work > MAX_ROUTE_STEPS:
        raise EventLogWorkspaceError("Capacity grid exceeds the route-step budget; narrow the experiment")
    return planned


def summarize_run(result: dict, samples: list[float], target_seconds: float, seed: int) -> dict:
    arrivals = result["cases_simulated"] - int(result["cases_simulated"] * sim.WARM_UP_SHARE)
    completed = result["cases_measured"]
    abandoned = result.get("cases_abandoned_after_warmup", 0)
    unresolved = arrivals - completed - abandoned
    if (len(samples) != completed or unresolved < 0 or arrivals < 1
            or any(not math.isfinite(x) or x < 0 for x in samples)):
        raise EventLogWorkspaceError("Simulation accounting is inconsistent; no capacity result returned")
    on_time = sum(duration <= target_seconds for duration in samples)
    # Known late completions and abandonments can never count as target hits.
    late = completed - on_time
    bottlenecks = [{"activity": r["activity"], "pool": r.get("pool"),
                    "offered_load": r["offered_load"], "overloaded": r["overloaded"]}
                   for r in sorted(result["activities"], key=lambda r: (-r["offered_load"], r["activity"]))[:5]]
    return {
        "seed": seed, "post_warmup_arrivals": arrivals, "completed": completed,
        "completed_on_time": on_time, "completed_late": late, "abandoned": abandoned,
        "unresolved": unresolved, "target_share_lower": on_time / arrivals,
        "target_share_upper": (on_time + unresolved) / arrivals,
        "overloaded": result["unstable"], "cycle_time_seconds_completed_only": result["cycle_time_seconds"],
        "highest_offered_load_activities": bottlenecks,
    }


def _range(values: list[float | None]) -> dict | None:
    present = [x for x in values if x is not None]
    if not present:
        return None
    return {"min": min(present), "median": median(present), "max": max(present),
            "replications_with_value": len(present)}


def summarize_cell(label: str, rate: float, runs: list[dict], target_share: float) -> dict:
    lower = min(r["target_share_lower"] for r in runs)
    upper = min(r["target_share_upper"] for r in runs)
    overloaded = sum(r["overloaded"] for r in runs)
    known_fail = sum(r["target_share_upper"] < target_share for r in runs)
    known_pass = sum(r["target_share_lower"] >= target_share and not r["overloaded"] for r in runs)
    if overloaded:
        status = "overloaded"
    elif known_fail:
        status = "not_met_in_all_runs"
    elif known_pass == len(runs):
        status = "met_in_all_runs"
    else:
        status = "indeterminate"
    return {
        "design": label, "arrival_rate_per_hour": rate, "status": status,
        "replications": len(runs), "overloaded_replications": overloaded,
        "known_target_miss_replications": known_fail, "known_target_met_stable_replications": known_pass,
        "worst_replication_target_share_bounds": {"lower": lower, "upper": upper},
        "completed_case_p90_seconds_across_replications": _range([
            r["cycle_time_seconds_completed_only"]["p90"] for r in runs]),
        "unresolved_arrivals_across_replications": sum(r["unresolved"] for r in runs),
        "abandoned_arrivals_across_replications": sum(r["abandoned"] for r in runs),
        "runs": runs,
    }


def _envelope(cells: list[dict]) -> list[dict]:
    designs = list(dict.fromkeys(c["design"] for c in cells))
    report = []
    for design in designs:
        selected = sorted((c for c in cells if c["design"] == design), key=lambda c: c["arrival_rate_per_hour"])
        passed = [c["arrival_rate_per_hour"] for c in selected if c["status"] == "met_in_all_runs"]
        failed = [c["arrival_rate_per_hour"] for c in selected if c["status"] != "met_in_all_runs"]
        report.append({"design": design, "tested_rates_meeting_target": passed,
                       "tested_rates_not_demonstrated": failed,
                       "highest_tested_rate_meeting_target": max(passed) if passed else None,
                       "nonmonotonic_tested_pattern": bool(passed and failed and min(failed) < max(passed)),
                       "untested_rates_have_no_result": True})
    return report


def run_grid(log_id: str, spec: Any, case_type: str | None = None) -> dict:
    packet = normalize(spec)
    if not _GUARD.acquire(blocking=False):
        raise EventLogWorkspaceError("Another local capacity grid is running; retry after it completes")
    try:
        # One calibration object, even if the log is appended while we simulate.
        base = sim.calibrate(log_id, case_type)
        planned = _prepare(packet, base)
        seeds = list(range(packet["baseline"]["seed"],
                           packet["baseline"]["seed"] + packet["replications"]))
        target = packet["target"]
        cells = []
        for label, rate, model in planned:
            runs = []
            for seed in seeds:
                samples: list[float] = []
                try:
                    result = sim.simulate({**model, "seed": seed}, samples)
                except (OverflowError, ValueError) as exc:
                    if isinstance(exc, ValueError) and "out of range" not in str(exc):
                        raise
                    raise EventLogWorkspaceError("A grid run exceeds the supported simulation time range") from None
                runs.append(summarize_run(result, samples, target["cycle_time_minutes"] * 60, seed))
            cells.append(summarize_cell(label, rate, runs, target["minimum_completion_share"]))
        calibration = {k: base[k] for k in ("arrivals_per_hour", "paths", "paths_cover_cases_share",
                       "activities", "observed_cases", "calibrated_cases", "excluded_case_reasons",
                       "limits", "arrival_rate_basis") if k in base}
        identity = {"log": base["log"], "case_notion": base["case_notion"],
                    "calibration": calibration, "inputs": packet}
        digest = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                  allow_nan=False, ensure_ascii=False).encode()).hexdigest()
        return {"format": REPORT_FORMAT, **identity, "calibration_and_inputs_sha256": digest,
                "seeds": seeds, "cells": cells, "tested_envelope": _envelope(cells),
                "simulated_cases": len(cells) * len(seeds) * packet["baseline"]["cases"],
                "single_calibration_used": True, "source_content_independently_verified": False,
                "simulated_not_observed": True, "production_capacity_certified": False,
                "execution_authorized": False, "auto_selected_design": False,
                "assumptions": ASSUMPTIONS}
    finally:
        _GUARD.release()
