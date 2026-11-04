"""Consent-gated corpus-discriminative category matching for existing review candidates.

Uses only literal, previously approved opaque IDs in observation metadata.
No text inference, provider access, or canonical membership writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

from .reconstruction_review_queue import prioritized_review_queue
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS
from .reconstruction_semantic_assignment import semantic_assignment, MAX_SCORES
from .reconstruction_tag_scoring import _TAG


def discriminative_category_proposals(
    events: list[dict[str, Any]], *, consent: bool = False,
    minimum_shared: int = 2, max_document_fraction: float = 0.75,
) -> dict[str, Any]:
    """Frequency-weighted Jaccard with conservative abstention and global matching.

    Counts distinct event IDs, never repeated observations as independent evidence.
    Frequency estimates use all valid consented metadata, not only candidate pairs.
    """
    if consent is not True:
        raise PermissionError("explicit local structured-category consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded event list required")
    if type(minimum_shared) is not int or not 2 <= minimum_shared <= 8:
        raise ValueError("minimum_shared must be 2..8")
    if type(max_document_fraction) is not float or not 0.5 <= max_document_fraction <= 1.0:
        raise ValueError("max_document_fraction must be float in [0.5, 1]")
    tags: dict[str, set[str]] = {}
    rejected: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event objects required")
        eid = event.get("event_id")
        if not isinstance(eid, str) or not 1 <= len(eid) <= 160:
            raise ValueError("bounded literal event IDs required")
        metadata = event.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            rejected.add(eid)
            continue
        values = (metadata or {}).get("semantic_category_ids", [])
        if not isinstance(values, list) or len(values) > 16 or any(
            not isinstance(v, str) or not _TAG.fullmatch(v) for v in values
        ):
            rejected.add(eid)
            continue
        if eid in tags:
            rejected.add(eid)
        else:
            tags[eid] = set(values)
    for eid in rejected:
        tags.pop(eid, None)
    documents = len(tags)
    frequency = Counter(tag for values in tags.values() for tag in values)
    # A tiny sample cannot establish a meaningful background prevalence.
    suppressed = {tag for tag, count in frequency.items()
                  if documents >= 4 and count / documents >= max_document_fraction}
    weights = {tag: 1.0 + math.log((documents + 1) / (count + 1))
               for tag, count in frequency.items() if tag not in suppressed}
    queue = prioritized_review_queue(events, limit=500)
    if queue["truncated"]:
        raise ValueError("source candidate queue truncated")
    pairs: dict[tuple[str, str], float] = {}
    for row in queue["queue"]:
        risks = set(row["risk_reasons"])
        if not ((row.get("review_status") == "inspect" and not risks) or
                (row.get("review_status") == "conflicted" and
                 risks == {"competing_candidate_components"})):
            continue
        a, b = row["event_id"], row["toward_event_id"]
        left, right = tags.get(a, set()) - suppressed, tags.get(b, set()) - suppressed
        common = left & right
        if len(common) < minimum_shared:
            continue
        denominator = sum(weights[t] for t in left | right)
        if denominator <= 0:
            continue
        score = sum(weights[t] for t in common) / denominator
        key = tuple(sorted((a, b)))
        pairs[key] = max(pairs.get(key, 0.0), min(1.0, score))
    if len(pairs) > MAX_SCORES:
        raise ValueError("too many eligible category-scored pairs")
    result = semantic_assignment(events, {
        "version": 1, "evidence_sha256": queue["evidence_sha256"],
        "pairs": [{"event_id": a, "toward_event_id": b, "score": score}
                  for (a, b), score in sorted(pairs.items())],
    }, consent=True)
    result.update({
        "scoring_method": "local_corpus_discriminative_categories_v1",
        "category_bearing_events": sum(bool(v) for v in tags.values()),
        "rejected_category_event_ids": len(rejected),
        "suppressed_ubiquitous_category_count": len(suppressed),
        "scored_candidate_pairs": len(pairs),
        "minimum_shared_discriminative_categories": minimum_shared,
        "automatic_merge": False, "semantic_model_used": False,
        "calibrated_probability": False,
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Local discriminative-category review proposals")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--consent-local-categories", action="store_true")
    parser.add_argument("--minimum-shared", type=int, default=2)
    args = parser.parse_args()
    if not args.consent_local_categories:
        parser.error("explicit --consent-local-categories required")
    if not args.events.is_file() or args.events.stat().st_size > MAX_BYTES:
        parser.error("missing or oversized local JSON")
    result = discriminative_category_proposals(
        json.loads(args.events.read_text(encoding="utf-8")),
        consent=True, minimum_shared=args.minimum_shared,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
