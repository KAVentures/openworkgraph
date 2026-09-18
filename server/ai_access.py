from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.RLock()
_ENABLED = False
_ACTIVITY: deque[dict[str, Any]] = deque(maxlen=200)


def ai_access_enabled() -> bool:
    with _LOCK:
        return bool(_ENABLED)


def set_ai_access(enabled: bool) -> bool:
    """Enable/disable AI access for this API process only.

    The state deliberately resets to OFF whenever OpenWorkGraph restarts. This
    makes ongoing MCP access an explicit per-run user choice rather than a
    persistent background permission.
    """
    global _ENABLED
    with _LOCK:
        _ENABLED = bool(enabled)
        return _ENABLED


def record_mcp_activity(entry: dict[str, Any]) -> dict[str, Any]:
    safe = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "tool": str(entry.get("tool") or "unknown")[:120],
        "status": str(entry.get("status") or "ok")[:32],
        "rows": max(0, int(entry.get("rows") or 0)),
        "bytes": max(0, int(entry.get("bytes") or 0)),
        "range_start": str(entry.get("range_start") or "")[:80],
        "range_end": str(entry.get("range_end") or "")[:80],
        # Search terms/arguments are intentionally not logged by default.
    }
    with _LOCK:
        _ACTIVITY.appendleft(safe)
    return safe


def recent_mcp_activity(limit: int = 50) -> list[dict[str, Any]]:
    n = max(1, min(int(limit), 200))
    with _LOCK:
        return list(_ACTIVITY)[:n]


def reset_for_tests() -> None:
    global _ENABLED
    with _LOCK:
        _ENABLED = False
        _ACTIVITY.clear()
