"""Explicit, evidence-bound review of ambiguous OWG reconstruction links.

This is a local, non-authoritative review overlay. It never changes the source
linker's canonical membership or authorizes an external business operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .work_item_links import link_observations

MAX_EVENTS = 10000
MAX_REVIEWS = 500
MAX_BYTES = 8_000_000


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _snapshot(linked: dict[str, Any]) -> dict[str, Any]:
    """Bind decisions to *all* normalized admitted evidence and candidate edges."""
    rows = linked["rows"]
    candidates = linked["candidates"]
    manifest = {
        "version": 1,
        "evidence_sha256": _digest({
            "rows": [{k: v for k, v in row.items() if k != "source"} | {"source": row["source"]}
                     for row in rows],
            "members": sorted(sorted(v) for v in linked["members"].values()),
            "candidates": sorted((c["event_id"], c["toward_event_id"], c["relation"])
                                 for c in candidates),
            "conflicting_references": linked["conflicting_references"],
        }),
        "candidate_count": len(candidates),
        "candidates": [
            {"event_id": c["event_id"], "toward_event_id": c["toward_event_id"],
             "relation": c["relation"]}
            for c in candidates
        ],
    }
    return manifest


def review_candidates(events: list[dict[str, Any]],
                      decisions: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a review manifest or proposed groups for explicitly accepted edges.

    Every accepted edge must exist in the current source-derived candidate set.
    Reviews are all-or-nothing, versioned, evidence-digest-bound and scoped.
    A proposal never merges distinct explicit record identities or actor scopes.
    """
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded events list required")
    linked = link_observations(events)
    manifest = _snapshot(linked)
    if decisions is None:
        return manifest
    if not isinstance(decisions, dict) or decisions.get("version") != 1:
        raise ValueError("unsupported review version")
    if decisions.get("evidence_sha256") != manifest["evidence_sha256"]:
        raise ValueError("stale or mismatched source evidence")
    accepted = decisions.get("accepted")
    if not isinstance(accepted, list) or len(accepted) > MAX_REVIEWS:
        raise ValueError("bounded accepted review list required")
    valid = {(c["event_id"], c["toward_event_id"], c["relation"])
             for c in linked["candidates"]}
    rows = {r["event_id"]: r for r in linked["rows"]}
    parent = {event_id: root for root, members in linked["members"].items()
              for event_id in members}
    components = {root: set(members) for root, members in linked["members"].items()}
    seen: set[tuple[str, str, str]] = set()
    reviewed: list[dict[str, str]] = []
    for decision in accepted:
        if not isinstance(decision, dict) or set(decision) != {"event_id", "toward_event_id", "relation"}:
            raise ValueError("invalid review decision")
        triple = (decision["event_id"], decision["toward_event_id"], decision["relation"])
        if any(not isinstance(v, str) for v in triple) or triple not in valid or triple in seen:
            raise ValueError("unknown or duplicate candidate")
        seen.add(triple)
        reviewed.append(dict(decision))

    # Freeze each source component's literal record and local scope claims.
    # A conflicting identity or missing scope never becomes a wildcard.
    def attributes(group: set[str]) -> tuple[set[str], set[tuple[str, ...] | None], bool]:
        records = {rows[e]["ref"] for e in group if rows[e]["ref"]}
        scopes = {rows[e]["scope"] for e in group}
        tainted = any(rows[e]["reference_tainted"] or rows[e]["ephemeral_claim_tainted"]
                      for e in group)
        return records, scopes, tainted

    for decision in reviewed:
        left, right = parent[decision["event_id"]], parent[decision["toward_event_id"]]
        if left == right:
            continue
        group = components[left] | components[right]
        refs, scopes, tainted = attributes(group)
        if tainted or len(refs) > 1 or len(scopes) != 1 or None in scopes:
            raise ValueError("review cannot bridge identity, scope or tainted evidence")
        components[left] = group
        del components[right]
        for event_id in group:
            parent[event_id] = left
    position = {r["event_id"]: i for i, r in enumerate(linked["rows"])}
    proposed = [sorted(group, key=position.__getitem__) for group in components.values()]
    proposed.sort(key=lambda ids: position[ids[0]])
    return {
        "version": 1, "evidence_sha256": manifest["evidence_sha256"],
        "reviewed_edge_count": len(reviewed),
        "proposed_groups": proposed,
        "canonical_membership_unchanged": True,
        "reviewed_edges": reviewed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Local reviewed candidate reconstruction (no source writes)")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--decisions", type=Path, help="Explicit reviewed JSON; omit to list candidates")
    args = parser.parse_args()
    def load(path: Path) -> Any:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError("missing or oversized local JSON input")
        return json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(review_candidates(load(args.events),
                                       load(args.decisions) if args.decisions else None),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
