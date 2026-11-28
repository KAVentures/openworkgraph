"""Offline prospective paired-case design; not a procurement decision gate."""
from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path

FORMAT = "openworkgraph.procurement-trial-design.v1"
REPORT = "openworkgraph.procurement-trial-design-report.v1"
BASIS = 10000
MAX_CASES = 50  # Current R26 trial enrollment cap; not a runtime dependency.


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _fields(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError("unexpected or missing fields")


def critical_wins(discordant, alpha_bps, comparisons):
    """Smallest wins giving an exact one-sided null tail <= family alpha / m."""
    _integer(discordant, 0, MAX_CASES, "discordant")
    _integer(alpha_bps, 1, 5000, "alpha_bps")
    _integer(comparisons, 1, 9, "comparisons")
    tail = 0
    threshold = None
    for wins in range(discordant, -1, -1):
        tail += comb(discordant, wins)
        if tail * BASIS * comparisons > alpha_bps * (1 << discordant):
            break
        threshold = wins
    return threshold


def _power(cases, win, loss, thresholds):
    """Exact multinomial rejection probability as an integer fraction."""
    same = BASIS - win - loss
    numerator = 0
    for d in range(cases + 1):
        threshold = thresholds[d]
        if threshold is None:
            continue
        tail = sum(comb(d, w) * win**w * loss**(d-w)
                   for w in range(threshold, d + 1))
        numerator += comb(cases, d) * same**(cases-d) * tail
    return numerator, BASIS**cases


def plan(request):
    _fields(request, ("format", "alpha_bps", "target_power_bps", "comparisons",
                      "maximum_cases", "scenarios"))
    if request["format"] != FORMAT:
        raise ValueError("unsupported format")
    alpha = _integer(request["alpha_bps"], 1, 5000, "alpha_bps")
    target = _integer(request["target_power_bps"], 1, BASIS, "target_power_bps")
    comparisons = _integer(request["comparisons"], 1, 9, "comparisons")
    maximum = _integer(request["maximum_cases"], 1, MAX_CASES, "maximum_cases")
    scenarios = request["scenarios"]
    if type(scenarios) is not list or not 1 <= len(scenarios) <= 12:
        raise ValueError("require 1 to 12 scenarios")
    seen = set()
    for scenario in scenarios:
        _fields(scenario, ("win_bps", "loss_bps"))
        win = _integer(scenario["win_bps"], 0, BASIS, "win_bps")
        loss = _integer(scenario["loss_bps"], 0, BASIS, "loss_bps")
        if win + loss > BASIS or (win, loss) in seen:
            raise ValueError("invalid or duplicate scenario")
        seen.add((win, loss))
    thresholds = [critical_wins(d, alpha, comparisons) for d in range(maximum+1)]
    rows = []
    first = None
    for cases in range(1, maximum+1):
        powers = []
        for scenario in scenarios:
            num, den = _power(cases, scenario["win_bps"], scenario["loss_bps"], thresholds)
            meets = num * BASIS >= target * den
            powers.append({"numerator": str(num), "denominator": str(den),
                           "power_bps_floor": num * BASIS // den, "meets_target": meets})
        all_meet = all(p["meets_target"] for p in powers)
        if all_meet and first is None:
            first = cases
        rows.append({"cases": cases, "powers": powers, "all_scenarios_meet": all_meet})
    return {"format": REPORT, "assumptions": request, "minimum_cases": first,
            "status": "feasible_under_assumptions" if first is not None else "not_within_cap",
            "critical_wins_by_discordant_count": thresholds, "rows": rows,
            "limitations": ["One prespecified binary outcome per independent paired case; repeats are not cases.",
                            "Scenarios are assumed probabilities, not measured effects or forecasts.",
                            "Bonferroni controls familywise type-I error; power is per comparison, not joint power.",
                            "No missing outcomes, clustering, interim looks, selection or execution authorization."]}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("nonfinite JSON value")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.request.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError("request exceeds 16 KiB")
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                             parse_constant=_reject_constant)
        result = plan(request)
    except (ValueError, OSError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
