"""Consent-gated global assignment of scored production reconstruction candidates.

Scores are externally supplied *numbers*, not authorization or observed proof.
No raw text, provider access, canonical membership changes or automatic writes.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any

from .work_item_links import link_observations
from .reconstruction_review_queue import prioritized_review_queue
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS

MAX_COMPONENTS = 16
MAX_SCORES = 100
MIN_SCORE = 0.80
MIN_MARGIN = 0.08


def semantic_assignment(
    events: list[dict[str, Any]],
    scores: dict[str, Any],
    *,
    consent: bool = False,
) -> dict[str, Any]:
    """Max-weight disjoint pairing with strict evidence and ambiguity abstention.

    The input scores must be computed from separately authorized, current,
    redacted evidence. This function cannot verify the upstream score provider
    or grant access to text. The result is a proposal for human inspection.
    """
    if consent is not True:
        raise ValueError("explicit semantic evidence consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded events list required")
    if not isinstance(scores, dict) or scores.get("version") != 1:
        raise ValueError("unsupported semantic score manifest")
    queue = prioritized_review_queue(events, limit=500)
    if scores.get("evidence_sha256") != queue["evidence_sha256"]:
        raise ValueError("stale or mismatched source evidence digest")
    if queue["truncated"]:
        raise ValueError("candidate queue truncated: refusing partial optimization")
    entries = scores.get("pairs")
    if not isinstance(entries, list) or len(entries) > MAX_SCORES:
        raise ValueError("bounded score pairs required")
    allowed = {}
    for row in queue["queue"]:
        key = tuple(sorted((row["event_id"], row["toward_event_id"])))
        if ((row.get("review_status") == "inspect" and not row["risk_reasons"]) or
                (row.get("review_status") == "conflicted" and
                 set(row["risk_reasons"]) == {"competing_candidate_components"})):
            allowed[key] = row
    weighted: dict[tuple[str, str], float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid score entry")
        a, b, score = entry.get("event_id"), entry.get("toward_event_id"), entry.get("score")
        if not isinstance(a, str) or not isinstance(b, str) or a == b:
            raise ValueError("invalid pair identity")
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("score must be finite and between zero and one")
        key = tuple(sorted((a, b)))
        if key in weighted:
            raise ValueError("duplicate semantic pair")
        if key not in allowed:
            raise ValueError("pair absent, conflicting or not eligible for review")
        weighted[key] = float(score)
    # Deterministic source components, not individual endpoints, are the units
    # of exclusivity. A component can have several eligible candidate endpoints.
    linked = link_observations(events)
    membership = {eid: root for root, ids in linked["members"].items() for eid in ids}
    component_edges: dict[tuple[str, str], tuple[float, tuple[str, str]]] = {}
    component_scores: dict[str, dict[str, float]] = {}
    for pair, score in weighted.items():
        a, b = pair
        ra, rb = membership.get(a), membership.get(b)
        if ra is None or rb is None or ra == rb:
            continue
        component_pair = tuple(sorted((ra, rb)))
        previous = component_edges.get(component_pair)
        if previous is None or score > previous[0] or (score == previous[0] and pair < previous[1]):
            component_edges[component_pair] = (score, pair)
    for (a, b), (score, _) in component_edges.items():
        component_scores.setdefault(a, {})[b] = score
        component_scores.setdefault(b, {})[a] = score
    ambiguous = {
        node for node, competitors in component_scores.items()
        if len(competitors) > 1 and
        (lambda values: values[0] - values[1] < MIN_MARGIN)(sorted(competitors.values(), reverse=True))
    }
    eligible = {key: value for key, value in component_edges.items()
                if value[0] >= MIN_SCORE and key[0] not in ambiguous and key[1] not in ambiguous}
    nodes = sorted({node for key in eligible for node in key})
    if len(nodes) > MAX_COMPONENTS:
        raise ValueError("too many eligible source components for bounded global assignment")
    index = {node: i for i, node in enumerate(nodes)}
    edges = {(index[a], index[b]): value[0] for (a, b), value in eligible.items()}

    @lru_cache(None)
    def solve(mask: int) -> tuple[float, tuple[tuple[int, int], ...]]:
        if not mask:
            return (0.0, ())
        first = (mask & -mask).bit_length() - 1
        remainder = mask & ~(1 << first)
        best = solve(remainder)
        for second in range(first + 1, len(nodes)):
            if not remainder & (1 << second):
                continue
            score = edges.get((first, second))
            if score is None:
                continue
            previous = solve(remainder & ~(1 << second))
            proposed = (previous[0] + score, ((first, second),) + previous[1])
            if proposed[0] > best[0] + 1e-10 or (
                abs(proposed[0] - best[0]) <= 1e-10 and proposed[1] < best[1]
            ):
                best = proposed
        return best

    optimum, chosen = solve((1 << len(nodes)) - 1)
    proposals = [
        {"event_id": eligible[(nodes[a], nodes[b])][1][0],
         "toward_event_id": eligible[(nodes[a], nodes[b])][1][1],
         "semantic_score": edges[(a, b)], "requires_human_review": True}
        for a, b in chosen
    ]
    return {
        "version": 1, "evidence_sha256": queue["evidence_sha256"],
        "proposals": proposals, "global_score_sum": round(optimum, 8),
        "abstained_ambiguous_endpoints": len(ambiguous),
        "unscored_or_ineligible_candidates": queue["total_candidates"] - len(weighted),
        "canonical_membership_unchanged": True,
        "authoritative": False, "source_verified": False,
        "requires_human_review": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Local semantic candidate matching (proposals only)")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--consent", action="store_true", help="Confirm independent current authorization for score evidence")
    args = parser.parse_args()
    for path in (args.events, args.scores):
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            parser.error("missing or oversized local JSON input")
    result = semantic_assignment(
        json.loads(args.events.read_text(encoding="utf-8")),
        json.loads(args.scores.read_text(encoding="utf-8")),
        consent=args.consent,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
