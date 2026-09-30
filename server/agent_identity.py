from __future__ import annotations

"""Opaque, cross-sensor identities for native agent execution evidence.

Claude Code hooks and native session files can describe the same physical
session. This module maps native session/turn/tool identifiers onto one local
HMAC namespace before canonical persistence (and, for hooks, before the
fail-open spool). Native identifiers are never returned from these helpers.
"""

from typing import Any

from .agent_session_store import _opaque, session_ref


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _child_ref(prefix: str, session: str, kind: str, native_id: Any) -> str:
    raw = _text(native_id)
    if not raw:
        return ""
    if raw.startswith(prefix + ":"):
        return raw
    return _opaque(prefix, f"claude_code|{session}|{kind}|{raw}", chars=24)


def is_claude_code_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return False
    framework = _text(event.get("framework"), 160).lower()
    provider = _text(event.get("provider"), 160).lower()
    sensor = _text(event.get("sensor_id"), 200).lower()
    return framework == "claude-code" or sensor.startswith("agent:claude") or (
        provider == "anthropic" and "claude" in _text(event.get("agent_name"), 160).lower()
    )


def normalize_claude_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return one Claude event with native session/turn/tool IDs made opaque.

    The physical session uses exactly the same ``as:`` HMAC as the native-session
    continuity sensor. Prompt turns keep separate opaque ``ar:`` run identities so
    turn-level rework/evaluation remains meaningful; genuine subagents also keep a
    separate opaque run. SessionStart/SessionEnd and transcript-only fallback records
    whose native run is the session itself stay on the session run and can later be
    suppressed from *derived run lists* when richer turn evidence exists. Canonical
    evidence is never discarded merely because a richer sensor may also observe it.
    """
    if not is_claude_code_event(event):
        return dict(event)

    out = dict(event)
    native_session = _text(out.get("session_id") or out.get("trace_id") or out.get("run_id"))
    if not native_session:
        return out

    safe_session = native_session if native_session.startswith("as:") else session_ref("claude_code", native_session)
    if not safe_session:
        return out

    native_run = _text(out.get("run_id"))
    agent_name = _text(out.get("agent_name"), 160).lower()
    is_subagent = agent_name.startswith("claude code/")

    if not native_run or native_run in {native_session, safe_session}:
        safe_run = safe_session
    elif native_run.startswith("ar:"):
        safe_run = native_run
    else:
        # Keep top-level prompt turns and true subagents distinct, but under the
        # same opaque physical-session namespace. The kind marker avoids a
        # hypothetical native prompt id colliding with a native subagent id.
        kind = "subagent" if is_subagent else "turn"
        safe_run = _child_ref("ar", safe_session, kind, native_run)

    out["session_id"] = safe_session
    out["trace_id"] = safe_session
    if native_run or "run_id" in out:
        out["run_id"] = safe_run

    native_span = _text(out.get("span_id"))
    if native_span:
        out["span_id"] = _child_ref("at", safe_session, "span", native_span)
    native_parent = _text(out.get("parent_span_id"))
    if native_parent:
        out["parent_span_id"] = _child_ref("at", safe_session, "span", native_parent)
    return out


def normalize_agent_event(event: dict[str, Any]) -> dict[str, Any]:
    return normalize_claude_event(event) if is_claude_code_event(event) else dict(event)


def normalize_agent_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_agent_event(event) for event in events if isinstance(event, dict)]


__all__ = [
    "is_claude_code_event",
    "normalize_claude_event",
    "normalize_agent_event",
    "normalize_agent_events",
]
