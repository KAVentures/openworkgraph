from __future__ import annotations

from typing import Any


def safe_collector_status(status: dict[str, Any] | None) -> dict[str, Any] | None:
    if not status:
        return None
    return {
        "connected": True,
        "received_at": status.get("received_at"),
    }


def safe_browser_status(status: dict[str, Any] | None) -> dict[str, Any] | None:
    if not status:
        return None
    return {
        "status": status.get("status"),
        "received_at": status.get("received_at"),
        "sensor_version": status.get("sensor_version"),
        "expected_sensor_version": status.get("expected_sensor_version"),
        "version_ok": status.get("version_ok"),
        "excluded": bool(status.get("excluded")),
    }


def dashboard_safe_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Remove rich context echoes that the human localhost dashboard does not need."""
    out = dict(profile)
    out["navigation_hunting_candidates"] = [
        {k: v for k, v in dict(item).items() if k not in {"resource_locator"}}
        for item in (profile.get("navigation_hunting_candidates") or [])
    ]
    out["rapid_click_candidates"] = [
        {k: v for k, v in dict(item).items() if k not in {"resource_locator", "target_label"}}
        for item in (profile.get("rapid_click_candidates") or [])
    ]
    out["dashboard_data_layer"] = "content_minimized"
    return out


__all__ = ["safe_collector_status", "safe_browser_status", "dashboard_safe_profile"]
