from __future__ import annotations

"""What the latest collector heartbeat says about sensor visibility."""

from typing import Any


def sensor_state(status: dict[str, Any] | None) -> dict[str, Any]:
    """Missing OS permissions and away state; a blind sensor is not "recording"."""
    activity = (status or {}).get("activity")
    activity = activity if isinstance(activity, dict) else {}
    permissions = activity.get("permissions")
    permissions = permissions if isinstance(permissions, dict) else {}
    return {
        "missing_permissions": sorted(str(k) for k, v in permissions.items() if v is False),
        "away": bool(activity.get("away")),
    }
