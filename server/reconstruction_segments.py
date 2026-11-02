"""Observed activity episodes inside an already evidence-linked work item.

Episodes are presentation-only: independent foreign-record activity, a
strictly witnessed different-app observation without a record, or a
capture-scope change can bound a displayed sequence. Episodes neither create
nor remove membership edges. A clock gap alone never supplies an edge or an
interruption claim. No raw scope values or foreign record IDs are returned.
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
    into this item's interruption boundaries. A different, known app without
    a source record can create an *uncertain* boundary only when strictly
    ordered between same-app member anchors in a complete exact capture scope.
    An observed scope change is a separate boundary; missing observation
    coverage is never invented.
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
    member_set = {row["event_id"] for _, row in members}
    foreign_positions: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    # Loose app switches are negative continuity evidence, never record identity.
    # Only complete capture scopes and untainted source-observed rows qualify.
    loose_app_positions: dict[tuple[str, str, str, str], list[tuple[int, str, str]]] = defaultdict(list)
    for position, row in enumerate(rows):
        scope = row["scope"]
        if (scope is not None and row["ref"] and row["ref"] not in own_refs
                and not row["reference_tainted"]):
            foreign_positions[scope].append(position)
        app = row["app"]
        if (scope is not None and all(scope) and not row["ref"]
                and row["event_id"] not in member_set
                and not row["reference_tainted"]
                and not row.get("ephemeral_claim_tainted", False)
                and isinstance(app, str) and app not in ("", "unknown", "other")
                and not any(ord(char) < 32 or ord(char) == 127 for char in app)
                and row["source"] not in ("", "unknown", "other")):
            loose_app_positions[scope].append((position, app, row["observed_at"]))

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
                # A known different app strictly between two same-app anchors
                # bounds an uncertain episode, NOT a verified case switch.
                # Timestamp ties cannot be ordered by event ID alone.
                anchor_app = row["app"]
                if (not reasons and all(row["scope"])
                        and anchor_app == previous_row["app"]
                        and anchor_app not in ("", "unknown", "other")
                        and not any(ord(char) < 32 or ord(char) == 127 for char in anchor_app)):
                    for loose_position, loose_app, loose_at in loose_app_positions.get(row["scope"], []):
                        if loose_position >= position:
                            break
                        if (previous_position < loose_position
                                and loose_app != anchor_app
                                and previous_row["observed_at"] < loose_at < row["observed_at"]):
                            reasons.append("observed_other_app_without_record")
                            break
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
