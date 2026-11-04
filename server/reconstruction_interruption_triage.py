"""Evidence-bound interruption triage for ambiguous reconstruction review.

Time gaps are descriptive only; never an independent linking signal.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .record_case_lanes import _stamp
from .reconstruction_review_queue import prioritized_review_queue
from .reconstruction_reviewed_links import MAX_BYTES, MAX_EVENTS

_UNSAFE = {"conflicting_explicit_records", "missing_or_cross_scope", "tainted_source_evidence"}


def interruption_review(events: list[dict[str, Any]], *, consent: bool = False,
                        limit: int = 100, pause_seconds: int = 120) -> dict[str, Any]:
    """Inspect production weak-link candidates, never create a time-only candidate."""
    if consent is not True:
        raise PermissionError("explicit local reconstruction review consent required")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("bounded event list required")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500")
    if type(pause_seconds) is not int or not 1 <= pause_seconds <= 86400:
        raise ValueError("pause_seconds must be 1..86400")
    stamps: dict[str, str] = {}
    invalid: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event objects required")
        identifier = event.get("event_id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 120:
            continue
        stamp = _stamp(event.get("observed_at"))
        if stamp is None or (identifier in stamps and stamps[identifier] != stamp):
            invalid.add(identifier)
        elif identifier not in invalid:
            stamps[identifier] = stamp
    queue = prioritized_review_queue(events, limit=500)
    if queue["truncated"]:
        raise ValueError("candidate queue truncated: cannot assess competition safely")
    proposals = []
    for row in queue["queue"]:
        left, right = row["event_id"], row["toward_event_id"]
        if left in invalid or right in invalid or left not in stamps or right not in stamps:
            continue
        reasons = set(row["risk_reasons"])
        if reasons & _UNSAFE:
            continue
        elapsed = abs((datetime.fromisoformat(stamps[right]) -
                       datetime.fromisoformat(stamps[left])).total_seconds())
        if elapsed > 86400 * 30:
            # A weak signal older than a month is not credible continuity evidence.
            continue
        competing = "competing_candidate_components" in reasons
        proposals.append({
            "event_id": left, "toward_event_id": right, "relation": row["relation"],
            "elapsed_seconds": round(elapsed, 3),
            "interruption_class": ("extended" if elapsed >= 86400 else
                                   "paused" if elapsed >= pause_seconds else "short"),
            "review_status": "abstain_competing" if competing else "inspect",
            "review_required": True, "automatic_merge": False,
        })
    proposals.sort(key=lambda p: (
        p["review_status"] != "inspect",
        p["interruption_class"] != "paused",
        -p["elapsed_seconds"], p["event_id"], p["toward_event_id"],
    ))
    return {
        "version": 1, "evidence_sha256": queue["evidence_sha256"],
        "proposals": proposals[:limit], "total_proposals": len(proposals),
        "truncated": len(proposals) > limit,
        "canonical_membership_unchanged": True, "authoritative": False,
        "time_only_linking": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Local evidence-bound interruption triage")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--consent-local-review", action="store_true")
    parser.add_argument("--pause-seconds", type=int, default=120)
    args = parser.parse_args()
    if not args.consent_local_review:
        parser.error("--consent-local-review required")
    if not args.events.is_file() or args.events.stat().st_size > MAX_BYTES:
        parser.error("missing or oversized input")
    print(json.dumps(interruption_review(
        json.loads(args.events.read_text(encoding="utf-8")),
        consent=True, pause_seconds=args.pause_seconds,
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
