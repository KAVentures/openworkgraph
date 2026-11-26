"""Prospective scoring of frozen time predictions without dropping open cases.

Identification bounds, not confidence intervals. No case is assumed finished,
and no unobserved completion time is imputed.
"""
from math import fsum, isfinite


def _valid(x):
    return type(x) in (int, float) and isfinite(x) and x >= 0


def _mean(xs):
    return fsum(xs) / len(xs) if xs else None


def _difference(model, baseline, actual):
    # The difference of absolute errors is monotone between the estimates,
    # constant outside them. Avoid cancellation for very long durations.
    if actual <= min(model, baseline):
        return model - baseline
    if actual >= max(model, baseline):
        return baseline - model
    return (model - actual) + (baseline - actual) if model >= baseline else (
        (actual - model) + (actual - baseline))


def paired_error_bounds(finished, opened, unknown, elapsed):
    """Sharp model-minus-baseline absolute-error bounds on paired frozen cases.

    For a censored completion t >= elapsed, the extrema occur at elapsed
    and at/after both predictions. For unknown evidence use t >= 0.
    """
    lows, highs, known = [], [], []
    counts = {"finished": 0, "still_open": 0, "unknown_observation": 0}
    for label, rows in (("finished", [(row, actual) for row, actual, _ in finished]),
                        ("still_open", [(row, elapsed) for row in opened]),
                        ("unknown_observation", [(row, 0) for row in unknown])):
        for row, actual in rows:
            m = row.get("median_remaining_seconds")
            b = row.get("naive_median_remaining_seconds")
            if not (_valid(m) and _valid(b) and _valid(actual)):
                continue
            value = _difference(m, b, actual)
            if label == "finished":
                lo = hi = value
                known.append(value)
            else:
                tail = b - m
                lo, hi = min(value, tail), max(value, tail)
            lows.append(lo)
            highs.append(hi)
            counts[label] += 1
    total = len(finished) + len(opened) + len(unknown)
    n = len(lows)
    lower, upper = _mean(lows), _mean(highs)
    return {"frozen_cases": total, "paired_cases": n,
            "cases_without_valid_pair": total-n, "paired_case_observation": counts,
            "finished_paired_mean_difference_seconds": _mean(known),
            "eventual_mean_absolute_error_difference_lower_seconds": lower,
            "eventual_mean_absolute_error_difference_upper_seconds": upper,
            "model_better_cases_lower": sum(hi < 0 for hi in highs),
            "model_better_cases_upper": sum(lo < 0 for lo in lows),
            "baseline_better_cases_lower": sum(lo > 0 for lo in lows),
            "baseline_better_cases_upper": sum(hi > 0 for hi in highs),
            "tied_cases_lower": sum(lo == hi == 0 for lo, hi in zip(lows, highs)),
            "tied_cases_upper": sum(lo <= 0 <= hi for lo, hi in zip(lows, highs)),
            "comparison": ("model_always_lower_error" if upper is not None and upper < 0 else
                           "baseline_always_lower_error" if lower is not None and lower > 0 else
                           "equal_error" if lower == upper == 0 else
                           "unresolved" if n else "unavailable"),
            "conditional_on_complete_observation": True,
            "bounds_are_confidence_intervals": False}


def _pinball(q, actual, probability):
    return probability * (actual-q) if actual >= q else (1-probability) * (q-actual)


def proper_time_scores(finished, opened, unknown, elapsed):
    """Pinball quantile and central 80% interval scores in seconds.

    For an unresolved row the attainable minimum is zero pinball loss or
    interval width, except when its censoring horizon exceeds the quantile
    or interval. There is no finite upper score bound until all are observed.
    """
    total = len(finished) + len(opened) + len(unknown)
    quantiles = []
    for key, p in (("remaining_p10_seconds", .1), ("median_remaining_seconds", .5),
                   ("remaining_p90_seconds", .9)):
        observed = [_pinball(row[key], actual, p) for row, actual, _ in finished
                    if _valid(row.get(key)) and _valid(actual)]
        censored = [p * max(0, elapsed-row[key]) for row in opened
                    if _valid(row.get(key)) and _valid(elapsed)]
        missing = sum(_valid(row.get(key)) for row in unknown)
        n = len(observed)+len(censored)+missing
        lower = fsum(observed+censored)/n if n else None
        quantiles.append({"quantile": p, "eligible_cases": n,
                          "ineligible_cases": total-n, "finished_cases": len(observed),
                          "censored_cases": len(censored), "unknown_cases": missing,
                          "finished_mean_pinball_seconds": _mean(observed),
                          "eventual_mean_pinball_lower_seconds": lower,
                          "eventual_mean_pinball_upper_seconds": lower
                          if n and len(observed)==n else None})

    def interval(row):
        l, u = row.get("remaining_p10_seconds"), row.get("remaining_p90_seconds")
        return (l, u) if _valid(l) and _valid(u) and l <= u else None

    widths, actual_scores, lower_scores = [], [], []
    early, late, open_misses, open_n, missing_n = 0, 0, 0, 0, 0
    for row, actual, _ in finished:
        pair = interval(row)
        if pair is None or not _valid(actual):
            continue
        l, u = pair
        width = u-l
        score = width + 10*max(0, l-actual) + 10*max(0, actual-u)
        widths.append(width)
        actual_scores.append(score)
        lower_scores.append(score)
        early += actual < l
        late += actual > u
    for row in opened:
        pair = interval(row)
        if pair is None or not _valid(elapsed):
            continue
        l, u = pair
        widths.append(u-l)
        lower_scores.append(u-l + 10*max(0, elapsed-u))
        open_misses += elapsed > u
        open_n += 1
    for row in unknown:
        pair = interval(row)
        if pair is None:
            continue
        l, u = pair
        widths.append(u-l)
        lower_scores.append(u-l)
        missing_n += 1
    n = len(widths)
    lower = _mean(lower_scores)
    return {"quantiles": quantiles, "interval_80": {
        "eligible_cases": n, "ineligible_cases": total-n,
        "finished_cases": len(actual_scores), "censored_cases": open_n,
        "unknown_cases": missing_n, "mean_frozen_width_seconds": _mean(widths),
        "finished_early_misses": early, "finished_late_misses": late,
        "censored_known_late_misses": open_misses,
        "finished_mean_interval_score_seconds": _mean(actual_scores),
        "eventual_mean_interval_score_lower_seconds": lower,
        "eventual_mean_interval_score_upper_seconds": lower
        if n and len(actual_scores)==n else None},
        "lower_scores_are_better": True, "conditional_on_complete_observation": True,
        "bounds_are_confidence_intervals": False}
