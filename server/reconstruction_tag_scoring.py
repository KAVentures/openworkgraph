"""Opt-in, local semantic-tag corroboration of existing ambiguous reconstruction candidates.

Tags are operator-supplied opaque category IDs, not captured text. Never joins cases.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .reconstruction_review_queue import prioritized_review_queue
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS

_TAG = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
_UNSAFE = {"conflicting_explicit_records", "missing_or_cross_scope", "tainted_source_evidence"}


def tagged_review_proposals(
    events: list[dict[str, Any]],
    tags: dict[str, list[str]],
    *,
    consent: bool = False,
    limit: int = 100,
    minimum_shared: int = 2,
    margin: float = 0.15,
) -> dict[str, Any]:
    """Score only source-grounded candidates; abstain on competing near-ties.

    This is local controlled-vocabulary overlap, not calibrated LLM inference.
    """
    if consent is not True:
        raise PermissionError("explicit local semantic-tag review consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded event list required")
    if not isinstance(tags, dict) or len(tags) > MAX_EVENTS:
        raise ValueError("bounded event-to-tags map required")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500")
    if type(minimum_shared) is not int or not 2 <= minimum_shared <= 8:
        raise ValueError("minimum_shared must be 2..8")
    if not isinstance(margin, (int, float)) or isinstance(margin, bool) or not 0 <= margin <= 1:
        raise ValueError("margin must be 0..1")
    normalized: dict[str, set[str]] = {}
    for event_id, values in tags.items():
        if not isinstance(event_id, str) or len(event_id) > 160:
            raise ValueError("invalid event ID")
        if not isinstance(values, list) or len(values) > 16 or any(
            not isinstance(v, str) or not _TAG.fullmatch(v) for v in values
        ):
            raise ValueError("tags must be bounded opaque category identifiers")
        normalized[event_id] = set(values)
    # A full, untruncated source queue is required to identify competing alternatives.
    source = prioritized_review_queue(events, limit=500)
    if source["truncated"]:
        raise ValueError("source candidate queue truncated; refusing incomplete competition")
    candidates = []
    for row in source["queue"]:
        reasons = set(row["risk_reasons"])
        if reasons & _UNSAFE:
            continue
        left, right = row["event_id"], row["toward_event_id"]
        a, b = normalized.get(left, set()), normalized.get(right, set())
        common = len(a & b)
        if common < minimum_shared:
            continue
        score = common / len(a | b)
        candidates.append((score, left, right, row["relation"]))
    candidates.sort(key=lambda r: (-r[0], r[1], r[2], r[3]))
    selected = []
    occupied: set[str] = set()
    for score, left, right, relation in candidates:
        if left in occupied or right in occupied:
            continue
        alternatives = [s for s, a, b, _ in candidates if (a, b) != (left, right)
                        and (left in (a, b) or right in (a, b))]
        if alternatives and score - max(alternatives) < margin:
            continue
        occupied.update((left, right))
        selected.append({
            "event_id": left, "toward_event_id": right, "relation": relation,
            "tag_overlap_score": round(score, 6),
            "review_required": True, "automatic_merge": False,
        })
    return {
        "version": 1, "evidence_sha256": source["evidence_sha256"],
        "proposals": selected[:limit], "total_proposals": len(selected),
        "truncated": len(selected) > limit, "canonical_membership_unchanged": True,
        "authoritative": False, "calibrated_probability": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Local consented semantic-tag candidate scoring")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--tags", required=True, type=Path)
    parser.add_argument("--consent-local-tags", action="store_true")
    args = parser.parse_args()
    if not args.consent_local_tags:
        parser.error("--consent-local-tags required")
    for path in (args.events, args.tags):
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            parser.error("missing or oversized input")
    print(json.dumps(tagged_review_proposals(
        json.loads(args.events.read_text(encoding="utf-8")),
        json.loads(args.tags.read_text(encoding="utf-8")), consent=True,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
