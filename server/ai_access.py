from __future__ import annotations

import json
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.RLock()
_ENABLED = True
_LOADED = False
# New installs default AI access ON at the separate Redacted context level.
# Explicit user choices are still remembered across restarts, and the optional
# reset-on-restart privacy setting still forces access OFF for that launch.
_RESET_ON_RESTART = False
_ACTIVITY: deque[dict[str, Any]] = deque(maxlen=200)
_STATE_FILE = "ai_access.json"


def _state_path():
    from .local_auth import auth_dir

    return auth_dir() / _STATE_FILE


def _load_unlocked() -> None:
    global _ENABLED, _LOADED, _RESET_ON_RESTART
    if _LOADED:
        return
    _LOADED = True
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(value, dict):
        return
    _RESET_ON_RESTART = bool(value.get("reset_on_restart", False))
    _ENABLED = False if _RESET_ON_RESTART else bool(value.get("enabled", False))


def _save_unlocked() -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"enabled": bool(_ENABLED), "reset_on_restart": bool(_RESET_ON_RESTART)}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, path)
    except Exception:
        # Fail safe: the in-memory switch still works; it just will not be
        # remembered. Nothing is ever turned ON by a failed write.
        pass


def ai_access_enabled() -> bool:
    with _LOCK:
        _load_unlocked()
        return bool(_ENABLED)


def set_ai_access(enabled: bool) -> bool:
    """Turn AI access on or off. Remembered across restarts unless reset_on_restart is set."""
    global _ENABLED
    with _LOCK:
        _load_unlocked()
        _ENABLED = bool(enabled)
        _save_unlocked()
        return _ENABLED


def resets_on_restart() -> bool:
    with _LOCK:
        _load_unlocked()
        return bool(_RESET_ON_RESTART)


def set_reset_on_restart(value: bool) -> bool:
    global _RESET_ON_RESTART
    with _LOCK:
        _load_unlocked()
        _RESET_ON_RESTART = bool(value)
        _save_unlocked()
        return _RESET_ON_RESTART


def record_mcp_activity(entry: dict[str, Any]) -> dict[str, Any]:
    safe = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "tool": str(entry.get("tool") or "unknown")[:120],
        "status": str(entry.get("status") or "ok")[:32],
        "rows": max(0, int(entry.get("rows") or 0)),
        "bytes": max(0, int(entry.get("bytes") or 0)),
        "range_start": str(entry.get("range_start") or "")[:80],
        "range_end": str(entry.get("range_end") or "")[:80],
        "client": str(entry.get("client") or "")[:40],
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
    global _ENABLED, _LOADED, _RESET_ON_RESTART
    with _LOCK:
        _ENABLED = False
        _RESET_ON_RESTART = False
        _LOADED = True
        _ACTIVITY.clear()
        try:
            _state_path().unlink(missing_ok=True)
        except Exception:
            pass
