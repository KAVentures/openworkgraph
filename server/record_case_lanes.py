"""Exact-record chronology for interleaved observed work.

These are read-only, privacy-minimized lanes over existing canonical events.
They are NOT inferred tasks, true case starts/ends, cross-record joins,
handoff attestations, or completed business workflows.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import re
from typing import Any

_RESOURCE = re.compile(r"^owg:r:[0-9a-f]{32}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_EVENT_ID = re.compile(r"^[A-Za-z0-9:._-]{1,120}$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_MAX_LANES = 30
_MAX_STEPS = 24


def _stamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc).isoformat()


def _token(value: Any) -> str:
    name = str(value or "").strip().lower()
    return name if _TOKEN.fullmatch(name) else "unknown"


def _identity(event: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    ref_obj = metadata.get("resource_reference") if isinstance(metadata.get("resource_reference"), dict) else {}
    ref = str(ref_obj.get("resource_ref") or "").lower()
    provider, kind = _token(ref_obj.get("provider")), _token(ref_obj.get("resource_kind"))
    host = str(ref_obj.get("host") or "").strip().lower()
    if not _RESOURCE.fullmatch(ref) or provider == "unknown" or kind == "unknown":
        return None
    # Host identity is important for rejecting inconsistent references, but is
    # never echoed back. Legacy observations can lack a host; that absence is
    # tracked as a distinct identity instead of guessed.
    if host and not _HOST.fullmatch(host):
        return None
    org = str(event.get("organization_id") or "")
    # A single opaque ref appearing under conflicting organization IDs must
    # never produce a cross-organization lane.
    if len(org) > 160:
        return None
    return ref, provider, kind, host, org


def record_case_lanes(
    events: list[dict[str, Any]], *, max_lanes: int = 20, max_steps: int = 24,
) -> dict[str, Any]:
    """Project case-separated, explicitly referenced observations.

    A->B->A events yield one record lane for A and another for B, never
    one inferred A/B business case. Repeated references across actors are
    descriptive and never imply a handoff. Empty and conflicting identities
    fail closed. No provider calls, database writes, or text/URL payloads.
    """
    lanes_limit = max(1, min(int(max_lanes), _MAX_LANES))
    steps_limit = max(1, min(int(max_steps), _MAX_STEPS))
    grouped: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
    identities: dict[str, tuple[str, str, str, str]] = {}
    conflicts: set[str] = set()
    seen_events: set[tuple[str, str]] = set()
    actors: dict[str, set[str]] = defaultdict(set)
    sessions: dict[str, set[str]] = defaultdict(set)
    duplicates = 0
    dropped_timestamps = 0
    # Preserve no raw row outside this temporary in-memory call.
    for event in events:
        if not isinstance(event, dict):
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        reference = metadata.get("resource_reference") if isinstance(metadata.get("resource_reference"), dict) else {}
        candidate_ref = str(reference.get("resource_ref") or "").lower()
        match = _identity(event)
        if not match:
            # Do not silently discard a corrupt row claiming the same normalized
            # reference and then publish the other rows as an unconflicted path.
            if _RESOURCE.fullmatch(candidate_ref):
                conflicts.add(candidate_ref)
            continue
        ref, provider, kind, host, org = match
        identity = (provider, kind, host, org)
        prior = identities.setdefault(ref, identity)
        if identity != prior:
            conflicts.add(ref)
            continue
        stamp = _stamp(event.get("observed_at"))
        if stamp is None:
            dropped_timestamps += 1
            continue
        event_id = str(event.get("event_id") or "")
        event_id = event_id if _EVENT_ID.fullmatch(event_id) else ""
        if event_id:
            event_key = (ref, event_id)
            if event_key in seen_events:
                duplicates += 1
                continue
            seen_events.add(event_key)
        if event.get("actor_id"):
            actors[ref].add(str(event["actor_id"]))
        if event.get("session_id"):
            sessions[ref].add(str(event["session_id"]))
        # Only the event type can become a readable action. Never export
        # window titles, UI strings, browser URLs, clipboard or source locators.
        action = _token(event.get("event_type"))
        source = _token(event.get("source"))
        grouped[ref].append((stamp, event_id, {
            "observed_at": stamp,
            "action": action,
            "source": source,
        }))
    ordered: list[dict[str, Any]] = []
    for ref, rows in grouped.items():
        if ref in conflicts or len(rows) < 2:
            continue
        rows.sort(key=lambda x: (x[0], x[1]))
        identity = identities[ref]
        # Diversity counts exclude malformed timestamps and duplicates.
        ordered.append({
            "resource_ref": ref,
            "provider": identity[0],
            "resource_kind": identity[1],
            "observed_event_count": len(rows),
            "observed_actor_count": len(actors[ref]),
            "observed_session_count": len(sessions[ref]),
            "first_observed_at": rows[0][0],
            "last_observed_at": rows[-1][0],
            "observed_steps": [item[2] for item in rows[:steps_limit]],
            "steps_truncated": len(rows) > steps_limit,
            "grouping_basis": "same_normalized_opaque_resource_ref",
            "record_identity_scope": "same_provider_kind_host_organization",
            "source_identity_verification_performed": False,
            "cross_record_join_inferred": False,
            "business_case_boundary_inferred": False,
            "actor_handoff_verified": False,
            "business_outcome_verified": False,
            "event_order_is_observed_not_required_policy": True,
        })
    ordered.sort(key=lambda item: (
        -item["observed_event_count"], item["resource_ref"],
    ))
    return {
        "format": "openworkgraph.record-case-lanes.v1",
        "lanes": ordered[:lanes_limit],
        "total_lanes": len(ordered),
        "conflicting_reference_groups_suppressed": len(conflicts),
        "duplicate_event_ids_suppressed": duplicates,
        "invalid_timestamps_omitted": dropped_timestamps,
        "source_event_window_may_be_partial": True,
        "no_lane_means_no_exact_repeat_observation_not_no_work": True,
        "derived": True,
        "authoritative": False,
        "read_only": True,
    }


__all__ = ["record_case_lanes"]
