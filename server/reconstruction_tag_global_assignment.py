"""Global optimization of consented opaque-tag reconstruction candidates.

Only production review candidates are scored. This module never creates case
membership, reads work text, invokes a provider, or changes canonical events.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .reconstruction_review_queue import prioritized_review_queue
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS
from .reconstruction_semantic_assignment import semantic_assignment, MAX_SCORES
from .reconstruction_tag_scoring import _TAG

_UNSAFE = {"conflicting_explicit_records", "missing_or_cross_scope", "tainted_source_evidence"}


def globally_tagged_proposals(
    events: list[dict[str, Any]],
    tags: dict[str, list[str]],
    *,
    consent: bool = False,
    minimum_shared: int = 2,
) -> dict[str, Any]:
    """Choose globally optimal nonoverlapping proposals; abstain on close ties.

    Scores are Jaccard similarities of operator-approved opaque category IDs,
    *not* calibrated probabilities or proof of a business-case identity.
    """
    if consent is not True:
        raise PermissionError("explicit local semantic-tag consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded events list required")
    if not isinstance(tags, dict) or len(tags) > MAX_EVENTS:
        raise ValueError("bounded tag map required")
    if type(minimum_shared) is not int or not 2 <= minimum_shared <= 8:
        raise ValueError("minimum_shared must be 2..8")
    normalized: dict[str, set[str]] = {}
    for event_id, values in tags.items():
        if not isinstance(event_id, str) or len(event_id) > 160:
            raise ValueError("invalid event ID")
        if not isinstance(values, list) or len(values) > 16 or any(
            not isinstance(tag, str) or not _TAG.fullmatch(tag) for tag in values
        ):
            raise ValueError("tags must be bounded opaque category IDs")
        normalized[event_id] = set(values)

    queue = prioritized_review_queue(events, limit=500)
    if queue["truncated"]:
        raise ValueError("candidate queue truncated; cannot safely optimize")
    scores: dict[tuple[str, str], float] = {}
    for row in queue["queue"]:
        risks = set(row["risk_reasons"])
        if risks & _UNSAFE or (risks and risks != {"competing_candidate_components"}):
            continue
        if row.get("review_status") not in ("inspect", "conflicted"):
            continue
        left, right = row["event_id"], row["toward_event_id"]
        a, b = normalized.get(left, set()), normalized.get(right, set())
        common = len(a & b)
        if common < minimum_shared:
            continue
        score = common / len(a | b)
        if not math.isfinite(score):
            raise ValueError("nonfinite score")
        key = tuple(sorted((left, right)))
        scores[key] = max(scores.get(key, 0.0), score)
    if len(scores) > MAX_SCORES:
        raise ValueError("too many scored candidate pairs")
    result = semantic_assignment(events, {
        "version": 1,
        "evidence_sha256": queue["evidence_sha256"],
        "pairs": [
            {"event_id": a, "toward_event_id": b, "score": score}
            for (a, b), score in sorted(scores.items())
        ],
    }, consent=True)
    result["scoring_method"] = "consented_opaque_tag_jaccard_global_v1"
    result["tag_scored_pairs"] = len(scores)
    result["semantic_model_used"] = False
    result["calibrated_probability"] = False
    result["automatic_merge"] = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Local global opaque-tag review proposals (no writes)")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--tags", type=Path, required=True)
    parser.add_argument("--consent-local-tags", action="store_true")
    args = parser.parse_args()
    if not args.consent_local_tags:
        parser.error("--consent-local-tags required")
    for path in (args.events, args.tags):
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            parser.error("missing or oversized local JSON input")
    result = globally_tagged_proposals(
        json.loads(args.events.read_text(encoding="utf-8")),
        json.loads(args.tags.read_text(encoding="utf-8")),
        consent=True,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
