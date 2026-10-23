"""Paired uncertainty for the reviewed three-arm product-value comparison.

The pre-registered decision in ``continuity.scoring.decision`` compares point
estimates against large engineering thresholds. A claim of demonstrated
benefit additionally needs paired uncertainty on the same episodes, so this
module reports an exact McNemar (sign) test on discordant paired outcomes and
a deterministic paired bootstrap interval for the rate difference. It never
changes the existing decision.
"""
from __future__ import annotations

from math import comb
import random
from typing import Any

BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 20261023
ALPHA = 0.05


def exact_sign_test(gains: int, losses: int) -> float:
    """Two-sided exact McNemar p-value from discordant pair counts."""
    n = gains + losses
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(gains, losses) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_bootstrap_interval(
    baseline: list[bool], treatment: list[bool], *,
    resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Percentile 95% interval for mean(treatment) - mean(baseline), resampling episodes."""
    if len(baseline) != len(treatment) or not baseline:
        raise ValueError("paired outcomes must be non-empty and the same length")
    n = len(baseline)
    diffs = [int(t) - int(b) for b, t in zip(baseline, treatment)]
    rng = random.Random(seed)
    estimates = sorted(
        sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples)
    )
    low = estimates[int((ALPHA / 2) * resamples)]
    high = estimates[min(resamples - 1, int((1 - ALPHA / 2) * resamples))]
    return round(low, 6), round(high, 6)


def paired_binary(baseline: list[bool], treatment: list[bool]) -> dict[str, Any]:
    gains = sum(1 for b, t in zip(baseline, treatment) if t and not b)
    losses = sum(1 for b, t in zip(baseline, treatment) if b and not t)
    low, high = paired_bootstrap_interval(baseline, treatment)
    return {
        "paired_episodes": len(baseline),
        "treatment_only_true": gains,
        "baseline_only_true": losses,
        "rate_difference": round((sum(treatment) - sum(baseline)) / len(baseline), 6),
        "bootstrap_95_interval": [low, high],
        "exact_mcnemar_p_value": round(exact_sign_test(gains, losses), 6),
    }


def compare_arms(baseline: list[dict[str, Any]], treatment: list[dict[str, Any]]) -> dict[str, Any]:
    """Paired statistics for two arms whose reviews are aligned by episode order."""
    if [row["episode_id"] for row in baseline] != [row["episode_id"] for row in treatment]:
        raise ValueError("arms must be aligned on identical episode order")
    success = paired_binary([bool(r["continuation_success"]) for r in baseline],
                            [bool(r["continuation_success"]) for r in treatment])
    harm = paired_binary([bool(r["consequential_wrong_action"]) for r in baseline],
                         [bool(r["consequential_wrong_action"]) for r in treatment])
    supported = (success["bootstrap_95_interval"][0] > 0
                 and success["exact_mcnemar_p_value"] < ALPHA
                 and harm["treatment_only_true"] <= harm["baseline_only_true"])
    return {
        "continuation_success": success,
        "consequential_wrong_action": harm,
        "success_gain_statistically_supported": supported,
        "method": {
            "test": "exact two-sided McNemar on discordant episode pairs",
            "interval": f"paired percentile bootstrap, {BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}",
            "alpha": ALPHA,
            "harm_rule": "treatment may not add consequential wrong actions",
        },
    }


__all__ = ["exact_sign_test", "paired_bootstrap_interval", "paired_binary", "compare_arms"]
