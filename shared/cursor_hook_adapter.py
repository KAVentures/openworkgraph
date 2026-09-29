from __future__ import annotations

"""Translate Cursor agent hooks into structural OpenWorkGraph evidence.

Cursor hook payloads can contain the prompt, attachments, agent text, tool
inputs/outputs, file edits, shell commands, error messages, the user's email,
workspace paths and transcript paths. This adapter reads only identifiers,
the hook name, the tool name, statuses and durations; nothing else is copied.

    sessionStart / sessionEnd      -> session starts / finishes (conversation_id)
    beforeSubmitPrompt / stop      -> turn starts / finishes (generation_id)
    postToolUse / postToolUseFailure -> tool_call
    subagentStop                   -> handoff to a subagent, with its outcome

Permission hooks (preToolUse, beforeShellExecution, beforeMCPExecution,
beforeReadFile, subagentStart) are never registered: they must return a
decision, and an observer must not be able to block or approve anything.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

SUPPORTED_EVENTS = (
    "sessionStart",
    "sessionEnd",
    "beforeSubmitPrompt",
    "postToolUse",
    "postToolUseFailure",
    "subagentStop",
    "stop",
)
_SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/ -]{0,159}$")
_STATUS = {"completed": "success", "success": "success", "aborted": "cancelled", "cancelled": "cancelled",
           "error": "error", "failed": "error"}


def _text(value: Any, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _label(value: Any, *, default: str, limit: int = 100) -> str:
    raw = _text(value, limit)
    return raw if raw and _SAFE_LABEL.fullmatch(raw) else default


def _event_id(*parts: Any) -> str:
    material = "\x1f".join(_text(p, 300) for p in parts).encode("utf-8")
    return "cursor-hook:" + hashlib.sha256(material).hexdigest()[:40]


def _duration(payload: dict[str, Any]) -> float:
    for key in ("duration_ms", "duration"):  # Cursor reports milliseconds
        try:
            value = float(payload.get(key))
        except Exception:
            continue
        return round(max(0.0, min(value / 1000.0, 7 * 24 * 3600)), 6)
    return 0.0


def _category(name: str) -> str:
    low = name.lower()
    if low.startswith("mcp"):
        return "mcp"
    if any(t in low for t in ("shell", "terminal", "command", "bash")):
        return "shell"
    if any(t in low for t in ("read", "edit", "write", "file", "delete")):
        return "filesystem"
    if any(t in low for t in ("search", "grep", "glob", "find", "codebase")):
        return "search"
    if any(t in low for t in ("web", "browser", "fetch")):
        return "browser"
    return "other" if name else "none"


def _tool_detail(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Structural facts from the tool input/output, derived in memory only."""
    from .tool_detail import enabled, tool_call_detail

    if not enabled():
        return {}
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    try:
        if tool_input.get("command"):
            output = f"{payload.get('tool_output') or ''}\n{payload.get('error_message') or ''}"
            return tool_call_detail(command=tool_input.get("command"), output=output)
        path = tool_input.get("file_path") or tool_input.get("target_file") or tool_input.get("path")
        if path and any(t in tool.lower() for t in ("read", "edit", "write", "delete", "file")):
            return tool_call_detail(paths=[path])
    except Exception:
        return {}
    return {}


def cursor_hook_to_agent_events(payload: dict[str, Any], *, observed_at: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    hook = _text(payload.get("hook_event_name"), 80)
    if hook not in SUPPORTED_EVENTS:
        return []
    conversation = _text(payload.get("conversation_id") or payload.get("session_id"), 128)
    if not conversation:
        return []
    generation = _text(payload.get("generation_id"), 128)
    timestamp = _text(observed_at, 80) or datetime.now(timezone.utc).isoformat()

    workspace: dict[str, str] = {}
    roots = payload.get("workspace_roots")
    if isinstance(roots, list) and roots:
        try:
            from .tool_detail import enabled, workspace_ref

            ref = workspace_ref(roots[0]) if enabled() else ""
            workspace = {"workspace_ref": ref} if ref else {}
        except Exception:
            workspace = {}

    def event(operation: str, status: str, run_id: str, key: str, *, tool: str = "", span: str = "") -> dict[str, Any]:
        return {
            "event_id": _event_id(conversation, run_id, key, span),
            "observed_at": timestamp,
            "sensor_id": "agent:cursor-hook",
            "session_id": conversation,
            "agent_name": "Cursor",
            "provider": "cursor",
            "framework": "cursor",
            "model": _label(payload.get("model"), default="", limit=160),
            "operation": operation,
            "status": status,
            "observation_level": "native_trace",
            "run_id": run_id,
            "trace_id": conversation,
            "span_id": span,
            "parent_span_id": "",
            "tool_name": tool,
            "tool_category": _category(tool),
            "duration_seconds": _duration(payload),
        } | workspace

    if hook == "sessionStart":
        return [event("run_started", "running", conversation, hook)]
    if hook == "sessionEnd":
        status = _STATUS.get(_text(payload.get("final_status") or payload.get("reason"), 40).lower(), "unknown")
        return [event("run_finished", status, conversation, hook)]
    # Turn-level hooks need the generation: falling back to the conversation would
    # make a turn's stop look like the whole session finishing.
    if not generation:
        return []
    if hook == "beforeSubmitPrompt":
        return [event("run_started", "running", generation, hook)]
    if hook == "stop":
        status = _STATUS.get(_text(payload.get("status"), 40).lower(), "unknown")
        return [event("run_finished", status, generation, hook)]
    if hook in {"postToolUse", "postToolUseFailure"}:
        tool = _label(payload.get("tool_name"), default="unknown-tool", limit=160)
        span = _text(payload.get("tool_use_id"), 128)
        if hook == "postToolUse":
            status = "success"
        elif payload.get("is_interrupt") is True:
            status = "cancelled"
        elif "den" in _text(payload.get("failure_type"), 40).lower():
            status = "denied"
        else:
            status = "error"
        projected = event("tool_call", status, generation, hook, tool=tool, span=span)
        detail = _tool_detail(tool, payload)
        if detail:
            projected["tool_detail"] = detail
        try:
            from .tool_detail import pr_refs

            tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
            watch = pr_refs(command=tool_input.get("command"), tool_name=str(payload.get("tool_name") or ""), output=payload.get("tool_output"))
        except Exception:
            watch = []
        if watch:
            projected["pr_watch"] = watch
        return [projected]
    if hook == "subagentStop":
        kind = _label(payload.get("subagent_type"), default="subagent", limit=80)
        status = _STATUS.get(_text(payload.get("status"), 40).lower(), "unknown")
        return [event("handoff", status, generation, hook, tool=f"subagent:{kind}", span=_text(payload.get("subagent_id"), 128))]
    return []
