"""Risk-prioritized, evidence-bound queue for human reconstruction review.

A queue is a suggestion for inspection, never a link decision or authorization.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS, review_candidates
from .work_item_links import link_observations

_RELATION_PRIORITY = {
    "same_tab_multiplexed": 3,
    "same_tab_unreferenced": 2,
    "surface_continuity": 1,
}


def prioritized_review_queue(events: list[dict[str, Any]], *, limit: int = 100) -> dict[str, Any]:
    """Rank current source candidates; expose safety vetoes without auto-linking."""
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded events list required")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    linked = link_observations(events)
    manifest = review_candidates(events)
    rows = {row["event_id"]: row for row in linked["rows"]}
    roots = {event_id: root for root, group in linked["members"].items() for event_id in group}
    members = {root: set(group) for root, group in linked["members"].items()}
    competing: dict[str, set[str]] = defaultdict(set)
    for candidate in linked["candidates"]:
        left, right = candidate["event_id"], candidate["toward_event_id"]
        if left not in roots or right not in roots:
            continue
        for event_id, other in ((left, right), (right, left)):
            competing[roots[event_id]].add(roots[other])
    ranked = []
    for candidate in linked["candidates"]:
        left, right = candidate["event_id"], candidate["toward_event_id"]
        if left not in roots or right not in roots:
            continue
        a, b = roots[left], roots[right]
        if a == b:
            continue
        group = members[a] | members[b]
        refs = {rows[e]["ref"] for e in group if rows[e]["ref"]}
        scopes = {rows[e]["scope"] for e in group}
        tainted = any(rows[e]["reference_tainted"] or rows[e]["ephemeral_claim_tainted"] for e in group)
        reasons = []
        if len(refs) > 1:
            reasons.append("conflicting_explicit_records")
        if len(scopes) != 1 or None in scopes:
            reasons.append("missing_or_cross_scope")
        if tainted:
            reasons.append("tainted_source_evidence")
        if len(competing[a] - {b}) or len(competing[b] - {a}):
            reasons.append("competing_candidate_components")
        relation = candidate["relation"]
        ranked.append({
            "event_id": left, "toward_event_id": right, "relation": relation,
            "review_status": "conflicted" if reasons else "inspect",
            "risk_reasons": reasons,
            "source_component_sizes": [len(members[a]), len(members[b])],
            "priority": _RELATION_PRIORITY.get(relation, 0),
            "automatic_merge": False,
        })
    ranked.sort(key=lambda x: (
        bool(x["risk_reasons"]), -x["priority"],
        x["event_id"], x["toward_event_id"], x["relation"],
    ))
    return {
        "version": 1, "evidence_sha256": manifest["evidence_sha256"],
        "total_candidates": len(ranked), "shown_candidates": min(limit, len(ranked)),
        "truncated": len(ranked) > limit, "queue": ranked[:limit],
        "canonical_membership_unchanged": True, "requires_explicit_review": True,
        "authoritative": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Local risk-prioritized candidate inspection")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not args.events.is_file() or args.events.stat().st_size > MAX_BYTES:
        parser.error("missing or oversized local JSON input")
    events = json.loads(args.events.read_text(encoding="utf-8"))
    print(json.dumps(prioritized_review_queue(events, limit=args.limit), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
