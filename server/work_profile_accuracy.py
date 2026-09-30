from __future__ import annotations

"""Accuracy helpers for work-profile scopes without changing legacy semantics."""

from datetime import datetime, timezone
from typing import Any

from .work_profile import (
    _ai_usage,
    _communication_actions,
    _context_rows,
    _focus_rows,
    _fragmentation,
    _hunting_candidates,
    _iso,
    _rhythm,
    _transfer_patterns,
    list_self_tags,
)


def local_day_since(*, now: datetime | None = None) -> str:
    point = now or datetime.now().astimezone()
    if point.tzinfo is None:
        point = point.replace(tzinfo=timezone.utc).astimezone()
    else:
        point = point.astimezone()
    midnight = point.replace(hour=0, minute=0, second=0, microsecond=0)
    return _iso(midnight)


def compute_today_work_profile(*, now: datetime | None = None) -> dict[str, Any]:
    """Same deterministic profile as the legacy helper, limited to local today."""
    since = local_day_since(now=now)
    focus = _focus_rows(since)
    context = _context_rows(since)
    transfers = _transfer_patterns(context)
    tags = list_self_tags(since=since)
    return {
        "scope": "today",
        "since": since,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_layer": "derived_local_work_profile",
        "fragmentation": _fragmentation(focus),
        "manual_transfer_patterns": transfers,
        "manual_transfer_count": sum(int(x.get("count") or 0) for x in transfers),
        "cross_surface_manual_transfer_count": sum(int(x.get("count") or 0) for x in transfers if x.get("cross_surface")),
        "daily_rhythm": _rhythm(focus),
        "ai_tool_usage": _ai_usage(focus, transfers, context),
        "communication_actions": _communication_actions(context),
        "navigation_hunting_candidates": _hunting_candidates(context),
        "self_tags": tags,
        "privacy": {
            "new_sensor_data_required": False,
            "typed_text_captured": False,
            "individual_key_identities_captured": False,
            "clipboard_contents_captured": False,
            "self_tags_are_voluntary": True,
        },
        "interpretation": {
            "not_a_productivity_score": True,
            "derived_metrics_are_regeneratable": True,
            "manual_transfers_use_existing_clipboard_transfer_links": True,
            "navigation_hunting_candidates_require_review": True,
            "communication_actions_are_workload_context_not_quality": True,
            "daily_gaps_are_not_assumed_to_be_breaks": True,
        },
    }


def profile_has_observed_work(profile: Any) -> bool:
    if not isinstance(profile, dict):
        return False
    fragmentation = profile.get("fragmentation") if isinstance(profile.get("fragmentation"), dict) else {}
    if float(fragmentation.get("foreground_seconds") or 0) > 0:
        return True
    return bool(
        profile.get("manual_transfer_count")
        or profile.get("ai_tool_usage")
        or profile.get("communication_actions", {}).get("total") if isinstance(profile.get("communication_actions"), dict) else False
    )


__all__ = ["local_day_since", "compute_today_work_profile", "profile_has_observed_work"]
