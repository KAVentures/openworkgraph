"""Bounded two-hop review of unassigned work fragments (#326 A / #264 Stage 2).

A weak relation is *not* a membership edge. This projection only explains a
candidate route through one other unassigned observation to a visible
source-anchored item. It never changes the union/find linker or authorizes work.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

_WEAK = frozenset({"same_tab_multiplexed", "same_tab_unreferenced", "surface_continuity"})
_MAX_ROWS = 24
_MAX_TARGETS = 4
_MAX_EDGES = 10_000
_MAX_PATHS = 20_000


def _empty(*, complete: bool = True) -> dict[str, Any]:
    return {
        "ambiguous_fragment_review_total": 0 if complete else None,
        "ambiguous_fragment_review_competing_count": 0 if complete else None,
        "ambiguous_fragment_review_truncated": not complete,
        "ambiguous_fragment_review_scan_complete": complete,
        "ambiguous_fragment_review_queue": [],
    }


def _scope_safe_pair(rows: dict[str, dict[str, Any]], a: str, b: str) -> bool:
    left, right = rows.get(a), rows.get(b)
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    scope = left.get("scope")
    if (not isinstance(scope, tuple) or len(scope) != 4
            or any(not isinstance(v, str) or not v for v in scope)
            or right.get("scope") != scope):
        return False
    # Do not build transitive hints through an unknown app/source, a different
    # application or capture channel, or a contradictory observation.
    for row in (left, right):
        if row.get("reference_tainted") or row.get("ephemeral_claim_tainted"):
            return False
        if (not isinstance(row.get("app"), str) or not row["app"]
                or not isinstance(row.get("source"), str)
                or row["source"] in ("", "unknown")):
            return False
    return left["app"] == right["app"] and left["source"] == right["source"]


def ambiguous_fragment_review(
    candidates: list[dict[str, Any]],
    rows: dict[str, dict[str, Any]],
    item_of: dict[str, str],
    singleton_ids: set[str],
    anchored_item_ids: set[str],
    *,
    max_rows: int = _MAX_ROWS,
    max_targets: int = _MAX_TARGETS,
) -> dict[str, Any]:
    """Suggest strictly two-edge weak paths, never inferred case membership.

    Every path is loose -> unassigned singleton -> witnessed visible item.
    No linked/hidden component may serve as an intermediary. A loose action
    already having a direct visible anchored candidate stays in the existing
    one-hop queue, avoiding duplicate or stronger-looking recommendations.
    Large/degenerate inputs fail closed with explicit incomplete counts.
    """
    max_rows = max(1, min(int(max_rows), _MAX_ROWS))
    max_targets = max(1, min(int(max_targets), _MAX_TARGETS))
    if len(candidates) > _MAX_EDGES or len(singleton_ids) > _MAX_EDGES:
        return _empty(complete=False)

    # Two disjoint kinds of edges: singleton-to-singleton, and singleton-to-
    # visible-anchored-member. Preserve only independently emitted weak edges.
    loose_edges: dict[str, set[tuple[str, str]]] = defaultdict(set)
    direct: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        a, b, relation = (candidate.get("event_id"),
                          candidate.get("toward_event_id"),
                          candidate.get("relation"))
        if (not isinstance(a, str) or not isinstance(b, str)
                or not a or not b or a == b
                or not isinstance(relation, str) or relation not in _WEAK
                or not _scope_safe_pair(rows, a, b)):
            continue
        if a in singleton_ids and b in singleton_ids:
            loose_edges[a].add((b, relation))
            loose_edges[b].add((a, relation))
        elif a in singleton_ids and item_of.get(b) in anchored_item_ids:
            direct[a].add((item_of[b], b, relation))
        elif b in singleton_ids and item_of.get(a) in anchored_item_ids:
            direct[b].add((item_of[a], a, relation))

    # Candidate path counts are bounded before allocation. Do not emit
    # misleading partial totals or arbitrary first-N evidence on overflow.
    potential = sum(len(neighbors) * len(direct.get(middle, ()))
                    for middle, neighbors in loose_edges.items())
    if potential > _MAX_PATHS:
        return _empty(complete=False)

    # loose -> target -> witnessed (middle, anchor, relation1, relation2)
    paths: dict[str, dict[str, set[tuple[str, str, str, str]]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for middle, neighbors in loose_edges.items():
        if not direct.get(middle):
            continue
        for loose, first_relation in neighbors:
            if direct.get(loose):
                continue
            for item_id, witness, second_relation in direct[middle]:
                paths[loose][item_id].add(
                    (middle, witness, first_relation, second_relation)
                )

    entries: list[dict[str, Any]] = []
    competing = 0
    for loose, targets in paths.items():
        if not targets:
            continue
        competing += int(len(targets) > 1)
        options: list[dict[str, Any]] = []
        for item_id, support in targets.items():
            middle, witness, first, second = min(support)
            options.append({
                "item_id": item_id,
                "distinct_intermediary_count": len({p[0] for p in support}),
                "distinct_two_hop_path_count": len(support),
                "witness_event_path": [loose, middle, witness],
                "weak_relation_path": [first, second],
            })
        options.sort(key=lambda x: (
            -x["distinct_intermediary_count"],
            -x["distinct_two_hop_path_count"], x["item_id"],
        ))
        entries.append({
            "event_id": loose,
            "review_priority": ("competing_visible_items" if len(options) > 1
                                else "two_hop_weak_evidence"),
            "candidate_item_count": len(options),
            "candidate_items": options[:max_targets],
            "candidate_items_truncated": len(options) > max_targets,
            "evidence_hops": 2,
            "auto_assigned": False,
            "requires_human_review": True,
        })
    entries.sort(key=lambda x: (
        0 if x["candidate_item_count"] > 1 else 1,
        -x["candidate_item_count"], x["event_id"],
    ))
    return {
        "ambiguous_fragment_review_total": len(entries),
        "ambiguous_fragment_review_competing_count": competing,
        "ambiguous_fragment_review_truncated": len(entries) > max_rows,
        "ambiguous_fragment_review_scan_complete": True,
        "ambiguous_fragment_review_queue": entries[:max_rows],
    }


__all__ = ["ambiguous_fragment_review"]
