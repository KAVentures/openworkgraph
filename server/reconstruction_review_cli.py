"""Offline, review-only event assignment queue for an authorized work-resumption response.

This module does not fetch history or authorize joins. Input must be an already
authorized response; callers must protect its event IDs under the original grant.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

MAX_PAIRS = 24
MAX_EVENTS_PER_PAIR = 8
MAX_SESSIONS_PER_PAIR = 8


def _event_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("evidence_event_ids must be a list")
    if len(value) > MAX_EVENTS_PER_PAIR:
        raise ValueError("unbounded evidence list")
    if any(not isinstance(x, str) or not x or len(x) > 120 for x in value):
        raise ValueError("invalid event ID")
    return sorted(set(value))


# History-access modes that may produce a review queue; anything else is denied.
REVIEWABLE_ACCESS = ("all_saved", "current_run")


def _session_window(value: Any) -> str:
    """Canonicalize a bounded session instant; refuse forged or ambiguous labels."""
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("invalid earlier session timestamp")
    try:
        if len(value) == 10:
            # Legacy date-only sessions remain distinct from timestamped sessions.
            return "earlier:" + date.fromisoformat(value).isoformat()
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("timezone required")
        return "earlier:" + instant.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid earlier session timestamp") from exc


def build_review_queue(response: dict[str, Any]) -> dict[str, Any]:
    """Create deterministic event-level review decisions, never confirmed cases.

    An event attached to multiple resource pairs is ambiguous, not silently
    assigned to either pair. Earlier and current windows remain distinct.
    """
    if not isinstance(response, dict):
        raise ValueError("response must be an object")
    if response.get("derived") is not True or response.get("authoritative") is not False:
        raise ValueError("expected derived non-authoritative resumption response")
    pairs = response.get("observed_pair_event_assignments")
    if not isinstance(pairs, list) or len(pairs) > MAX_PAIRS:
        raise ValueError("missing or unbounded pair assignments")
    # Offline JSON is never proof of a current grant. Online callers must also
    # revalidate authorization/revocation before generating a review response.
    if response.get("saved_history_access") not in REVIEWABLE_ACCESS:
        raise ValueError("no recognized saved-history access")
    coverage = response.get("coverage", {})
    if not isinstance(coverage, dict):
        raise ValueError("invalid coverage metadata")
    indexed: dict[tuple[str, str], set[str]] = defaultdict(set)
    incomplete = bool(response.get("observed_pair_event_assignments_truncated") or
                      coverage.get("scan_truncated"))
    for pair in pairs:
        if not isinstance(pair, dict) or pair.get("assignment_status") != "observed_same_event_pair" or pair.get("proves_same_case") is not False:
            raise ValueError("unsupported pair assignment")
        pointers = pair.get("resource_pointers")
        if not isinstance(pointers, list) or len(pointers) != 2:
            raise ValueError("pair must have exactly two pointers")
        identities = []
        for pointer in pointers:
            if not isinstance(pointer, dict):
                raise ValueError("invalid pointer")
            identity = tuple(pointer.get(k) for k in ("provider", "resource_kind", "resource_ref"))
            if any(not isinstance(v, str) or not v or len(v) > 256 for v in identity):
                raise ValueError("invalid pointer identity")
            identities.append(identity)
        if identities[0] == identities[1]:
            raise ValueError("self-pair is not co-reference")
        pair_key = json.dumps(sorted(identities), separators=(",", ":"))
        for event_id in _event_ids(pair.get("current_evidence_event_ids")):
            indexed[("current", event_id)].add(pair_key)
        earlier = pair.get("earlier_corroborations", [])
        if not isinstance(earlier, list) or len(earlier) > MAX_SESSIONS_PER_PAIR:
            raise ValueError("unbounded earlier corroborations")
        for session in earlier:
            if not isinstance(session, dict) or not isinstance(session.get("session_started_at"), str):
                raise ValueError("invalid earlier session")
            window = _session_window(session["session_started_at"])
            for event_id in _event_ids(session.get("evidence_event_ids")):
                indexed[(window, event_id)].add(pair_key)
            incomplete |= bool(session.get("evidence_truncated"))
        incomplete |= bool(pair.get("current_evidence_truncated") or pair.get("earlier_sessions_truncated"))
    rows = []
    # Chronological: earlier sessions by their start, then the current window.
    def _order(item):
        (window, event_id), _ = item
        return (0, window, event_id) if window.startswith("earlier:") else (1, window, event_id)
    for (window, event_id), candidates in sorted(indexed.items(), key=_order):
        rows.append({"window": window, "event_id": event_id,
                     "candidate_resource_pairs": [json.loads(p) for p in sorted(candidates)],
                     "review_status": "ambiguous_multiple_pairs" if len(candidates) > 1 else "observed_pair_needs_review",
                     "confirmed_case": False})
    # Pair-only evidence does not enumerate every event in the source scan.
    # Even a complete scan cannot establish that absent events were unassigned.
    return {"event_reviews": rows, "event_reviews_count": len(rows),
            "coverage_incomplete": incomplete,
            "unlisted_events_are_unassigned": False,
            "no_case_identity_inferred": True, "requires_human_review": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Review exact observed event-to-resource-pair evidence offline")
    parser.add_argument("input", help="Authorized work-resumption response JSON, or - for stdin")
    args = parser.parse_args()
    try:
        if args.input == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.input, encoding="utf-8") as stream:
                payload = json.load(stream)
        print(json.dumps(build_review_queue(payload), sort_keys=True, separators=(",", ":")))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"reconstruction review refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
