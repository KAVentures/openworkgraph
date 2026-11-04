"""Consent-gated task segmentation using observed exact-record switches and idle gaps.

Switches split activity even when the same record resumes before the idle threshold.
These are review-only observed segments, never inferred cross-record case joins.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .record_case_lanes import _EVENT_ID, _identity, _stamp
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS


def switch_segments(events: list[dict[str, Any]], *, consent: bool = False,
                    idle_seconds: int = 120, max_segments: int = 200) -> dict[str, Any]:
    if consent is not True:
        raise PermissionError("explicit local reconstruction review consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded event list required")
    if type(idle_seconds) is not int or not 1 <= idle_seconds <= 86400:
        raise ValueError("idle_seconds must be 1..86400")
    if type(max_segments) is not int or not 1 <= max_segments <= 500:
        raise ValueError("max_segments must be 1..500")
    scopes: dict[str, tuple[str, ...]] = {}
    conflicts: set[str] = set()
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
        stamp = _stamp(event.get("observed_at"))
        if stamp is not None:
            rows.append((stamp, event_id, ref))
    duplicate_ids = {eid for eid, count in Counter(row[1] for row in rows).items() if count > 1}
    rows = [row for row in rows if row[1] not in duplicate_ids and row[2] not in conflicts]
    tied = {stamp for stamp, count in Counter(row[0] for row in rows).items() if count > 1}
    ambiguous_count = sum(row[0] in tied for row in rows)
    rows = sorted(row for row in rows if row[0] not in tied)
    runs: list[list[tuple[str, str, str]]] = []
    split_reasons: list[str] = []
    for row in rows:
        if not runs:
            runs.append([row])
            split_reasons.append("first_observation")
            continue
        last = runs[-1][-1]
        gap = (datetime.fromisoformat(row[0]) - datetime.fromisoformat(last[0])).total_seconds()
        reason = ("record_switch" if row[2] != last[2] else
                  "idle_gap" if gap > idle_seconds else None)
        if reason:
            runs.append([row])
            split_reasons.append(reason)
        else:
            runs[-1].append(row)
    segments = []
    for index, run in enumerate(runs):
        ref = run[0][2]
        prior_same = next((j for j in range(index - 1, -1, -1)
                           if runs[j][0][2] == ref), None)
        intervening = [runs[j][0][2] for j in range(prior_same + 1, index)] if prior_same is not None else []
        # Return is supported only by a concrete intervening observed record,
        # not by time, app continuity or an unobserved gap.
        observed_return = bool(intervening)
        segments.append({
            "segment_index": index + 1,
            "resource_ref": ref,
            "first_observed_at": run[0][0],
            "last_observed_at": run[-1][0],
            "observed_event_count": len(run),
            "split_reason": split_reasons[index],
            "possible_resumption": prior_same is not None,
            "observed_record_switch_return": observed_return,
            "previous_same_record_segment_index": prior_same + 1 if prior_same is not None else None,
            "intervening_resource_refs": list(dict.fromkeys(intervening))[:24],
            "intervening_refs_truncated": len(set(intervening)) > 24,
            "review_required": True,
        })
    return {
        "format": "openworkgraph.reconstruction-switch-segments.v1",
        "segments": segments[:max_segments],
        "total_segments": len(segments),
        "truncated": len(segments) > max_segments,
        "duplicate_event_ids_suppressed": len(duplicate_ids),
        "conflicting_reference_groups_suppressed": len(conflicts),
        "ambiguous_timestamp_observations_suppressed": ambiguous_count,
        "cross_record_links_inferred": False,
        "canonical_membership_unchanged": True,
        "authoritative": False,
        "read_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Review switch-aware exact-record task segments")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--consent-local-review", action="store_true")
    parser.add_argument("--idle-seconds", type=int, default=120)
    args = parser.parse_args()
    if not args.consent_local_review:
        parser.error("--consent-local-review required")
    if not args.events.is_file() or args.events.stat().st_size > MAX_BYTES:
        parser.error("missing or oversized local events JSON")
    print(json.dumps(switch_segments(json.loads(args.events.read_text(encoding="utf-8")),
                                     consent=True, idle_seconds=args.idle_seconds),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
