from __future__ import annotations

"""Agent-capture housekeeping that runs inside the real OpenWorkGraph launcher.

* Keeps the agent spool's recording lease current while capture is recording,
  and revokes it the moment capture is paused or stopped, or OpenWorkGraph exits.
* Delivers spooled agent events through the normal ingest path.
* Refreshes OpenWorkGraph's own Claude Code hook entries when a newer version
  needs more hook events (only when Claude Code Observe is on and OpenWorkGraph's
  hooks are already installed; a backup is written first).

Started from ``server.enterprise_runner.main`` only, never from app startup, so
tests and embedded apps never touch the user's Claude Code settings.
"""

import atexit
import threading
import uuid
from pathlib import Path
from typing import Any

from . import agent_spool
from . import agent_telemetry_diagnostics as diagnostics

TICK_SECONDS = 2.0
LEASE_RENEW_BEFORE_SECONDS = 60.0
FLUSH_EVERY_SECONDS = 5.0

_LEASE_ID = uuid.uuid4().hex
_STATE: dict[str, Any] = {"lease_active": False, "lease_valid_until": None, "last_flush": None}
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def _data_dir() -> Path:
    from shared.capture_control import data_dir

    return data_dir()


def _demo() -> bool:
    try:
        from .enterprise_app import _demo_mode

        return bool(_demo_mode())
    except Exception:
        return False


def _ingest_spooled(events: list[dict[str, Any]]) -> None:
    from .agent_ingest import ingest_agent_payloads
    from .agent_routes import _observation_enabled_for

    diagnostics.received("spool")
    allowed = [event for event in events if _observation_enabled_for(event)]
    if not allowed:
        diagnostics.observation_off("spool")
    else:
        diagnostics.processed("spool", ingest_agent_payloads(allowed))


def tick(now: float | None = None) -> None:
    """One lease/flush step; separated from the thread for tests."""
    import time as _time

    from shared.capture_control import read_state

    current = _time.time() if now is None else now
    recording = not _demo() and read_state().get("state") == "recording"
    if recording:
        valid_until = _STATE.get("lease_valid_until") or 0.0
        if not _STATE["lease_active"] or valid_until - current < LEASE_RENEW_BEFORE_SECONDS:
            lease = agent_spool.issue_lease(_data_dir(), lease_id=_LEASE_ID, now=current)
            _STATE.update(lease_active=True, lease_valid_until=lease["valid_until"])
    elif _STATE["lease_active"]:
        agent_spool.revoke_lease(lease_id=_LEASE_ID)
        _STATE.update(lease_active=False, lease_valid_until=None)

    last = _STATE.get("last_flush") or 0.0
    if current - last >= FLUSH_EVERY_SECONDS and agent_spool.pending_count():
        _STATE["last_flush"] = current
        if not _demo():
            agent_spool.flush_spool(_data_dir(), _ingest_spooled, now=current)


def _loop() -> None:
    while not _STOP.is_set():
        try:
            tick()
        except Exception:
            pass
        _STOP.wait(TICK_SECONDS)


def stop() -> None:
    _STOP.set()
    agent_spool.revoke_lease(lease_id=_LEASE_ID)
    _STATE.update(lease_active=False, lease_valid_until=None)


def start() -> None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return
    if not _demo():
        refresh_outdated_claude_hooks()
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="owg-agent-capture-runtime", daemon=True)
    _THREAD.start()
    atexit.register(stop)


def refresh_outdated_claude_hooks() -> dict[str, Any] | None:
    from adapters.claude_code_hook import SUPPORTED_EVENTS, settings_fragment

    from . import agent_config_writer as writer
    from .connections import is_enabled

    try:
        if not is_enabled("claude_code", "observe"):
            return None
        missing = writer.claude_missing_hook_events(SUPPORTED_EVENTS)
        if not missing:
            return None
        result = writer.claude_connect(settings_fragment)
        print(f"OpenWorkGraph: added Claude Code hooks {', '.join(missing)} (backup: {result.get('backup')}).")
        return {"added": missing, **result}
    except writer.ConfigConflict as exc:
        print(f"OpenWorkGraph: Claude Code hooks are out of date but were left unchanged: {exc}")
    except Exception:
        pass
    return None


def configuration_state() -> dict[str, Any]:
    """What is configured, as booleans and names only (no tokens or headers)."""
    from adapters.claude_code_hook import SUPPORTED_EVENTS

    from . import agent_config_writer as writer

    claude: dict[str, Any] = {}
    try:
        data = writer._load_claude(writer.claude_settings_path())
        env = data.get("env") if isinstance(data.get("env"), dict) else {}
        present = writer.claude_owg_hook_events(data)
        claude = {
            "hook_events": sorted(present),
            "missing_hook_events": sorted(set(SUPPORTED_EVENTS) - present) if present else [],
            "telemetry_enabled": str(env.get("CLAUDE_CODE_ENABLE_TELEMETRY") or "") == "1",
            "otel_logs_exporter": str(env.get("OTEL_LOGS_EXPORTER") or "") or None,
            "otel_metrics_exporter": str(env.get("OTEL_METRICS_EXPORTER") or "") or None,
            "otel_logs_endpoint_is_openworkgraph": "/agent-ingest/v1/claude-otel" in str(env.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT") or ""),
        }
    except Exception as exc:  # unreadable settings are themselves a diagnosis
        claude = {"error": type(exc).__name__}
    try:
        codex = {"configured": bool(writer.codex_status().get("configured"))}
    except Exception as exc:
        codex = {"error": type(exc).__name__}
    return {
        "claude_code": claude,
        "codex": codex,
        "agent_spool": {
            "lease_active": bool(_STATE["lease_active"]),
            "pending_files": agent_spool.pending_count(),
            "limits": {
                "lease_seconds": agent_spool.LEASE_TTL_SECONDS,
                "max_files": agent_spool.MAX_SPOOL_FILES,
                "max_age_seconds": agent_spool.MAX_AGE_SECONDS,
            },
        },
    }
