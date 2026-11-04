"""Independent, consented category corroboration across deterministic source components.

Only already linked observations provide corroboration; ambiguous edges never do.
Outputs remain review proposals, not task identity or automatic membership.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from typing import Any

from .work_item_links import link_observations
from .reconstruction_review_queue import prioritized_review_queue
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS
from .reconstruction_semantic_assignment import semantic_assignment, MAX_SCORES
from .reconstruction_tag_scoring import _TAG


def component_category_proposals(events: list[dict[str, Any]], *, consent: bool = False) -> dict[str, Any]:
    """Score candidate components using categories repeated in distinct source events.

    A shared category must be independently observed twice in EACH component.
    Ubiquitous categories are excluded. Global assignment and existing risk
    vetoes remain authoritative for which candidates may be proposed.
    """
    if consent is not True:
        raise PermissionError("explicit local structured-category consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded event list required")
    tags: dict[str, set[str]] = {}
    invalid: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event objects required")
        eid = event.get("event_id")
        if not isinstance(eid, str) or not 1 <= len(eid) <= 160:
            raise ValueError("bounded literal event ID required")
        metadata = event.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            invalid.add(eid)
            continue
        values = (metadata or {}).get("semantic_category_ids", [])
        if not isinstance(values, list) or len(values) > 16 or any(
            not isinstance(v, str) or not _TAG.fullmatch(v) for v in values
        ):
            invalid.add(eid)
            continue
        if eid in tags:
            invalid.add(eid)
        else:
            tags[eid] = set(values)
    for eid in invalid:
        tags.pop(eid, None)

    linked = link_observations(events)
    membership = {eid: root for root, ids in linked["members"].items() for eid in ids}
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for eid, values in tags.items():
        if eid in membership:
            counts[membership[eid]].update(values)
    supported = {root: {tag for tag, n in counter.items() if n >= 2}
                 for root, counter in counts.items()}
    ncomponents = len(linked["members"])
    frequency = Counter(tag for values in supported.values() for tag in values)
    excluded = {tag for tag, n in frequency.items()
                if ncomponents >= 4 and n / ncomponents >= 0.75}
    weights = {tag: 1 + math.log((ncomponents + 1) / (n + 1))
               for tag, n in frequency.items() if tag not in excluded}

    queue = prioritized_review_queue(events, limit=500)
    if queue["truncated"]:
        raise ValueError("candidate queue truncated")
    scores: dict[tuple[str, str], float] = {}
    for row in queue["queue"]:
        risks = set(row["risk_reasons"])
        if not ((row["review_status"] == "inspect" and not risks) or
                (row["review_status"] == "conflicted" and
                 risks == {"competing_candidate_components"})):
            continue
        a, b = row["event_id"], row["toward_event_id"]
        ra, rb = membership.get(a), membership.get(b)
        if ra is None or rb is None or ra == rb:
            continue
        left = supported.get(ra, set()) - excluded
        right = supported.get(rb, set()) - excluded
        common = left & right
        if len(common) < 2:
            continue
        denom = sum(weights[t] for t in left | right)
        if denom <= 0:
            continue
        key = tuple(sorted((a, b)))
        scores[key] = max(scores.get(key, 0), sum(weights[t] for t in common) / denom)
    if len(scores) > MAX_SCORES:
        raise ValueError("too many eligible component scores")
    result = semantic_assignment(events, {
        "version": 1, "evidence_sha256": queue["evidence_sha256"],
        "pairs": [{"event_id": a, "toward_event_id": b, "score": min(1.0, score)}
                  for (a, b), score in sorted(scores.items())],
    }, consent=True)
    result.update({
        "scoring_method": "independently_corroborated_component_categories_v1",
        "scored_candidate_pairs": len(scores),
        "corroborated_source_components": sum(bool(v) for v in supported.values()),
        "rejected_category_event_ids": len(invalid),
        "excluded_ubiquitous_categories": len(excluded),
        "minimum_independent_observations_per_component": 2,
        "minimum_shared_categories": 2,
        "automatic_merge": False, "semantic_model_used": False,
        "calibrated_probability": False,
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Corroborated component-category review proposals")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--consent-local-categories", action="store_true")
    args = parser.parse_args()
    if not args.consent_local_categories:
        parser.error("explicit --consent-local-categories required")
    if not args.events.is_file() or args.events.stat().st_size > MAX_BYTES:
        parser.error("missing or oversized local JSON")
    print(json.dumps(component_category_proposals(
        json.loads(args.events.read_text(encoding="utf-8")), consent=True
    ), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
