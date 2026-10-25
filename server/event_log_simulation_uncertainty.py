"""Uncertainty of what-if results from estimating routing and arrivals on a finite log (#370 R14).

A what-if run uses the routing shares and arrival rate measured from the log as if they were exact.
With few cases behind a branch they are not. An opt-in ``uncertainty`` setting repeats the paired
baseline/scenario simulation with parameters drawn from what the log supports:

- routing: shares from a Dirichlet distribution over the observed path counts (a Bayesian bootstrap of
  which path each case took), so a branch seen 3 times varies far more than one seen 3,000 times;
- arrival rate: from a Gamma distribution over the number of arrivals and their time span.

Values the user entered (paths, arrivals_per_hour) stay as entered; activity times stay at their measured
values. Each replication draws its own simulation random numbers and shares them between baseline and
scenario, so intervals include simulation noise and the change stays paired. The intervals describe this
estimation uncertainty only, not unmodelled changes in the process.
"""
from __future__ import annotations

import random
from typing import Any, Callable

from .event_log_workspace import EventLogWorkspaceError

MIN_REPLICATIONS = 2
MAX_REPLICATIONS = 50
MAX_SIMULATED_CASES = 400_000        # replications × cases × 2 runs
LEVEL = 0.95
ASSUMPTIONS = [
    "Intervals repeat the paired simulation with routing shares and the arrival rate drawn from what the log "
    "supports: a Dirichlet distribution over observed path counts and a Gamma distribution over observed arrivals.",
    "Paths and arrival rates you entered stay as entered; activity times stay at their measured values, so the "
    "intervals leave out their uncertainty.",
    "Each replication uses its own simulation random numbers, shared by baseline and scenario. The intervals cover "
    "estimation and simulation noise, not changes in the process that the log does not show.",
]


def parse(spec: Any, cases: int) -> int | None:
    if spec is None:
        return None
    if not isinstance(spec, dict) or set(spec) - {"replications"}:
        raise EventLogWorkspaceError("uncertainty takes replications")
    count = spec.get("replications", 20)
    if isinstance(count, bool) or not isinstance(count, int) or not MIN_REPLICATIONS <= count <= MAX_REPLICATIONS:
        raise EventLogWorkspaceError(f"uncertainty replications must be a whole number from {MIN_REPLICATIONS} "
                                     f"to {MAX_REPLICATIONS}")
    if count * cases * 2 > MAX_SIMULATED_CASES:
        raise EventLogWorkspaceError(f"uncertainty simulates replications × cases × 2 runs; keep that at most "
                                     f"{MAX_SIMULATED_CASES:,} (fewer replications or cases)")
    return count


def draw(base: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """A copy of the calibration with routing shares and the arrival rate drawn from their estimation uncertainty."""
    covered = base["calibrated_cases"]
    counts = [max(1, round(p["weight"] * covered)) for p in base["paths"]]
    gammas = [rng.gammavariate(n, 1.0) for n in counts]
    total = sum(gammas)
    drawn = dict(base)
    drawn["paths"] = [{**p, "weight": g / total} for p, g in zip(base["paths"], gammas)]
    rate = base["arrivals_per_hour"]
    if rate is not None and covered >= 3:
        shape = covered - 1                       # arrivals between the first and last selected case start
        drawn["arrivals_per_hour"] = rng.gammavariate(shape, rate / shape)
    return drawn


def _interval(values: list[float]) -> dict[str, float] | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None

    def at(q: float) -> float:
        k = (len(values) - 1) * q
        lo, hi = int(k), min(int(k) + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (k - lo)
    tail = (1 - LEVEL) / 2
    return {"low": round(at(tail), 6), "median": round(at(0.5), 6), "high": round(at(1 - tail), 6)}


def replicate(base: dict[str, Any], seed: int, count: int,
              run: Callable[[dict[str, Any], int], tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]],
              ) -> dict[str, Any]:
    """``run(drawn_base, seed)`` builds and simulates both models; returns (baseline, result, baseline_model, model)."""
    rng = random.Random(f"owg-uncertainty:{seed}")
    rows: dict[str, list[float]] = {k: [] for k in ("baseline_p50", "baseline_p90", "result_p50", "result_p90",
                                                    "change_p50", "change_p90", "arrivals_per_hour")}
    weights: list[list[float]] = [[] for _ in base["paths"]]
    faster = unstable = 0
    for r in range(count):
        drawn = draw(base, rng)
        baseline, result, baseline_model, _ = run(drawn, (seed + 7919 * (r + 1)) % 2 ** 31)
        b, s = baseline["cycle_time_seconds"], result["cycle_time_seconds"]
        for key, runs in (("p50", (b, s)), ("p90", (b, s))):
            rows["baseline_" + key].append(runs[0][key])
            rows["result_" + key].append(runs[1][key])
            if runs[0][key] is not None and runs[1][key] is not None:
                rows["change_" + key].append(runs[1][key] - runs[0][key])
        rows["arrivals_per_hour"].append(baseline_model["arrivals_per_hour"])
        for i, p in enumerate(drawn["paths"]):
            weights[i].append(p["weight"])
        faster += bool(b["p50"] is not None and s["p50"] is not None and s["p50"] < b["p50"])
        unstable += bool(baseline["unstable"] or result["unstable"])
    seconds = {k: _interval(v) for k, v in rows.items() if k != "arrivals_per_hour"}
    return {
        "replications": count, "level": LEVEL, "method": "dirichlet_routing_gamma_arrivals",
        "cycle_time_seconds": {"baseline": {"p50": seconds["baseline_p50"], "p90": seconds["baseline_p90"]},
                               "result": {"p50": seconds["result_p50"], "p90": seconds["result_p90"]},
                               "change": {"p50": seconds["change_p50"], "p90": seconds["change_p90"]}},
        "replications_where_scenario_median_is_faster": faster,
        "replications_with_an_overloaded_activity": unstable,
        "measured_routing": [{"activities": p["activities"], "weight": round(p["weight"], 6),
                              "observed_cases": max(1, round(p["weight"] * base["calibrated_cases"])),
                              "drawn": _interval(w)} for p, w in zip(base["paths"], weights)],
        "baseline_arrivals_per_hour": _interval(rows["arrivals_per_hour"]),
        "assumptions": ASSUMPTIONS,
    }


__all__ = ["parse", "draw", "replicate", "MAX_REPLICATIONS"]
