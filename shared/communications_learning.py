"""Learned multi-label intents from human-reviewed examples (#370 R22).

A small, transparent model: hashed word and character n-gram features (so it
works across languages and scripts without a tokenizer per language), one
logistic-regression classifier per intent, and per-intent Platt calibration fit
on out-of-fold scores. It is pure Python so it runs on every supported install.

Every prediction is per intent: ``present``, ``absent`` or ``uncertain``. A
message can have several intents or none. Probabilities are calibrated on the
reviewer's own examples only; they are estimates, not verified reasons for
contact. Message text is data: nothing in it is ever followed as an instruction.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import math
import random
import re
import unicodedata
from typing import Any, Iterable

FORMAT = "openworkgraph.communications-intent-model.v1"
BUCKETS = 1 << 20
MIN_POSITIVES = 5
MIN_NEGATIVES = 5
MIN_EVAL_SUPPORT = 5
PRESENT_AT = 0.7
ABSENT_AT = 0.3
FOLDS = 5
_WORD = re.compile(r"\w+", re.UNICODE)


class LearningError(ValueError):
    """Safe error; never echoes message text."""


def _bucket(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big") % BUCKETS


def features(text: str) -> dict[int, float]:
    """L2-normalised hashed word unigrams/bigrams and in-word character 3–5 grams."""
    normal = unicodedata.normalize("NFKC", text).casefold()
    words = _WORD.findall(normal)[:2000]
    counts: dict[int, float] = defaultdict(float)
    for word in words:
        counts[_bucket("w:" + word)] += 1.0
        padded = f"<{word}>"
        for n in (3, 4, 5):
            for i in range(max(len(padded) - n + 1, 0)):
                counts[_bucket("c:" + padded[i:i + n])] += 0.5
    for a, b in zip(words, words[1:]):
        counts[_bucket("b:" + a + " " + b)] += 1.0
    if not counts:
        return {}
    scaled = {k: 1.0 + math.log(v) for k, v in counts.items()}
    norm = math.sqrt(sum(v * v for v in scaled.values()))
    return {k: v / norm for k, v in scaled.items()}


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _fit(rows: list[tuple[dict[int, float], int]], *, epochs: int, l2: float, seed: int) -> tuple[dict[int, float], float]:
    """Logistic regression by SGD with class weighting and lazy L2 shrinkage."""
    weights: dict[int, float] = defaultdict(float)
    bias = 0.0
    positives = sum(y for _, y in rows) or 1
    negatives = (len(rows) - positives) or 1
    pos_w, neg_w = len(rows) / (2 * positives), len(rows) / (2 * negatives)
    order = list(range(len(rows)))
    rng = random.Random(seed)
    step = 0
    for epoch in range(epochs):
        rng.shuffle(order)
        rate = 0.5 / (1 + epoch * 0.2)
        for index in order:
            x, y = rows[index]
            step += 1
            z = bias + sum(weights[k] * v for k, v in x.items())
            gradient = (_sigmoid(z) - y) * (pos_w if y else neg_w)
            shrink = 1.0 - rate * l2
            for k, v in x.items():
                weights[k] = weights[k] * shrink - rate * gradient * v
            bias -= rate * gradient
    return {k: round(v, 6) for k, v in weights.items() if abs(v) > 1e-6}, round(bias, 6)


def _score(weights: dict[int, float], bias: float, x: dict[int, float]) -> float:
    return bias + sum(weights.get(k, 0.0) * v for k, v in x.items())


def _platt(scores: list[float], labels: list[int]) -> tuple[float, float]:
    """Platt scaling with Bayesian-smoothed targets, fit by Newton's method."""
    positives = sum(labels)
    negatives = len(labels) - positives
    hi, lo = (positives + 1) / (positives + 2), 1 / (negatives + 2)
    targets = [hi if y else lo for y in labels]
    a, b = 1.0, 0.0
    for _ in range(100):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for s, t in zip(scores, targets):
            p = _sigmoid(a * s + b)
            d = p - t
            w = max(p * (1 - p), 1e-9)
            g_a += d * s
            g_b += d
            h_aa += w * s * s
            h_ab += w * s
            h_bb += w
        h_aa += 1e-6
        h_bb += 1e-6
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (h_bb * g_a - h_ab * g_b) / det
        db = (h_aa * g_b - h_ab * g_a) / det
        a, b = a - da, b - db
        if abs(da) < 1e-8 and abs(db) < 1e-8:
            break
    return round(a, 6), round(b, 6)


def _fold(example_id: str, folds: int) -> int:
    return int(hashlib.sha256(example_id.encode()).hexdigest(), 16) % folds


def train(examples: list[dict[str, Any]], intents: list[str], *, epochs: int = 12, l2: float = 1e-4,
          seed: int = 7) -> dict[str, Any]:
    """Train one calibrated classifier per intent from labelled training examples.

    ``examples`` items have ``example_id``, ``text`` and ``labels`` (intent names).
    Intents with too few positive or negative examples are reported and skipped.
    """
    labelled = [e for e in examples if isinstance(e.get("labels"), list)]
    if len(labelled) < MIN_POSITIVES + MIN_NEGATIVES:
        raise LearningError(f"label at least {MIN_POSITIVES + MIN_NEGATIVES} training examples first")
    rows = [(e["example_id"], features(e["text"]), set(e["labels"])) for e in labelled]
    models, skipped = {}, []
    for intent in intents:
        ys = [1 if intent in labels else 0 for _, _, labels in rows]
        if sum(ys) < MIN_POSITIVES or len(ys) - sum(ys) < MIN_NEGATIVES:
            skipped.append({"intent": intent, "positive_examples": sum(ys), "reason": "insufficient_examples"})
            continue
        data = [(x, y) for (_, x, _), y in zip(rows, ys)]
        # Out-of-fold scores give the calibration map honest (unseen) inputs.
        oof = [0.0] * len(data)
        for k in range(FOLDS):
            train_rows = [d for (eid, _, _), d in zip(rows, data) if _fold(eid, FOLDS) != k]
            if not any(y for _, y in train_rows) or all(y for _, y in train_rows):
                continue
            w, b = _fit(train_rows, epochs=epochs, l2=l2, seed=seed + k)
            for i, (eid, x, _) in enumerate(rows):
                if _fold(eid, FOLDS) == k:
                    oof[i] = _score(w, b, x)
        a, c = _platt(oof, ys)
        weights, bias = _fit(data, epochs=epochs, l2=l2, seed=seed)
        models[intent] = {"weights": {str(k): v for k, v in weights.items()}, "bias": bias,
                          "calibration": {"a": a, "b": c}, "positive_examples": sum(ys),
                          "training_examples": len(ys)}
    if not models:
        raise LearningError(f"no intent has {MIN_POSITIVES}+ positive and {MIN_NEGATIVES}+ negative examples yet")
    return {"format": FORMAT, "intents": models, "skipped_intents": skipped,
            "thresholds": {"present_at": PRESENT_AT, "absent_at": ABSENT_AT},
            "feature_buckets": BUCKETS, "training_example_count": len(rows)}


def _loaded(model: dict[str, Any]) -> dict[str, tuple[dict[int, float], float, float, float]]:
    if not isinstance(model, dict) or model.get("format") != FORMAT:
        raise LearningError("not an OWG intent model")
    out = {}
    for name, item in model["intents"].items():
        out[name] = ({int(k): float(v) for k, v in item["weights"].items()}, float(item["bias"]),
                     float(item["calibration"]["a"]), float(item["calibration"]["b"]))
    return out


def predictor(model: dict[str, Any]):
    """Return ``predict(text) -> {intent: probability}`` for a trained model."""
    loaded = _loaded(model)

    def predict(text: str) -> dict[str, float]:
        x = features(text)
        return {name: _sigmoid(a * _score(w, b, x) + c) for name, (w, b, a, c) in loaded.items()}
    return predict


def decide(probabilities: dict[str, float]) -> dict[str, str]:
    return {name: "present" if p >= PRESENT_AT else "absent" if p <= ABSENT_AT else "uncertain"
            for name, p in probabilities.items()}


def primary_label(decisions: dict[str, str]) -> str:
    """One label for volume/timeline views; the per-intent decisions stay multi-label."""
    present = sorted(n for n, d in decisions.items() if d == "present")
    if len(present) == 1:
        return present[0]
    if present:
        return "multiple intents"
    return "uncertain" if any(d == "uncertain" for d in decisions.values()) else "unclassified"


def _wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.959964
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def _metrics(pairs: list[tuple[float, int]]) -> dict[str, Any]:
    tp = sum(1 for p, y in pairs if p >= PRESENT_AT and y)
    fp = sum(1 for p, y in pairs if p >= PRESENT_AT and not y)
    fn = sum(1 for p, y in pairs if p < PRESENT_AT and y)
    uncertain = sum(1 for p, _ in pairs if ABSENT_AT < p < PRESENT_AT)
    positives = sum(y for _, y in pairs)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else (0.0 if precision == 0 or recall == 0 else None)
    bins: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, y in pairs:
        bins[min(int(p * 10), 9)].append((p, y))
    ece = sum(len(b) / len(pairs) * abs(sum(p for p, _ in b) / len(b) - sum(y for _, y in b) / len(b))
              for b in bins.values()) if pairs else None
    return {"examples": len(pairs), "positive_examples": positives,
            "precision": None if precision is None else round(precision, 4), "precision_interval": _wilson(tp, tp + fp),
            "recall": None if recall is None else round(recall, 4), "recall_interval": _wilson(tp, tp + fn),
            "f1": None if f1 is None else round(f1, 4),
            "uncertain_rate": round(uncertain / len(pairs), 4) if pairs else None,
            "brier_score": round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None,
            "expected_calibration_error": None if ece is None else round(ece, 4),
            "sufficient_support": positives >= MIN_EVAL_SUPPORT and len(pairs) - positives >= MIN_EVAL_SUPPORT}


def evaluate(model: dict[str, Any], holdout: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Per-intent and per-language quality on reviewed examples never used for training."""
    predict = predictor(model)
    rows = [(e.get("language") or "und", set(e["labels"]), predict(e["text"])) for e in holdout
            if isinstance(e.get("labels"), list)]
    report = {"format": "openworkgraph.communications-intent-evaluation.v1", "holdout_examples": len(rows),
              "languages": sorted({lang for lang, _, _ in rows}), "intents": [],
              "thresholds": {"present_at": PRESENT_AT, "absent_at": ABSENT_AT},
              "evaluated_on": "reviewer_labelled_holdout_never_used_for_training",
              "verified_reasons_for_contact": False}
    f1s = []
    for intent in sorted(model["intents"]):
        pairs = [(probs[intent], 1 if intent in labels else 0) for _, labels, probs in rows]
        overall = _metrics(pairs)
        by_language = {}
        for lang in report["languages"]:
            subset = [(probs[intent], 1 if intent in labels else 0) for lg, labels, probs in rows if lg == lang]
            by_language[lang] = _metrics(subset)
        if overall["sufficient_support"] and overall["f1"] is not None:
            f1s.append(overall["f1"])
        report["intents"].append({"intent": intent, **overall, "by_language": by_language})
    report["macro_f1_supported_intents"] = round(sum(f1s) / len(f1s), 4) if f1s else None
    report["supported_intent_count"] = len(f1s)
    return report


__all__ = ["FORMAT", "LearningError", "decide", "evaluate", "features", "predictor", "primary_label", "train"]
