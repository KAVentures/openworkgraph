"""Finite-population score bounds for frozen prospective predictions.

An open case is censored at the retained observation horizon, conditional on
complete observation. Missing cases and inconsistent historical completions
are unknown, never treated as successful or completed observations.
"""
from math import isfinite


def _finite(value):
    return type(value) in (int, float) and isfinite(value)


def _probability(value):
    return _finite(value) and 0 <= value <= 1


def coverage_bounds(finished, open_rows, unknown_count, elapsed):
    """Bounds over the exact frozen prediction population, not confidence intervals."""
    total = len(finished) + len(open_rows) + unknown_count
    inside = sum(item["remaining_p10_seconds"] <= actual <= item["remaining_p90_seconds"]
                 for item, actual, _ in finished)
    censored_misses = sum(elapsed > item["remaining_p90_seconds"] for item in open_rows)
    unknown = len(open_rows) - censored_misses + unknown_count
    missed = len(finished) - inside + censored_misses
    # If remaining time is at least elapsed, the smallest possible absolute
    # error is max(0, elapsed - median); missing observations contribute zero
    # to the bound, not a claim of zero actual error.
    minimum_error = sum(abs(item["median_remaining_seconds"] - actual)
                        for item, actual, _ in finished)
    minimum_error += sum(max(0, elapsed - item["median_remaining_seconds"]) for item in open_rows)
    return {
        "frozen_cases": total, "known_inside_range": inside,
        "known_outside_range": missed, "still_open_known_misses": censored_misses,
        "range_membership_unknown": unknown,
        "eventual_range_coverage_lower": round(inside / total, 4) if total else None,
        "eventual_range_coverage_upper": round((inside + unknown) / total, 4) if total else None,
        "eventual_mae_lower_bound_seconds": round(minimum_error / total, 3) if total else None,
        "eventual_mae_upper_bound_seconds": round(minimum_error / total, 3)
            if total and len(finished) == total else None,
        "conditional_on_complete_observation": True,
        "bounds_are_confidence_intervals": False,
    }


def paired_time_score(finished):
    """Compare model and naive median on the identical scored subset."""
    pairs = [(abs(item["median_remaining_seconds"] - actual),
              abs(item["naive_median_remaining_seconds"] - actual))
             for item, actual, _ in finished if _finite(item.get("naive_median_remaining_seconds"))]
    n = len(pairs)
    if not n:
        return None
    model = sum(a for a, _ in pairs) / n
    baseline = sum(b for _, b in pairs) / n
    return {
        "paired_cases": n, "finished_cases_without_baseline": len(finished) - n,
        "model_mae_seconds": round(model, 3), "baseline_mae_seconds": round(baseline, 3),
        "model_minus_baseline_mae_seconds": round(model - baseline, 3),
        "model_better_cases": sum(a < b for a, b in pairs),
        "baseline_better_cases": sum(a > b for a, b in pairs),
        "tied_cases": sum(a == b for a, b in pairs),
    }


def outcome_bounds(finished, unresolved):
    """Brier bounds over cases with both frozen model and baseline risk."""
    known = [(item["outcome_probability"], item["base_rate"], int(bool(y)))
             for item, _, y in finished if y is not None
             and _probability(item.get("outcome_probability")) and _probability(item.get("base_rate"))]
    unknown = [(item["outcome_probability"], item["base_rate"])
               for item in [*unresolved, *(item for item, _, y in finished if y is None)]
               if _probability(item.get("outcome_probability")) and _probability(item.get("base_rate"))]
    n = len(known) + len(unknown)
    if not n:
        return None
    model = sum((p-y)**2 for p, _, y in known)
    base = sum((b-y)**2 for _, b, y in known)
    low_delta = high_delta = model - base
    low_model = high_model = model
    for p, b in unknown:
        errors = (p*p, (1-p)**2)
        differences = (errors[0]-b*b, errors[1]-(1-b)**2)
        low_model += min(errors)
        high_model += max(errors)
        low_delta += min(differences)
        high_delta += max(differences)
    return {
        "paired_frozen_cases": n, "known_outcome_cases": len(known),
        "unknown_outcome_cases": len(unknown),
        "frozen_cases_without_paired_risk": len(finished) + len(unresolved) - n,
        "paired_model_brier_score": round(model / len(known), 5) if known else None,
        "paired_baseline_brier_score": round(base / len(known), 5) if known else None,
        "eventual_model_brier_lower": round(low_model / n, 5),
        "eventual_model_brier_upper": round(high_model / n, 5),
        "eventual_model_minus_baseline_brier_lower": round(low_delta / n, 5),
        "eventual_model_minus_baseline_brier_upper": round(high_delta / n, 5),
        "bounds_are_confidence_intervals": False,
    }
