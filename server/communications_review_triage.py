"""Human-only active review queue for consented, redacted communications examples (R22).

Rank existing *unlabelled training-split* examples for reviewer attention, using
only the local keyword taxonomy and an optional current local learned model.
Held-out examples must not be used to select training labels or calibrate this
queue. No label is changed automatically; suggestions are not ground truth.
"""
from __future__ import annotations

from typing import Any

from shared import communications_learning as learning
from shared import communications_mining as mining

from . import communications_review as review

FORMAT = "openworkgraph.communications-active-review.v1"
MAX_LIMIT = 50
MAX_OFFSET = review.MAX_EXAMPLES
PRIORITIES = ("model_keyword_disagreement", "model_uncertain", "keyword_ambiguous",
              "keyword_unclassified", "routine")


def queue(taxonomy: Any, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Return a bounded, deterministic, local-only triage view; never persist predictions."""
    intents = mining.parse_taxonomy(taxonomy)
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise review.ReviewError(f"limit must be from 1 to {MAX_LIMIT}")
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise review.ReviewError(f"offset must be from 0 to {MAX_OFFSET}")
    with review._LOCK:
        data = review._load()  # Retention expiry is enforced by the existing store.
        review._require_enabled(data)
        model = review._load_model()
        fresh, stale_reason = (review._freshness(model, data, [i.name for i in intents])
                               if model is not None else (False, None))
        # Copy only the user's already-redacted records for inference outside the store lock.
        examples = [dict(e) for e in data["examples"] if e.get("labels") is None]
    heldout = sum(e.get("split") == "holdout" for e in examples)
    training = [e for e in examples if e.get("split") == "train"]
    predictor = learning.predictor(model) if model is not None and fresh else None
    ranked = []
    for example in training:
        keyword, scores = mining.classify(example["text"], intents)
        model_label = None
        reasons = []
        if predictor is not None:
            probs = predictor(example["text"])
            decisions = learning.decide(probs)
            model_label = learning.primary_label(decisions)
            if keyword not in ("ambiguous", "unclassified") and model_label not in (
                    "uncertain", "multiple intents") and model_label != keyword:
                reasons.append("model_keyword_disagreement")
            if model_label in ("uncertain", "multiple intents") or "uncertain" in decisions.values():
                reasons.append("model_uncertain")
        if keyword == "ambiguous":
            reasons.append("keyword_ambiguous")
        elif keyword == "unclassified":
            reasons.append("keyword_unclassified")
        priority = next((i for i, kind in enumerate(PRIORITIES) if kind in reasons), len(PRIORITIES) - 1)
        ranked.append({
            "example_id": example["example_id"], "text": example["text"],
            "language": example["language"], "labels": None, "split": "train",
            "created_at": example["created_at"], "expires_at": example["expires_at"],
            "source": example["source"], "keyword_intent": keyword,
            "keyword_matching_intents": sorted(scores),
            "learned_primary_intent": model_label,
            "priority": PRIORITIES[priority], "reasons": reasons,
        })
    ranked.sort(key=lambda row: (PRIORITIES.index(row["priority"]), row["created_at"], row["example_id"]))
    return {
        "format": FORMAT,
        "examples": ranked[offset:offset + limit],
        "total": len(ranked), "total_eligible": len(ranked),
        "flagged_examples": sum(bool(e["reasons"]) for e in ranked),
        "unlabelled_holdout_excluded": heldout, "selected_split": "train",
        "offset": offset, "limit": limit,
        "learned_predictions_used": predictor is not None,
        "model_status": "current" if predictor is not None else "stale" if model is not None else "absent",
        "model_stale_reason": stale_reason if model is not None and not fresh else None,
        "labels_auto_applied": False, "model_retrained": False,
        "source_systems_queried": False, "external_transmission": False,
        "review_texts_are_existing_redacted_opt_in_examples": True,
        "message_text_is_data_not_instructions": True,
        "interpretation": (
            "This human-only queue prioritizes disagreements and uncertainty over existing redacted "
            "training examples. It does not use the sealed holdout for active selection, assign labels, "
            "train a model or verify contact reasons. A low-priority example may still be wrong."
        ),
    }


__all__ = ["queue", "FORMAT", "MAX_LIMIT", "MAX_OFFSET"]
