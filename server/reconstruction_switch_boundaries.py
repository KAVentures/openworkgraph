"""Review-only exact-record interruption boundaries from observed record switches.

A -> B -> A is an interruption of A even when its idle gap is short.
No cross-record membership is ever inferred from proximity.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .record_case_lanes import _EVENT_ID, _identity, _stamp
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS


def switch_boundaries(events: list[dict[str, Any]], *, consent: bool = False,
                      max_boundaries: int = 200) -> dict[str, Any]:
    if consent is not True:
        raise PermissionError("explicit local reconstruction review consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded event list required")
    if type(max_boundaries) is not int or not 1 <= max_boundaries <= 500:
        raise ValueError("max_boundaries must be 1..500")
    scopes: dict[str, tuple[str, ...]] = {}
    conflicts: set[str] = set()
    seen: set[str] = set()
    duplicates: set[str] = set()
    rows: list[tuple[str, str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event objects required")
        identity = _identity(event)
        if identity is None:
            continue
        ref, provider, kind, host, org = identity
        scope = (provider, kind, host, org)
        if ref in scopes and scopes[ref] != scope:
            conflicts.add(ref)
        scopes.setdefault(ref, scope)
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
            continue
        if event_id in seen:
            duplicates.add(event_id)
        seen.add(event_id)
        stamp = _stamp(event.get("observed_at"))
        if stamp is not None:
            rows.append((stamp, event_id, ref))
    rows = sorted((r for r in rows if r[2] not in conflicts and r[1] not in duplicates))
    # Equal timestamps have no reliable causal order. Exclude all such observations.
    stamp_counts: dict[str, int] = defaultdict(int)
    for stamp, _, _ in rows:
        stamp_counts[stamp] += 1
    ambiguous = sum(stamp_counts[stamp] > 1 for stamp, _, _ in rows)
    rows = [r for r in rows if stamp_counts[r[0]] == 1]
    # Record transitions are observed chronology, not inferred case joins.
    runs: list[list[tuple[str, str, str]]] = []
    for row in rows:
        if not runs or runs[-1][-1][2] != row[2]:
            runs.append([])
        runs[-1].append(row)
    boundaries: list[dict[str, Any]] = []
    for index in range(2, len(runs)):
        before, between, after = runs[index - 2:index + 1]
        if before[0][2] != after[0][2]:
            continue
        # Each A->B->A candidate requires all three observed exact references.
        # No timestamp threshold, same-record semantic guess, or source join.
        boundaries.append({
            "resource_ref": before[0][2],
            "intervening_resource_ref": between[0][2],
            "last_before_switch_at": before[-1][0],
            "first_intervening_at": between[0][0],
            "last_intervening_at": between[-1][0],
            "first_after_return_at": after[0][0],
            "elapsed_seconds": round((datetime.fromisoformat(after[0][0]) -
                                      datetime.fromisoformat(before[-1][0])).total_seconds(), 3),
            "observed_before_count": len(before),
            "observed_intervening_count": len(between),
            "observed_after_count": len(after),
            "basis": "observed_exact_record_switch_and_return",
            "possible_resumption": True,
            "requires_review": True,
            "canonical_membership_changed": False,
        })
    return {
        "format": "openworkgraph.reconstruction-switch-boundaries.v1",
        "boundaries": boundaries[:max_boundaries],
        "total_boundaries": len(boundaries),
        "truncated": len(boundaries) > max_boundaries,
        "conflicting_reference_groups_suppressed": len(conflicts),
        "duplicate_event_ids_suppressed": len(duplicates),
        "ambiguous_timestamp_observations_suppressed": ambiguous,
        "cross_record_links_inferred": False,
        "canonical_membership_unchanged": True,
        "authoritative": False,
        "read_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Review observed A-B-A work interruptions")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--consent-local-review", action="store_true")
    args = parser.parse_args()
    if not args.consent_local_review:
        parser.error("--consent-local-review required")
    if not args.events.is_file() or args.events.stat().st_size > MAX_BYTES:
        parser.error("missing or oversized events JSON")
    print(json.dumps(switch_boundaries(json.loads(args.events.read_text(encoding="utf-8")),
                                       consent=True), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
