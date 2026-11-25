"""Descriptive, fixed-holdout probability reliability. No threshold fitting."""
from collections import Counter
import math

BINS = 10
MIN_CLASS_SUPPORT = 5
EPSILON = 1e-15


def _mean(values):
    return math.fsum(values) / len(values) if values else None


def _loss(p, y):
    p = min(1-EPSILON, max(EPSILON, p))
    return -math.log(p if y else 1-p)


def _ranking(rows, positives, negatives):
    # Group identical scores before counting wins or precision increments.
    # AUROC gives half credit to ties; AP is the step integral of precision.
    if not positives or not negatives:
        return None, None
    groups = {}
    for p, y in rows:
        bucket = groups.setdefault(p, [0, 0])
        bucket[int(y)] += 1
    seen_pos = seen_neg = wins = 0
    ap = 0.0
    for p in sorted(groups, reverse=True):
        neg, pos = groups[p]
        wins += neg * seen_pos + 0.5 * pos * neg
        seen_pos += pos
        seen_neg += neg
        ap += (pos / positives) * (seen_pos / (seen_pos + seen_neg))
    return wins / (positives * negatives), ap


def report(rows, training_positives, training_count):
    """rows: (probability or None, reviewer label), one per held-out message."""
    observed = [(p, bool(y)) for p, y in rows if p is not None]
    n = len(observed)
    positives = sum(y for _, y in observed)
    negatives = n - positives
    prior = training_positives / training_count if training_count else None
    bins = [[] for _ in range(BINS)]
    for p, y in observed:
        if not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("probabilities must be finite and bounded")
        bins[min(BINS-1, int(p * BINS))].append((p, y))
    reliability = []
    weighted_error = 0.0
    for i, group in enumerate(bins):
        mean_p = _mean([p for p, _ in group])
        rate = _mean([y for _, y in group])
        if group:
            weighted_error += len(group) * abs(mean_p - rate)
        reliability.append({"lower": i/BINS, "upper": (i+1)/BINS,
                            "count": len(group), "positives": sum(y for _, y in group),
                            "mean_probability": mean_p if len(group) >= 5 else None,
                            "observed_positive_rate": rate if len(group) >= 5 else None})
    brier = _mean([(p-y)**2 for p, y in observed])
    base_brier = _mean([(prior-y)**2 for _, y in observed]) if prior is not None else None
    auc, ap = _ranking(observed, positives, negatives)
    return {"holdout_examples": len(rows), "scored_examples": n,
            "missing_probabilities": len(rows)-n,
            "scored_positives": positives, "scored_negatives": negatives,
            "training_examples": training_count, "training_positive_rate": prior,
            "brier_score": brier, "training_prior_brier_score": base_brier,
            "brier_skill_against_training_prior": (1-brier/base_brier
                if brier is not None and base_brier else None),
            "log_loss_clipped": _mean([_loss(p, y) for p, y in observed]),
            "training_prior_log_loss_clipped": (_mean([_loss(prior, y) for _, y in observed])
                if prior is not None else None),
            "ece_fixed_10_bins": weighted_error/n if n else None,
            "auroc": auc, "average_precision": ap,
            "observed_positive_rate": positives/n if n else None,
            "sufficient_scored_support": positives >= MIN_CLASS_SUPPORT and negatives >= MIN_CLASS_SUPPORT,
            "bins": reliability}


def summarize(rows, names, trained, training):
    """Only trained labels get probability metrics; absent scores stay missing."""
    counts = Counter(name for example in training for name in set(example["labels"]))
    per_intent = []
    for name in names:
        if name not in trained:
            per_intent.append({"intent": name, "trained": False, "metrics": None})
            continue
        metrics = report([(probabilities.get(name), name in truth)
                          for truth, _, _, probabilities in rows], counts[name], len(training))
        per_intent.append({"intent": name, "trained": True, "metrics": metrics})
    supported = [x["metrics"] for x in per_intent if x["metrics"] is not None
                 and x["metrics"]["sufficient_scored_support"]
                 and not x["metrics"]["missing_probabilities"]]
    keys = ("brier_score", "brier_skill_against_training_prior", "log_loss_clipped",
            "ece_fixed_10_bins", "auroc", "average_precision")
    return {"per_intent": per_intent, "macro_supported_intents": len(supported),
            "macro": {k: _mean([m[k] for m in supported if m[k] is not None]) for k in keys},
            "macro_metric_support": {k: sum(m[k] is not None for m in supported) for k in keys},
            "bin_count": BINS, "minimum_class_support": MIN_CLASS_SUPPORT,
            "minimum_bin_display_count": 5, "log_loss_clip_epsilon": EPSILON,
            "thresholds_selected": False, "external_calibration_verified": False}
