"""Local corroboration scoring of production reconstruction review candidates.

This is a conservative *structural* baseline, not semantic understanding.
Only independently observed candidate relation types can corroborate a pair.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .reconstruction_review_queue import prioritized_review_queue
from .work_item_links import link_observations
from .reconstruction_semantic_assignment import semantic_assignment
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS

# Distinct relation families are necessary; repeated observations of the same
# weak signal are not independent corroboration.
FAMILY = {
    "same_tab_multiplexed": "tab",
    "same_tab_unreferenced": "tab",
    "surface_continuity": "surface",
}
MAX_PAIRS = 100


def corroborated_assignment(events: list[dict[str, Any]], *, consent: bool = False) -> dict[str, Any]:
    """Return human-review proposals only when independent weak signals agree.

    The production review queue retains all source-scope and taint vetoes;
    global assignment enforces competition, stale digest and one-to-one rules.
    """
    if consent is not True:
        raise ValueError("explicit local reconstruction review consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded events list required")
    queue = prioritized_review_queue(events, limit=500)
    if queue["truncated"]:
        raise ValueError("candidate queue truncated")
    # Evidence families corroborate *component* pairs, not necessarily the
    # same raw event pair. One component may contain several exact-record or
    # observed-transfer events; independent weak observations can originate
    # from different members without sharing identical endpoint event IDs.
    linked = link_observations(events)
    roots = {event_id: root for root, members in linked["members"].items()
             for event_id in members}
    families: dict[tuple[str, str], set[str]] = defaultdict(set)
    representatives: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for row in queue["queue"]:
        if row["risk_reasons"] and set(row["risk_reasons"]) != {"competing_candidate_components"}:
            continue
        family = FAMILY.get(row["relation"])
        if not family:
            continue
        a, b = row["event_id"], row["toward_event_id"]
        if a not in roots or b not in roots or roots[a] == roots[b]:
            continue
        key = tuple(sorted((roots[a], roots[b])))
        families[key].add(family)
        representatives[key].append(tuple(sorted((a, b))))
    # One scored representative per independently corroborated component pair.
    # Do not count repetitions within a signal family as new evidence.
    scored = [
        {"event_id": pair[0], "toward_event_id": pair[1], "score": 0.84}
        for key, signals in sorted(families.items()) if len(signals) >= 2
        for pair in [min(representatives[key])]
    ]
    if len(scored) > MAX_PAIRS:
        raise ValueError("too many corroborated pairs")
    result = semantic_assignment(events, {
        "version": 1, "evidence_sha256": queue["evidence_sha256"],
        "pairs": scored,
    }, consent=True)
    result["scoring_method"] = "independent_structural_relation_families_v1"
    result["corroborated_pairs"] = len(scored)
    result["unscored_weak_pairs"] = len(families) - len(scored)
    result["semantic_model_used"] = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Local corroborated reconstruction proposals; no writes")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--consent", action="store_true")
    args = parser.parse_args()
    if not args.events.is_file() or args.events.stat().st_size > MAX_BYTES:
        parser.error("missing or oversized observations")
    print(json.dumps(corroborated_assignment(
        json.loads(args.events.read_text(encoding="utf-8")), consent=args.consent,
    ), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
