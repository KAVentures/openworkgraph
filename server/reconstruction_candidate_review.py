"""Reviewable, never-auto-merged weak assignment evidence for existing items.

Only singleton observations and source-anchored items already visible in the
linker's response qualify. The queue is a review aid, not a classifier, a
probability, a new graph edge, or an authorization to act.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

_MAX_REVIEW_ROWS = 24
_MAX_REVIEW_TARGETS = 4
_WEAK_RELATIONS = frozenset({
    "same_tab_multiplexed", "same_tab_unreferenced", "surface_continuity",
})


def ambiguous_assignment_review(
    candidates: list[dict[str, Any]],
    item_of: dict[str, str],
    singleton_ids: set[str],
    anchored_item_ids: set[str],
    *,
    max_rows: int = _MAX_REVIEW_ROWS,
    max_targets: int = _MAX_REVIEW_TARGETS,
) -> dict[str, Any]:
    """Group actual weak candidate edges into a bounded analyst review queue.

    A candidate must connect exactly one *singleton* observation to a visible
    already-linked source-anchored item. Hidden or unanchored components are
    never silently represented as singleton observations or eligible targets.
    Evidence support counts are not confidence scores or calibrated rankings.
    """
    max_rows = max(1, min(int(max_rows), _MAX_REVIEW_ROWS))
    max_targets = max(1, min(int(max_targets), _MAX_REVIEW_TARGETS))
    # event -> item -> distinct (witness event, weak relation)
    grouped: dict[str, dict[str, set[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        source, toward, relation = (
            candidate.get("event_id"),
            candidate.get("toward_event_id"),
            candidate.get("relation"),
        )
        if (not isinstance(source, str) or not source
                or not isinstance(toward, str) or not toward
                or source == toward or not isinstance(relation, str)
                or relation not in _WEAK_RELATIONS):
            continue
        if source in singleton_ids and toward in item_of:
            loose, witness = source, toward
        elif toward in singleton_ids and source in item_of:
            loose, witness = toward, source
        else:
            continue
        target = item_of[witness]
        if target in anchored_item_ids:
            grouped[loose][target].add((witness, relation))

    entries: list[dict[str, Any]] = []
    full_support_by_event: dict[str, int] = {}
    competing = 0
    for event_id, targets in grouped.items():
        multiple = len(targets) > 1
        competing += int(multiple)
        options = []
        for item_id, edges in targets.items():
            options.append({
                "item_id": item_id,
                "weak_relations": sorted({relation for _, relation in edges}),
                "distinct_witness_event_count": len({witness for witness, _ in edges}),
                "distinct_candidate_edge_count": len(edges),
            })
        # Stable evidence-volume order is a review convenience, not a
        # probability or a decision that the first target is correct.
        options.sort(key=lambda option: (
            -option["distinct_candidate_edge_count"], option["item_id"]
        ))
        full_support_by_event[event_id] = sum(len(edges) for edges in targets.values())
        entries.append({
            "event_id": event_id,
            "review_priority": "competing_visible_items" if multiple else "weak_evidence_only",
            "candidate_item_count": len(options),
            "candidate_items": options[:max_targets],
            "candidate_items_truncated": len(options) > max_targets,
            "auto_assigned": False,
            "requires_human_review": True,
        })

    entries.sort(key=lambda entry: (
        0 if entry["candidate_item_count"] > 1 else 1,
        -full_support_by_event[entry["event_id"]],
        entry["event_id"],
    ))
    return {
        "ambiguous_assignment_review_total": len(entries),
        "ambiguous_assignment_review_competing_count": competing,
        "ambiguous_assignment_review_truncated": len(entries) > max_rows,
        "ambiguous_assignment_review_queue": entries[:max_rows],
    }


__all__ = ["ambiguous_assignment_review"]
