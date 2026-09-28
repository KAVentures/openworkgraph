from __future__ import annotations

"""Translate Gemini CLI OpenTelemetry log events into structural agent events.

Gemini CLI emits one log record per event, named by the ``event.name``
attribute. With ``logPrompts`` on (Gemini's default), the same records also
carry the prompt, request/response text and tool arguments. OpenWorkGraph turns
``logPrompts`` off when it configures Gemini CLI, and independently reads only
the allowlisted attributes below, so content never enters OpenWorkGraph even if
a user re-enables prompt logging.

Mapped:
    gemini_cli.user_prompt    -> run_started   (turn = prompt_id)
    gemini_cli.api_response   -> model_call    (model, duration, token counts)
    gemini_cli.api_error      -> model_call    (status error)
    gemini_cli.tool_call      -> tool_call     (+ human_approval_received when a
                                                person accepted/rejected/modified)
Everything else is counted as ignored.
"""

import hashlib
from typing import Any

from .claude_otel_adapter import (
    _duration_seconds,
    _hash,
    _iter_records,
    _observed_at,
    _safe_label,
    _text,
    _tool_category,
    _value,
)

_SUPPORTED = {
    "gemini_cli.user_prompt",
    "gemini_cli.api_response",
    "gemini_cli.api_error",
    "gemini_cli.tool_call",
}
# Gemini's ToolCallDecision: accept/reject/modify are a person's choice;
# auto_accept is policy and is not a human decision.
_HUMAN_DECISIONS = {"accept": "success", "reject": "denied", "modify": "success"}


def _int(value: Any) -> int | None:
    try:
        number = int(_value(value) if isinstance(value, dict) else value)
    except Exception:
        return None
    return number if 0 <= number <= 1_000_000_000 else None


def _usage(attrs: dict[str, Any]) -> dict[str, int]:
    mapping = {
        "input_tokens": "input_token_count",
        "output_tokens": "output_token_count",
        "cached_input_tokens": "cached_content_token_count",
        "total_tokens": "total_token_count",
    }
    out = {}
    for target, source in mapping.items():
        value = _int(attrs.get(source))
        if value is not None:
            out[target] = value
    return out


def _event_id(run_id: str, key: str, attrs: dict[str, Any], native: dict[str, Any]) -> str:
    # One id per native record (timestamp is per record), so exporter retries are
    # idempotent and several model calls in one prompt stay distinct.
    stamp = _text(attrs.get("event.timestamp"), 80) or _text(native.get("timeUnixNano"), 40)
    material = f"{run_id}\x1f{key}\x1f{stamp}".encode("utf-8")
    return "gemini-otel:" + hashlib.sha256(material).hexdigest()[:40]


def _event_name(attrs: dict[str, Any]) -> str:
    raw = _text(attrs.get("event.name"), 120).lower()
    return raw if raw.startswith("gemini_cli.") else (f"gemini_cli.{raw}" if raw else "")


def gemini_otel_to_agent_events(
    payload: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
    max_records: int = 1000,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not isinstance(payload, dict):
        raise ValueError("Gemini CLI OpenTelemetry payload must be an object")
    defaults = dict(defaults or {})
    events: list[dict[str, Any]] = []
    seen = ignored = 0

    for native, attrs, resource_attrs in _iter_records(payload, max_records):
        seen += 1
        name = _event_name(attrs)
        if name not in _SUPPORTED:
            ignored += 1
            continue
        session_id = _text(attrs.get("session.id") or resource_attrs.get("session.id"), 128)
        prompt_id = _text(attrs.get("prompt_id"), 128)
        run_id = prompt_id or session_id
        if not run_id:
            ignored += 1
            continue

        def base(operation: str, status: str, *, tool_name: str = "", span_id: str = "", key: str = name) -> dict[str, Any]:
            return {
                "event_id": _event_id(run_id, key, attrs, native),
                "observed_at": _observed_at(attrs, native),
                "organization_id": _text(defaults.get("organization_id"), 128),
                "actor_id": _text(defaults.get("actor_id"), 128),
                "device_id": _text(defaults.get("device_id"), 128) or "gemini-local",
                "sensor_id": "agent:gemini-cli-otel",
                "session_id": session_id or run_id,
                "agent_name": "Gemini CLI",
                "provider": "google",
                "framework": "gemini-cli",
                "model": _safe_label(attrs.get("model"), limit=200),
                "operation": operation,
                "status": status,
                "observation_level": "native_trace",
                "run_id": run_id,
                "trace_id": session_id or run_id,
                "span_id": span_id,
                "tool_name": tool_name,
                "tool_category": _tool_category(tool_name, attrs) if tool_name else "none",
                "duration_seconds": _duration_seconds(attrs.get("duration_ms")),
                "usage": _usage(attrs) if operation == "model_call" else {},
            }

        if name == "gemini_cli.user_prompt":
            events.append(base("run_started", "running"))
        elif name == "gemini_cli.api_response":
            code = _int(attrs.get("status_code"))
            events.append(base("model_call", "success" if code in (None, 200) else "error"))
        elif name == "gemini_cli.api_error":
            events.append(base("model_call", "error"))
        elif name == "gemini_cli.tool_call":
            tool = _safe_label(attrs.get("function_name"), default="unknown-tool", limit=160)
            if str(_value(attrs.get("tool_type")) or "") == "mcp":
                server = _safe_label(attrs.get("mcp_server_name"), default="", limit=80)
                tool = f"mcp__{server}__{tool}" if server else f"mcp__{tool}"
            call_key = "tool:" + _hash(f"{prompt_id}|{tool}|{_observed_at(attrs, native)}")
            raw_success = _value(attrs.get("success"))
            success = str(raw_success).lower() if raw_success is not None else ""
            tool_status = "success" if success == "true" else "error" if success == "false" else "unknown"
            events.append(base("tool_call", tool_status, tool_name=tool, span_id=call_key))
            decision = _text(attrs.get("decision"), 40).lower()
            if decision in _HUMAN_DECISIONS:
                events.append(base(
                    "human_approval_received", _HUMAN_DECISIONS[decision],
                    tool_name=tool, span_id=call_key, key=name + ":decision",
                ))

    unique: dict[str, dict[str, Any]] = {}
    for event in events:
        unique.setdefault(str(event["event_id"]), event)
    return list(unique.values()), {"records_seen": seen, "records_ignored": ignored, "agent_events": len(unique)}
