"""Observed activity episodes inside an already evidence-linked work item.

Episodes are presentation-only: independent foreign-record activity or a
capture-scope change can interrupt a displayed sequence, but episodes neither
create nor remove membership edges. A clock gap alone never supplies an edge or
an interruption claim. No raw scope values or foreign record IDs are returned.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from typing import Any

_MAX_SEGMENTS = 16
_MAX_SEGMENT_EVENT_IDS = 20


def observed_activity_segments(
    rows: list[dict[str, Any]],
    member_ids: list[str],
    *,
    max_segments: int = _MAX_SEGMENTS,
) -> dict[str, Any]:
    """Bounded, provenance-aware episodes for one existing linked component.

    All input rows are validated observations in chronological order. A foreign
    record counts as an interruption only within the same exact local capture
    scope as both neighboring member observations. Other tenants, actors,
    devices, sessions and malformed/missing scope cannot leak their activity
    into this item's interruption boundaries. An observed scope change is a
    separate boundary; missing observation coverage is never invented.
    """
    max_segments = max(1, min(int(max_segments), _MAX_SEGMENTS))
    by_id = {row["event_id"]: (position, row) for position, row in enumerate(rows)}
    members = sorted(
        (by_id[event_id] for event_id in member_ids if event_id in by_id),
        key=lambda pair: pair[0],
    )
    if not members:
        return {
            "activity_segment_count": 0,
            "activity_segments": [],
            "activity_segments_truncated": False,
        }

    own_refs = {row["ref"] for _, row in members if row["ref"]}
    foreign_positions: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    for position, row in enumerate(rows):
        scope = row["scope"]
        if (scope is not None and row["ref"] and row["ref"] not in own_refs
                and not row["reference_tainted"]):
            foreign_positions[scope].append(position)

    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    boundary: list[str] = []
    previous_position: int | None = None
    previous_row: dict[str, Any] | None = None

    def emit() -> None:
        if not current:
            return
        if len(segments) < max_segments:
            segments.append({
                "segment_index": len(segments) + 1,
                "boundary_before": list(boundary),
                "first_observed_at": current[0]["observed_at"],
                "last_observed_at": current[-1]["observed_at"],
                "observed_event_count": len(current),
                "event_ids": [row["event_id"] for row in current[:_MAX_SEGMENT_EVENT_IDS]],
                "event_ids_truncated": len(current) > _MAX_SEGMENT_EVENT_IDS,
            })

    total_segments = 0
    for position, row in members:
        reasons: list[str] = []
        if previous_row is not None and previous_position is not None:
            if row["scope"] != previous_row["scope"]:
                reasons.append("capture_scope_change")
            elif row["scope"] is not None:
                positions = foreign_positions.get(row["scope"], [])
                at = bisect_right(positions, previous_position)
                if at < len(positions) and positions[at] < position:
                    reasons.append("interleaved_other_record")
        if not current or reasons:
            if current:
                emit()
            current = []
            boundary = reasons
            total_segments += 1
        current.append(row)
        previous_position, previous_row = position, row
    emit()
    return {
        "activity_segment_count": total_segments,
        "activity_segments": segments,
        "activity_segments_truncated": total_segments > len(segments),
    }


__all__ = ["observed_activity_segments"]
