"""Moment-matched service models. Recorded gaps are not service durations."""
from __future__ import annotations

import math
import random
from typing import Any

DISTRIBUTION = "measured_gamma"
ASSUMPTION = (
    "Measured variability fits a Gamma shape to the coefficient of variation of selected "
    "event gaps; chosen activity minutes set its mean. Gaps include waiting and are not "
    "observed processing times. Fewer than 10 gaps or unsupported dispersion use an explicit "
    "exponential fallback; constant gaps use fixed times."
)


def describe(count: int, mean: float | None, deviation: float | None) -> dict[str, Any]:
    cv = None
    if mean is not None and deviation is not None and mean > 0:
        candidate = deviation / mean
        if math.isfinite(candidate) and candidate >= 0:
            cv = candidate
    base = {"observed_gaps": count, "coefficient_of_variation": cv}
    if count < 10:
        return {**base, "model": "exponential", "reason": "insufficient_gaps"}
    if mean == 0 and deviation == 0:
        return {**base, "model": "fixed", "reason": "all_gaps_zero"}
    if cv == 0:
        return {**base, "model": "fixed", "reason": "constant_gaps"}
    if cv is None or not 0.01 <= cv <= 10:
        return {**base, "model": "exponential", "reason": "dispersion_outside_supported_range"}
    return {**base, "model": "gamma", "shape": 1 / cv**2, "reason": "moment_matched"}


def service_time(mean_seconds: float, model: dict[str, Any], rng: random.Random) -> float:
    if mean_seconds == 0 or model["model"] == "fixed":
        return mean_seconds
    if model["model"] == "gamma":
        shape = model["shape"]
        return rng.gammavariate(shape, mean_seconds / shape)
    return rng.expovariate(1 / mean_seconds)
