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
    "UserPromptSubmit",
    "Stop",
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
    if low.startswith("mcp__") or low == "mcp_tool":
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


def _prompt_run_id(payload: dict[str, Any], session_id: str) -> str:
    # Claude Code v2.1.196+ gives every within-turn hook the same prompt_id used
    # by its OTel prompt.id. Prefer it so hook evidence and OTel evidence land in
    # one execution. Older versions safely fall back to the session boundary.
    return _text(payload.get("prompt_id"), 128) or session_id


def _child_run_id(session_id: str, agent_id: str) -> str:
    """A subagent's own run (IDs are at most 128 chars).

    Keyed on the session and the subagent's id only: the subagent's own hooks
    need not carry the parent's prompt_id. The parent turn is linked through the
    handoff span, which equals the child's run_started span.
    """
    candidate = f"{session_id}:sub:{agent_id}"
    if len(candidate) <= 128 and re.fullmatch(r"[A-Za-z0-9_.:\-]+", candidate):
        return candidate
    return "sub:" + hashlib.sha256(f"{session_id}\x1f{agent_id}".encode("utf-8")).hexdigest()[:40]


def _run_id(payload: dict[str, Any], session_id: str) -> str:
    """The turn, or the subagent's own run when the hook fired inside a subagent."""
    agent_id = _text(payload.get("agent_id"), 128)
    return _child_run_id(session_id, agent_id) if agent_id else _prompt_run_id(payload, session_id)


def _tool_detail(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Structural facts from the tool's input/output, derived in memory only.

    The input and response are read here and never copied: only allowlisted
    command names, test counts, file types, keyed file hashes and line counts
    come out (see shared.tool_detail).
    """
    from .tool_detail import enabled, tool_call_detail

    if not enabled():
        return {}
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    response = payload.get("tool_response")
    response = response if isinstance(response, dict) else {"stdout": response} if isinstance(response, str) else {}
    try:
        if tool_name in {"Bash", "BashOutput"} and tool_input.get("command"):
            output = "\n".join(str(response.get(k) or "") for k in ("stdout", "stderr"))
            output += "\n" + str(payload.get("error") or "")
            return tool_call_detail(command=tool_input.get("command"), output=output)
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if tool_name in {"Edit", "MultiEdit", "Write", "NotebookEdit"} and path:
            patch = response.get("structuredPatch")
            if isinstance(patch, list) and patch:
                return tool_call_detail(paths=[path], patch=patch)
            if tool_name == "Write" and str(response.get("type") or "") == "create":
                return tool_call_detail(paths=[path], new_file_text=tool_input.get("content") or "")
            return tool_call_detail(paths=[path])
        if tool_name == "Read" and path:
            return tool_call_detail(paths=[path])
    except Exception:
        return {}
    return {}


def _hook_agent_name(payload: dict[str, Any]) -> str:
    agent_id = _text(payload.get("agent_id"), 128)
    if not agent_id:
        return "Claude Code"
    agent_type = _safe_label(payload.get("agent_type"), default="subagent", limit=80)
    return f"Claude Code/{agent_type}"


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
    parent_span_id: str = "",
    tool_name: str = "",
    model: str = "",
) -> dict[str, Any]:
    return {
        "event_id": _event_id(trace_id, event_key, span_id, run_id, agent_name),
        "observed_at": observed_at,
        "sensor_id": "agent:claude-code-hook",
        "session_id": trace_id,
        "agent_name": agent_name,
        "provider": "anthropic",
        "framework": "claude-code",
        "model": model,
        "operation": operation,
        "status": status,
        "observation_level": "native_trace",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "tool_name": tool_name,
        "tool_category": _tool_category(tool_name),
        "duration_seconds": _duration_seconds(payload),
    }


def claude_hook_to_agent_events(
    payload: dict[str, Any],
    *,
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Project one Claude Code hook invocation into privacy-safe structural events.

    Content-bearing hooks remain ignored. Within-turn events prefer prompt_id, so
    they correlate with Claude Code OTel without exposing prompt content. A
    SubagentStart produces both the parent handoff and a child-run boundary.
    """
    if not isinstance(payload, dict):
        return []
    hook = _text(payload.get("hook_event_name"), 80)
    if hook not in _SUPPORTED_EVENTS:
        return []

    session_id = _text(payload.get("session_id"), 128)
    if not session_id:
        return []
    timestamp = _text(observed_at, 80) or _now_iso()
    trace_id = session_id
    prompt_run_id = _run_id(payload, session_id)
    parent_run_id = _prompt_run_id(payload, session_id)
    agent_name = _hook_agent_name(payload)

    if hook == "SessionStart":
        return [_base_event(
            payload,
            operation="run_started",
            status="running",
            observed_at=timestamp,
            run_id=session_id,
            trace_id=trace_id,
            event_key=hook,
            model=_safe_label(payload.get("model"), default="", limit=160),
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

    if hook in {"UserPromptSubmit", "Stop"}:
        # One turn = one prompt: it starts when the prompt is submitted and
        # finishes when Claude stops responding. These payloads carry the prompt
        # text and the last assistant message; neither is ever read here. Without
        # a prompt_id there is no turn identity, and falling back to the session
        # would make a turn's Stop look like the whole session finishing.
        prompt_id = _text(payload.get("prompt_id"), 128)
        if not prompt_id:
            return []
        return [_base_event(
            payload,
            operation="run_started" if hook == "UserPromptSubmit" else "run_finished",
            status="running" if hook == "UserPromptSubmit" else "success",
            observed_at=timestamp,
            run_id=prompt_id,
            trace_id=trace_id,
            event_key=hook,
        )]

    if hook in {"PostToolUse", "PostToolUseFailure"}:
        tool_use_id = _text(payload.get("tool_use_id"), 128)
        tool_name = _safe_label(payload.get("tool_name"), default="unknown-tool", limit=160)
        event = _base_event(
            payload,
            operation="tool_call",
            status="success" if hook == "PostToolUse" else "error",
            observed_at=timestamp,
            run_id=prompt_run_id,
            trace_id=trace_id,
            span_id=tool_use_id,
            event_key=hook,
            tool_name=tool_name,
            agent_name=agent_name,
        )
        detail = _tool_detail(tool_name, payload)
        if detail:
            event["tool_detail"] = detail
        return [event]

    if hook == "PermissionRequest":
        tool_use_id = _text(payload.get("tool_use_id"), 128)
        tool_name = _safe_label(payload.get("tool_name"), default="unknown-tool", limit=160)
        return [_base_event(
            payload,
            operation="human_approval_requested",
            status="running",
            observed_at=timestamp,
            run_id=prompt_run_id,
            trace_id=trace_id,
            span_id=tool_use_id,
            event_key=hook,
            tool_name=tool_name,
            agent_name=agent_name,
        )]

    if hook == "PermissionDenied":
        # PermissionDenied can be an automatic policy denial. Do not turn it into
        # a human decision; Claude's tool_decision OTel event identifies the
        # actual decision source when richer telemetry is connected.
        tool_use_id = _text(payload.get("tool_use_id"), 128)
        tool_name = _safe_label(payload.get("tool_name"), default="unknown-tool", limit=160)
        return [_base_event(
            payload,
            operation="error",
            status="denied",
            observed_at=timestamp,
            run_id=prompt_run_id,
            trace_id=trace_id,
            span_id=tool_use_id,
            event_key=hook,
            tool_name=tool_name,
            agent_name=agent_name,
        )]

    if hook == "SubagentStart":
        child_id = _text(payload.get("agent_id"), 128)
        if not child_id:
            return []
        child_type = _safe_label(payload.get("agent_type"), default="subagent", limit=80)
        child_name = f"Claude Code/{child_type}"
        # One event belongs to the parent execution and records the delegation;
        # the other opens the child execution under the same prompt correlation.
        return [
            _base_event(
                payload,
                operation="handoff",
                status="running",
                observed_at=timestamp,
                run_id=parent_run_id,
                trace_id=trace_id,
                span_id=child_id,
                event_key="SubagentHandoff",
                tool_name=f"subagent:{child_type}",
                agent_name="Claude Code",
            ),
            _base_event(
                payload,
                operation="run_started",
                status="running",
                observed_at=timestamp,
                run_id=_child_run_id(session_id, child_id),
                trace_id=trace_id,
                span_id=child_id,
                event_key=hook,
                agent_name=child_name,
            ),
        ]

    if hook == "SubagentStop":
        child_id = _text(payload.get("agent_id"), 128)
        if not child_id:
            return []
        child_type = _safe_label(payload.get("agent_type"), default="subagent", limit=80)
        return [_base_event(
            payload,
            operation="run_finished",
            status="unknown",
            observed_at=timestamp,
            run_id=_child_run_id(session_id, child_id),
            trace_id=trace_id,
            span_id=child_id,
            event_key=hook,
            agent_name=f"Claude Code/{child_type}",
        )]

    if hook == "StopFailure":
        return [_base_event(
            payload,
            operation="error",
            status="error",
            observed_at=timestamp,
            run_id=prompt_run_id,
            trace_id=trace_id,
            span_id=_text(payload.get("prompt_id"), 128),
            event_key=hook,
            agent_name=agent_name,
        )]

    return []
