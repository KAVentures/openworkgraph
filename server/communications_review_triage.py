"""Human-only active review queue for consented, redacted communications examples (R22).

Rank existing *unlabelled training-split* examples for reviewer attention, using
only the local keyword taxonomy and an optional current local learned model.
Held-out examples must not be used to select training labels or calibrate this
queue. No label is changed automatically; suggestions are not ground truth.
"""
from __future__ import annotations

from typing import Any
from collections import Counter
import hashlib
import json
import re

from shared.communications_review_order import diverse_order

from shared import communications_learning as learning
from shared import communications_mining as mining

from . import communications_review as review

FORMAT = "openworkgraph.communications-active-review.v1"
MAX_LIMIT = 50
MAX_OFFSET = review.MAX_EXAMPLES
PRIORITIES = ("model_keyword_disagreement", "model_uncertain", "keyword_ambiguous",
              "keyword_unclassified", "routine")


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,
                                     separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _facets(rows):
    return {name:dict(sorted(Counter(row[name] for row in rows).items()))
            for name in ("language","keyword_intent","priority")}


def queue(taxonomy: Any, *, limit: int = 20, offset: int = 0, order: str = 'priority',
          language: str | None = None, keyword_intent: str | None = None,
          priority: str | None = None, expected_queue_sha256: str | None = None,
          current_taxonomy=None) -> dict[str, Any]:
    """Return a bounded, deterministic, local-only triage view; never persist predictions."""
    intents = mining.parse_taxonomy(taxonomy)
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise review.ReviewError(f"limit must be from 1 to {MAX_LIMIT}")
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise review.ReviewError(f"offset must be from 0 to {MAX_OFFSET}")
    if order not in ('priority', 'diverse'):
        raise review.ReviewError('choose priority or diverse review order')
    for value,name in ((language,"language"),(keyword_intent,"keyword intent"),(priority,"priority")):
        if value is not None and (not isinstance(value,str) or not 1<=len(value)<=120):
            raise review.ReviewError("invalid "+name+" filter")
    if priority is not None and priority not in PRIORITIES:
        raise review.ReviewError("unknown priority filter")
    if expected_queue_sha256 is not None and (not isinstance(expected_queue_sha256,str)
            or not re.fullmatch(r"[a-f0-9]{64}",expected_queue_sha256)):
        raise review.ReviewError("invalid queue digest")
    taxonomy_digest=_digest(taxonomy)
    names=[i.name for i in intents]
    with review._LOCK:
        data = review._load()  # Retention expiry is enforced by the existing store.
        review._require_enabled(data)
        model = review._load_model()
        fresh, stale_reason = (review._freshness(model, data, [i.name for i in intents])
                               if model is not None else (False, None))
        # Copy only the user's already-redacted records for inference outside the store lock.
        examples = [dict(e) for e in data["examples"] if e.get("labels") is None]
        # Labelled inputs affect the existing model freshness, including
        # holdout labels, but are not used as ranking candidates.
        basis=review._training_basis(data["examples"],names)
        model_digest=_digest(model)
        settings_digest=_digest(data["settings"])
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
        priority_index = next((i for i, kind in enumerate(PRIORITIES) if kind in reasons), len(PRIORITIES) - 1)
        ranked.append({
            "example_id": example["example_id"], "text": example["text"],
            "language": example["language"], "labels": None, "split": "train",
            "created_at": example["created_at"], "expires_at": example["expires_at"],
            "source": example["source"], "keyword_intent": keyword,
            "keyword_matching_intents": sorted(scores),
            "learned_primary_intent": model_label,
            "priority": PRIORITIES[priority_index], "reasons": reasons,
        })
    ranked.sort(key=lambda row: (PRIORITIES.index(row["priority"]), row["created_at"], row["example_id"]))
    all_facets=_facets(ranked)
    filters={"language":language,"keyword_intent":keyword_intent,"priority":priority}
    selected=[row for row in ranked if all(value is None or row[key]==value
                                           for key,value in filters.items())]
    if order == "diverse":
        selected = diverse_order(selected)
    # The pin covers all eligible ranked training rows, not just a page.
    # Unlabelled holdout text is not hashed into the returned queue pin.
    # The existing training-basis check covers labelled model inputs.
    queue_sha256=_digest({"format":FORMAT,"taxonomy_sha256":taxonomy_digest,
        "training_basis_sha256":basis,"model_sha256":model_digest,"settings_sha256":settings_digest,
        "training":ranked,"unlabelled_holdout_count":heldout,"order":order,"filters":filters})
    if expected_queue_sha256 is not None and queue_sha256!=expected_queue_sha256:
        raise review.ReviewError("review queue changed; restart from the first page")
    page = selected[offset:offset + limit]
    # Revalidate the candidate population after inference: do not return
    # previously copied text after an observed opt-out, deletion or label edit.
    with review._LOCK:
        latest = review._load()
        review._require_enabled(latest)
        if ([e for e in latest["examples"] if e.get("labels") is None] != examples
                or review._training_basis(latest["examples"],names)!=basis
                or _digest(review._load_model())!=model_digest
                or _digest(latest["settings"])!=settings_digest
                or (current_taxonomy is not None and _digest(current_taxonomy())!=taxonomy_digest)):
            raise review.ReviewError("review examples, model or taxonomy changed during ranking; reload the queue")
    return {
        "format": FORMAT,
        "examples": page,
        "review_order": order,
        "queue_sha256":queue_sha256,"filters":filters,
        "eligible_facets":all_facets,"matching_facets":_facets(selected),
        "matching_count":len(selected),"next_offset":offset+len(page) if page and offset+len(page)<len(selected) else None,
        "eligible_by_declared_language": dict(sorted(Counter(e["language"] for e in ranked).items())),
        "page_by_declared_language": dict(sorted(Counter(e["language"] for e in page).items())),
        "language_inferred": False,
        "sampling_representativeness_verified": False,
        "total": len(selected), "total_eligible": len(ranked),
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
