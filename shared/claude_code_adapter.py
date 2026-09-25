from __future__ import annotations

"""Translate Claude Code lifecycle hooks into structural OpenWorkGraph evidence.

Claude Code hook payloads can contain prompt text, tool arguments/results,
transcript paths, assistant messages, and other sensitive content. This adapter
uses a strict allowlist and never copies those payload fields into agent events.
"""

from datetime import datetime, timezone
import hashlib
import re
from typing import Any


_SUPPORTED_EVENTS = frozenset({
    "SessionStart",
    "SessionEnd",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
    "SubagentStart",
    "SubagentStop",
    "StopFailure",
})
_SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,159}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_label(value: Any, *, default: str, limit: int = 100) -> str:
    raw = _text(value, limit=limit)
    if not raw or not _SAFE_LABEL.fullmatch(raw):
        return default
    return raw


def _event_id(*parts: Any) -> str:
    # Hash native identifiers so arbitrary runtime IDs never become visible event
    # identifiers while retries remain idempotent.
    material = "\x1f".join(_text(part, 300) for part in parts).encode("utf-8")
    return "claude-hook:" + hashlib.sha256(material).hexdigest()[:40]


def _duration_seconds(payload: dict[str, Any]) -> float:
    try:
        millis = float(payload.get("duration_ms") or 0)
    except Exception:
        return 0.0
    return round(max(0.0, min(millis / 1000.0, 7 * 24 * 60 * 60)), 6)


def _tool_category(name: str) -> str:
    low = name.lower()
    if low.startswith("mcp__"):
        return "mcp"
    if low in {"bash", "shell", "terminal", "computer"} or any(
        token in low for token in ("exec", "command", "powershell")
    ):
        return "shell"
    if any(token in low for token in ("read", "write", "edit", "file", "notebook")):
        return "filesystem"
    if any(token in low for token in ("websearch", "web_search", "search", "grep", "glob")):
        return "search"
    if any(token in low for token in ("webfetch", "browser", "playwright", "chrome")):
        return "browser"
    if any(token in low for token in ("github", "git", "code")):
        return "code"
    return "other" if name else "none"


def _base_event(
    payload: dict[str, Any],
    *,
    operation: str,
    status: str,
    observed_at: str,
    run_id: str,
    trace_id: str,
    event_key: str,
    agent_name: str = "Claude Code",
    span_id: str = "",
    tool_name: str = "",
) -> dict[str, Any]:
    return {
        "event_id": _event_id(trace_id, event_key, span_id, run_id),
        "observed_at": observed_at,
        "agent_name": agent_name,
        "provider": "anthropic",
        "framework": "claude-code",
        "operation": operation,
        "status": status,
        "observation_level": "native_trace",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "tool_name": tool_name,
        "tool_category": _tool_category(tool_name),
        "duration_seconds": _duration_seconds(payload),
    }


def claude_hook_to_agent_events(
    payload: dict[str, Any],
    *,
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Project one Claude Code hook invocation into zero or one safe events.

    Hooks that expose content but do not add reliable structural workflow signal
    (for example UserPromptSubmit, PreToolUse, MessageDisplay, and Stop) are
    intentionally ignored. Tool calls are recorded only after success/failure.
    """
    if not isinstance(payload, dict):
        return []
    hook = _text(payload.get("hook_event_name"), 80)
    if hook not in _SUPPORTED_EVENTS:
        return []

    session_id = _text(payload.get("session_id"), 240)
    if not session_id:
        return []
    timestamp = _text(observed_at, 80) or _now_iso()
    trace_id = session_id

    if hook == "SessionStart":
        return [_base_event(
            payload,
            operation="run_started",
            status="running",
            observed_at=timestamp,
            run_id=session_id,
            trace_id=trace_id,
            event_key=hook,
        )]

    if hook == "SessionEnd":
        return [_base_event(
            payload,
            operation="run_finished",
            status="unknown",
            observed_at=timestamp,
            run_id=session_id,
            trace_id=trace_id,
            event_key=hook,
        )]

    if hook in {"PostToolUse", "PostToolUseFailure"}:
        tool_use_id = _text(payload.get("tool_use_id"), 240)
        tool_name = _safe_label(payload.get("tool_name"), default="unknown-tool", limit=160)
        return [_base_event(
            payload,
            operation="tool_call",
            status="success" if hook == "PostToolUse" else "error",
            observed_at=timestamp,
            run_id=session_id,
            trace_id=trace_id,
            span_id=tool_use_id,
            event_key=hook,
            tool_name=tool_name,
        )]

    if hook == "PermissionRequest":
        tool_use_id = _text(payload.get("tool_use_id"), 240)
        tool_name = _safe_label(payload.get("tool_name"), default="unknown-tool", limit=160)
        return [_base_event(
            payload,
            operation="human_approval_requested",
            status="running",
            observed_at=timestamp,
            run_id=session_id,
            trace_id=trace_id,
            span_id=tool_use_id,
            event_key=hook,
            tool_name=tool_name,
        )]

    if hook == "PermissionDenied":
        # Claude Code emits PermissionDenied in auto mode. Do not mislabel an
        # automatic policy denial as a human approval/denial decision.
        tool_use_id = _text(payload.get("tool_use_id"), 240)
        tool_name = _safe_label(payload.get("tool_name"), default="unknown-tool", limit=160)
        return [_base_event(
            payload,
            operation="error",
            status="denied",
            observed_at=timestamp,
            run_id=session_id,
            trace_id=trace_id,
            span_id=tool_use_id,
            event_key=hook,
            tool_name=tool_name,
        )]

    if hook in {"SubagentStart", "SubagentStop"}:
        agent_id = _text(payload.get("agent_id"), 240)
        if not agent_id:
            return []
        agent_type = _safe_label(payload.get("agent_type"), default="subagent", limit=80)
        sub_run_id = f"{session_id}:{agent_id}"
        return [_base_event(
            payload,
            operation="run_started" if hook == "SubagentStart" else "run_finished",
            status="running" if hook == "SubagentStart" else "unknown",
            observed_at=timestamp,
            run_id=sub_run_id,
            trace_id=trace_id,
            span_id=agent_id,
            event_key=hook,
            agent_name=f"Claude Code/{agent_type}",
        )]

    if hook == "StopFailure":
        prompt_id = _text(payload.get("prompt_id"), 240)
        return [_base_event(
            payload,
            operation="error",
            status="error",
            observed_at=timestamp,
            run_id=session_id,
            trace_id=trace_id,
            span_id=prompt_id,
            event_key=hook,
        )]

    return []
