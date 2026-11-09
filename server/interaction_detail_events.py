"""Stored event types of opt-in high-fidelity interaction detail (#634).

Hover dwells, drag and drop and sampled pointer trajectories describe *how* a
control was used. They are never membership evidence on their own: grouping
code may attach them to work already established by other observations of the
same exact record, but they must not create, extend or merge a record lane or a
linked work item, and a pointer sample (which carries no record) stays unassigned.
"""
from __future__ import annotations

from typing import Any

INTERACTION_DETAIL_EVENT_TYPES = frozenset({
    "browser_control_hover_dwell", "browser_drag_start", "browser_drag_drop", "browser_drag_end",
    "browser_pointer_trajectory", "screen_hover_dwell", "screen_drag", "screen_pointer_trajectory",
})


def is_interaction_detail(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    event_type = event.get("event_type")
    return isinstance(event_type, str) and event_type in INTERACTION_DETAIL_EVENT_TYPES


__all__ = ["INTERACTION_DETAIL_EVENT_TYPES", "is_interaction_detail"]
