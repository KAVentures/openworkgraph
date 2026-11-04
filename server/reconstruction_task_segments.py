"""Read-only interruption and interleaving segments for exact business-record observations.

Never infer a link from elapsed time or from switching between unrelated records.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .record_case_lanes import _EVENT_ID, _identity, _stamp, _token
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS

MAX_SEGMENTS = 500


def task_segments(events: list[dict[str, Any]], *, consent: bool = False,
                  idle_seconds: int = 120, max_segments: int = 200) -> dict[str, Any]:
    """Split exact-record observations into gap-bounded activity segments.

    The output describes observed work only; it cannot establish actual task
    boundaries, verified business outcomes or cross-record case membership.
    """
    if consent is not True:
        raise PermissionError("explicit local reconstruction review consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded event list required")
    if type(idle_seconds) is not int or not 1 <= idle_seconds <= 86400:
        raise ValueError("idle_seconds must be 1..86400")
    if type(max_segments) is not int or not 1 <= max_segments <= MAX_SEGMENTS:
        raise ValueError("max_segments must be 1..500")
    identities = {}
    conflicts = set()
    observations = []
    event_ids = set()
    duplicate_ids = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event objects required")
        identity = _identity(event)
        if identity is None:
            continue
        ref, provider, kind, host, org = identity
        scope = (provider, kind, host, org)
        if ref in identities and identities[ref] != scope:
            conflicts.add(ref)
        identities.setdefault(ref, scope)
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
            continue
        if event_id in event_ids:
            duplicate_ids.add(event_id)
        event_ids.add(event_id)
        stamp = _stamp(event.get("observed_at"))
        if stamp is None:
            continue
        observations.append((stamp, event_id, ref, _token(event.get("event_type")),
                             str(event.get("session_id") or "")[:160]))
    observations = [row for row in observations
                    if row[2] not in conflicts and row[1] not in duplicate_ids]
    observations.sort(key=lambda row: (row[0], row[1], row[2]))
    # Interleaving is established only by the order of observed exact record refs.
    # Never join different refs, even if they appear within the same idle window.
    interleavings = defaultdict(int)
    for previous, current in zip(observations, observations[1:]):
        if previous[2] != current[2]:
            interleavings[previous[2]] += 1
    by_ref = defaultdict(list)
    for row in observations:
        by_ref[row[2]].append(row)
    segments = []
    for ref, rows in by_ref.items():
        groups = []
        current = []
        for row in rows:
            gap = (datetime.fromisoformat(row[0]) -
                   datetime.fromisoformat(current[-1][0])).total_seconds() if current else 0
            if current and gap > idle_seconds:
                groups.append(current)
                current = []
            current.append(row)
        if current:
            groups.append(current)
        for index, group in enumerate(groups):
            prior = groups[index - 1] if index else None
            gap = ((datetime.fromisoformat(group[0][0]) -
                    datetime.fromisoformat(prior[-1][0])).total_seconds()
                   if prior else None)
            segments.append({
                "resource_ref": ref,
                "segment_index": index + 1,
                "first_observed_at": group[0][0],
                "last_observed_at": group[-1][0],
                "observed_event_count": len(group),
                "observed_actions": [row[3] for row in group[:24]],
                "actions_truncated": len(group) > 24,
                "observed_session_count": len({row[4] for row in group if row[4]}),
                "gap_from_previous_segment_seconds": round(gap, 3) if gap is not None else None,
                "possible_resumption": prior is not None,
                "interleaved_with_other_records": bool(interleavings[ref]),
                "grouping_basis": "same_exact_scoped_resource_ref_and_observed_idle_gap",
                "review_required": True,
            })
    segments.sort(key=lambda row: (row["first_observed_at"], row["resource_ref"],
                                   row["segment_index"]))
    return {
        "format": "openworkgraph.reconstruction-task-segments.v1",
        "segments": segments[:max_segments],
        "total_segments": len(segments),
        "truncated": len(segments) > max_segments,
        "conflicting_reference_groups_suppressed": len(conflicts),
        "duplicate_event_ids_suppressed": len(duplicate_ids),
        "interleaved_reference_count": len(interleavings),
        "time_only_linking": False,
        "cross_record_links_inferred": False,
        "canonical_membership_unchanged": True,
        "business_boundaries_verified": False,
        "authoritative": False,
        "read_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Local exact-record task activity segmentation")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--consent-local-review", action="store_true")
    parser.add_argument("--idle-seconds", type=int, default=120)
    args = parser.parse_args()
    if not args.consent_local_review:
        parser.error("--consent-local-review required")
    if not args.events.is_file() or args.events.stat().st_size > MAX_BYTES:
        parser.error("missing or oversized local events JSON")
    print(json.dumps(task_segments(json.loads(args.events.read_text(encoding="utf-8")),
                                   consent=True, idle_seconds=args.idle_seconds),
                     sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
