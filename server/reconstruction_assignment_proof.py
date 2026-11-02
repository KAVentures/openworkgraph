"""Bounded explanations of accepted work-item membership, not case identity.

The accepted union graph is supplied by the existing linker. This module never
creates an edge, evaluates a candidate, reads event text, or grants authority.
An exact-record observation is an *anchor*, not a verified business case.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

_MAX_PROOFS = 24
_MAX_HOPS = 8


def accepted_assignment_proofs(
    member_rows: list[dict[str, Any]],
    accepted_edges: list[tuple[str, str, str]],
    *,
    visible_event_ids: list[str],
    max_proofs: int = _MAX_PROOFS,
    max_hops: int = _MAX_HOPS,
) -> dict[str, Any]:
    """Explain unreferenced members by shortest accepted-edge paths to record anchors.

    Counts cover all supplied members; only bounded proof paths using already
    visible event IDs are disclosed. Ties use sorted anchors and neighbors.
    A component may contain several source records; a path is NOT evidence
    that all its records form one business case.
    """
    max_proofs = max(1, min(int(max_proofs), _MAX_PROOFS))
    max_hops = max(1, min(int(max_hops), _MAX_HOPS))
    by_id = {row["event_id"]: row for row in member_rows}
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for left, right, basis in accepted_edges:
        if left not in by_id or right not in by_id or left == right:
            continue
        adjacency[left].append((right, basis))
        adjacency[right].append((left, basis))
    for neighbors in adjacency.values():
        neighbors.sort(key=lambda entry: (entry[0], entry[1]))

    anchors = sorted(event_id for event_id, row in by_id.items() if row.get("ref"))
    # Multi-source BFS: each unreferenced event is attributed to a nearest
    # independently referenced observation through *accepted* edges only.
    parent: dict[str, tuple[str | None, str | None]] = {
        anchor: (None, None) for anchor in anchors
    }
    queue = deque(anchors)
    while queue:
        current = queue.popleft()
        for neighbor, basis in adjacency.get(current, ()):
            if neighbor not in parent:
                parent[neighbor] = (current, basis)
                queue.append(neighbor)

    loose = [row["event_id"] for row in member_rows if not row.get("ref")]
    visible = set(visible_event_ids)
    record_count = len({row["ref"] for row in member_rows if row.get("ref")})
    proofs: list[dict[str, Any]] = []
    for event_id in loose[:max_proofs]:
        if event_id not in parent:
            proofs.append({
                "event_id": event_id,
                "status": "no_exact_record_anchor",
                "anchor_event_id": None,
                "evidence_event_path": [],
                "accepted_link_basis_path": [],
            })
            continue
        path = [event_id]
        bases: list[str] = []
        cursor = event_id
        # All accepted links form a finite BFS predecessor tree.
        while parent[cursor][0] is not None:
            previous, basis = parent[cursor]
            path.append(previous)
            bases.append(basis)
            cursor = previous
        if len(bases) > max_hops:
            status = "path_exceeds_display_hop_limit"
        elif not all(node in visible for node in path):
            status = "path_outside_visible_event_window"
        else:
            status = "anchored_by_accepted_links"
        proofs.append({
            "event_id": event_id,
            "status": status,
            "anchor_event_id": path[-1] if status == "anchored_by_accepted_links" else None,
            "evidence_event_path": path if status == "anchored_by_accepted_links" else [],
            "accepted_link_basis_path": bases if status == "anchored_by_accepted_links" else [],
        })
    return {
        "unreferenced_member_count": len(loose),
        "record_anchored_unreferenced_member_count": sum(event_id in parent for event_id in loose),
        "unanchored_unreferenced_member_count": sum(event_id not in parent for event_id in loose),
        "assignment_proofs": proofs,
        "assignment_proofs_truncated": len(loose) > len(proofs),
        "multiple_exact_record_anchors_present": record_count > 1,
        "assignment_proofs_are_accepted_link_paths_not_verified_cases": True,
    }


__all__ = ["accepted_assignment_proofs"]
