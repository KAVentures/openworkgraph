"""Optional record-centric discovery over existing opaque business references.

Never guesses business IDs from titles, paths, clipboard content or proximity.
Reference capture remains explicitly opt-in and canonical events stay authoritative.
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

_STORED_RESOURCE_REF = re.compile(r"^owg:r:[0-9a-f]{32}$")
_MAX_GROUPS = 20
_MAX_EXAMPLES = 6


def record_linked_activity(
    events: list[dict[str, Any]], *, max_groups: int = _MAX_GROUPS,
) -> dict[str, Any]:
    """Group observations only when they share the *same* normalized local ref.

    Distinct system-specific references aren't joined just because their event
    timing is close. This is activity on a record, NOT task segmentation,
    a verified workflow, causal handoff, or a source-system outcome.
    """
    grouped: dict[str, dict[str, Any]] = {}
    referenced_event_count = 0
    for event in events:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        reference = metadata.get("resource_reference") if isinstance(metadata.get("resource_reference"), dict) else {}
        ref = str(reference.get("resource_ref") or "").lower()
        if not _STORED_RESOURCE_REF.fullmatch(ref):
            continue
        provider = str(reference.get("provider") or "").strip().lower()
        kind = str(reference.get("resource_kind") or "").strip().lower()
        if not provider or not kind or len(provider) > 40 or len(kind) > 40:
            continue
        referenced_event_count += 1
        slot = grouped.setdefault(ref, {
            "resource_ref": ref, "provider": provider, "resource_kind": kind,
            "observed_event_count": 0, "_sessions": set(), "_actors": set(),
            "_timestamps": [], "example_event_ids": [],
        })
        # A mismatched provider/kind is ambiguous; withhold the group rather than
        # interpreting an inconsistent reference as evidence of a cross-app join.
        if slot["provider"] != provider or slot["resource_kind"] != kind:
            slot["_ambiguous"] = True
            continue
        slot["observed_event_count"] += 1
        if event.get("session_id"):
            slot["_sessions"].add(str(event["session_id"]))
        if event.get("actor_id"):
            slot["_actors"].add(str(event["actor_id"]))
        observed_at = str(event.get("observed_at") or "")
        if observed_at:
            slot["_timestamps"].append(observed_at)
        event_id = str(event.get("event_id") or "")
        if event_id and len(slot["example_event_ids"]) < _MAX_EXAMPLES:
            slot["example_event_ids"].append(event_id)

    rows: list[dict[str, Any]] = []
    for slot in grouped.values():
        if slot.get("_ambiguous") or slot["observed_event_count"] < 2:
            continue
        times = sorted(slot["_timestamps"])
        rows.append({
            "resource_ref": slot["resource_ref"],
            "provider": slot["provider"],
            "resource_kind": slot["resource_kind"],
            "observed_event_count": slot["observed_event_count"],
            "observed_session_count": len(slot["_sessions"]),
            "observed_actor_count": len(slot["_actors"]),
            "first_observed_at": times[0] if times else "",
            "last_observed_at": times[-1] if times else "",
            "example_event_ids": slot["example_event_ids"],
            "grouping_basis": "identical_normalized_opaque_resource_reference",
            "cross_system_identity_inferred": False,
            "handoff_or_outcome_verified": False,
        })
    rows.sort(key=lambda row: (
        -row["observed_session_count"], -row["observed_event_count"],
        row["resource_ref"],
    ))
    return {
        "groups": rows[:max(1, min(int(max_groups), _MAX_GROUPS))],
        "total_groups": len(rows),
        "observed_events_with_normalized_reference": referenced_event_count,
        "references_are_opt_in": True,
        "source_system_ids_or_values_included": False,
        "no_group_means_no_matching_observed_reference_not_no_related_work": True,
        "relationship_to_task_inference": "supplemental_navigation_only",
        "grouping_never_asserts_handoff_or_verified_business_outcome": True,
    }
