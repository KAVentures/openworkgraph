"""Observed activity episodes inside an already evidence-linked work item.

Episodes are presentation-only: independent foreign-record activity,
strictly witnessed different-app/tab observations without a record, a direct
within-item exact-record context change, or a capture-scope change can bound
a displayed sequence. Episodes neither create
nor remove membership edges. A clock gap alone never supplies an edge or an
interruption claim. No raw scope values or foreign record IDs are returned.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from typing import Any

_MAX_SEGMENTS = 16
_MAX_SEGMENT_EVENT_IDS = 20


def _clipboard_action(action: Any) -> bool:
    """A clipboard action is not independent evidence of tab focus."""
    return isinstance(action, str) and (
        action in ("copy", "cut", "paste")
        or action.endswith(("_copy", "_cut", "_paste"))
    )


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
    Independently named other-tab observations can similarly split an observed
    episode only between same-tab, same-app, same-source member anchors.
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
    loose_app_indices = {
        scope: [position for position, _, _ in observations]
        for scope, observations in loose_app_positions.items()
    }
    # Independent activity in a different, literal tab can interrupt an
    # observed same-tab episode. It never assigns that activity to a case.
    loose_tab_positions: dict[
        tuple[str, str, str, str], list[tuple[int, str, str, str, str]]
    ] = defaultdict(list)
    for position, row in enumerate(rows):
        scope = row["scope"]
        app, source, tab = row["app"], row["source"], row["tab"]
        if (scope is not None and all(scope) and row["event_id"] not in member_set
                and not row["ref"] and not row["transfer"]
                and not row["reference_tainted"]
                and not row.get("ephemeral_claim_tainted", False)
                and not row.get("action_claim_malformed", False)
                and not _clipboard_action(row["action"])
                and isinstance(tab, str) and bool(tab)
                and isinstance(app, str) and app not in ("", "unknown", "other",
                                                         "other app", "unknown app")
                and not any(ord(char) < 32 or ord(char) == 127 for char in app)
                and isinstance(source, str) and source not in ("", "unknown", "other")):
            loose_tab_positions[scope].append(
                (position, tab, app, source, row["observed_at"])
            )
    loose_tab_indices = {
        scope: [position for position, *_ in observations]
        for scope, observations in loose_tab_positions.items()
    }

    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    boundary: list[str] = []
    previous_position: int | None = None
    previous_row: dict[str, Any] | None = None
    # Exact-record anchors may be separated by clean, already-linked but
    # unreferenced member actions. Do not attribute those actions to either
    # record; only split at the next independently observed different record.
    last_record_anchor: dict[str, Any] | None = None
    unreferenced_since_anchor = 0
    unreferenced_bridge_clean = True
    max_unreferenced_record_bridge = 8

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
                elif (all(row["scope"]) and row["ref"] and previous_row["ref"]
                        and row["ref"] != previous_row["ref"]
                        and not row["reference_tainted"]
                        and not previous_row["reference_tainted"]
                        and previous_row["observed_at"] < row["observed_at"]):
                    # A clipboard-linked component may contain several literal
                    # source records. Keep the membership, but distinguish
                    # independently observed record-context episodes. Neither
                    # this boundary nor the transfer proves a task handoff.
                    reasons.append("observed_member_record_change")
                elif (all(row["scope"]) and row["ref"] and not previous_row["ref"]
                      and last_record_anchor is not None
                      and 0 < unreferenced_since_anchor <= max_unreferenced_record_bridge
                      and unreferenced_bridge_clean
                      and last_record_anchor["scope"] == row["scope"]
                      and last_record_anchor["ref"] != row["ref"]
                      and not row["reference_tainted"]
                      and last_record_anchor["observed_at"] < row["observed_at"]
                      and previous_row["observed_at"] < row["observed_at"]):
                    # A -> unreferenced member(s) -> B: the exact B observation
                    # begins a new record-context episode. The intervening
                    # members retain their original membership and unknown
                    # record identity. No time-only join or handoff is inferred.
                    reasons.append("observed_member_record_change")
                # A known different app strictly between two same-app anchors
                # bounds an uncertain episode, NOT a verified case switch.
                # Timestamp ties cannot be ordered by event ID alone.
                anchor_app = row["app"]
                if (not reasons and all(row["scope"])
                        and anchor_app == previous_row["app"]
                        and anchor_app not in ("", "unknown", "other")
                        and not any(ord(char) < 32 or ord(char) == 127 for char in anchor_app)):
                    scope = row["scope"]
                    loose_events = loose_app_positions.get(scope, [])
                    start = bisect_right(loose_app_indices.get(scope, []), previous_position)
                    for index in range(start, len(loose_events)):
                        loose_position, loose_app, loose_at = loose_events[index]
                        if loose_position >= position:
                            break
                        if (loose_app != anchor_app
                                and previous_row["observed_at"] < loose_at < row["observed_at"]):
                            reasons.append("observed_other_app_without_record")
                            break
                # An independently named different tab in the same known
                # browser/app and capture source is an uncertain focus
                # interruption. A reused tab ID by itself is never evidence;
                # two independently observed matching member anchors are
                # required and timestamp ties always abstain.
                anchor_tab = row["tab"]
                if (not reasons and anchor_tab and anchor_tab == previous_row["tab"]
                        and row["app"] == previous_row["app"]
                        and row["source"] == previous_row["source"]
                        and row["source"] not in ("", "unknown", "other")
                        and not row["transfer"] and not previous_row["transfer"]
                        and not _clipboard_action(row["action"])
                        and not _clipboard_action(previous_row["action"])
                        and row["app"] not in ("", "unknown", "other",
                                              "other app", "unknown app")
                        and not row.get("ephemeral_claim_tainted", False)
                        and not previous_row.get("ephemeral_claim_tainted", False)
                        and not row.get("action_claim_malformed", False)
                        and not previous_row.get("action_claim_malformed", False)
                        and previous_row["observed_at"] < row["observed_at"]):
                    scope = row["scope"]
                    other_tabs = loose_tab_positions.get(scope, [])
                    start = bisect_right(loose_tab_indices.get(scope, []),
                                         previous_position)
                    for index in range(start, len(other_tabs)):
                        loose_position, loose_tab, loose_app, loose_source, loose_at = (
                            other_tabs[index]
                        )
                        if loose_position >= position:
                            break
                        if (loose_tab != anchor_tab and loose_app == row["app"]
                                and loose_source == row["source"]
                                and previous_row["observed_at"] < loose_at
                                < row["observed_at"]):
                            reasons.append("observed_other_tab_without_record")
                            break
        if not current or reasons:
            if current:
                emit()
            current = []
            boundary = reasons
            total_segments += 1
        current.append(row)
        # Never carry a record-context witness across a separately observed
        # interruption, scope change or ambiguous/tainted intermediate member.
        if reasons:
            last_record_anchor = None
            unreferenced_since_anchor = 0
            unreferenced_bridge_clean = True
        if row["ref"]:
            last_record_anchor = (
                row if row["scope"] is not None and all(row["scope"])
                and not row["reference_tainted"]
                and not row.get("ephemeral_claim_tainted", False)
                else None
            )
            unreferenced_since_anchor = 0
            unreferenced_bridge_clean = True
        elif last_record_anchor is not None:
            unreferenced_since_anchor += 1
            unreferenced_bridge_clean = (
                unreferenced_bridge_clean
                and row["scope"] == last_record_anchor["scope"]
                and not row["reference_tainted"]
                and not row.get("ephemeral_claim_tainted", False)
                and not row.get("action_claim_malformed", False)
                and not row["transfer"]
                and not _clipboard_action(row["action"])
                and last_record_anchor["observed_at"] < row["observed_at"]
                and (previous_row is None
                     or previous_row["observed_at"] < row["observed_at"])
            )
        previous_position, previous_row = position, row
    emit()
    return {
        "activity_segment_count": total_segments,
        "activity_segments": segments,
        "activity_segments_truncated": total_segments > len(segments),
    }


__all__ = ["observed_activity_segments"]
