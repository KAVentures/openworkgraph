from __future__ import annotations

"""Vendor-neutral structural evidence contract for AI-agent execution.

This module intentionally captures *what an agent did* without capturing prompt,
response, tool-argument, tool-result, or chain-of-thought content. Adapters for
specific runtimes should translate their native traces into this contract before
persistence.
"""

import re
import uuid
from typing import Any


AGENT_OPERATIONS = frozenset({
    "run_started",
    "run_finished",
    "model_call",
    "tool_call",
    "handoff",
    "human_approval_requested",
    "human_approval_received",
    "error",
})

AGENT_STATUSES = frozenset({"running", "success", "error", "cancelled", "denied", "unknown"})

AGENT_OBSERVATION_LEVELS = frozenset({
    "native_trace",
    "instrumented_tools",
    "mcp_only",
    "os_observed",
    "outcome_only",
})

AGENT_TOOL_CATEGORIES = frozenset({
    "filesystem",
    "shell",
    "browser",
    "code",
    "search",
    "network",
    "database",
    "messaging",
    "issue_tracker",
    "deployment",
    "mcp",
    "other",
    "none",
})

_FORBIDDEN_CONTENT_KEYS = frozenset({
    "prompt",
    "prompts",
    "message",
    "messages",
    "completion",
    "completions",
    "content",
    "input",
    "output",
    "request",
    "response",
    "reasoning",
    "chain_of_thought",
    "chain-of-thought",
    "tool_arguments",
    "tool_args",
    "tool_input",
    "tool_result",
    "tool_results",
    "tool_output",
})

_ALLOWED_USAGE_KEYS = frozenset({"input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"})


class AgentEvidenceError(ValueError):
    """Raised when an adapter attempts to emit an invalid or unsafe agent event."""


def _text(value: Any, *, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _required_text(payload: dict[str, Any], key: str, *, limit: int = 240) -> str:
    value = _text(payload.get(key), limit=limit)
    if not value:
        raise AgentEvidenceError(f"{key} is required")
    return value


def _enum(payload: dict[str, Any], key: str, allowed: frozenset[str], *, default: str | None = None) -> str:
    raw = _text(payload.get(key) if key in payload else default, limit=120).lower()
    if not raw or raw not in allowed:
        raise AgentEvidenceError(f"invalid {key}: {raw or '<empty>'}")
    return raw


def _nonnegative_number(value: Any, *, maximum: float) -> float:
    try:
        number = float(value or 0)
    except Exception as exc:
        raise AgentEvidenceError("duration_seconds must be numeric") from exc
    if number < 0 or number > maximum:
        raise AgentEvidenceError("duration_seconds out of range")
    return number


def _usage(payload: dict[str, Any]) -> dict[str, int]:
    raw = payload.get("usage")
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise AgentEvidenceError("usage must be an object")
    unknown = set(raw) - _ALLOWED_USAGE_KEYS
    if unknown:
        raise AgentEvidenceError(f"unsupported usage fields: {', '.join(sorted(unknown))}")
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            amount = int(value)
        except Exception as exc:
            raise AgentEvidenceError(f"{key} must be an integer") from exc
        if amount < 0 or amount > 1_000_000_000:
            raise AgentEvidenceError(f"{key} out of range")
        out[key] = amount
    return out


def _assert_no_content_fields(value: Any, *, path: str = "event") -> None:
    """Fail closed if an adapter tries to send content-bearing fields.

    This is intentionally recursive so a native trace cannot hide a prompt or
    tool result in an otherwise structural nested object.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_CONTENT_KEYS:
                raise AgentEvidenceError(f"content-bearing field is not allowed: {path}.{key}")
            _assert_no_content_fields(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_content_fields(child, path=f"{path}[{index}]")


def _actor_id(payload: dict[str, Any], agent_name: str) -> str:
    explicit = _text(payload.get("actor_id"), limit=240)
    if explicit:
        return explicit
    slug = re.sub(r"[^a-z0-9._-]+", "-", agent_name.lower()).strip("-")[:160] or "unknown"
    return f"agent:{slug}"


def agent_event_to_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert one structural agent event into OpenWorkGraph canonical evidence.

    The returned object intentionally matches the existing canonical event shape,
    so agent execution can coexist with desktop/browser evidence without a new
    database schema.
    """
    if not isinstance(payload, dict):
        raise AgentEvidenceError("agent event must be an object")
    _assert_no_content_fields(payload)

    observed_at = _required_text(payload, "observed_at", limit=80)
    agent_name = _required_text(payload, "agent_name", limit=160)
    operation = _enum(payload, "operation", AGENT_OPERATIONS)
    status = _enum(payload, "status", AGENT_STATUSES, default="unknown")
    observation_level = _enum(
        payload,
        "observation_level",
        AGENT_OBSERVATION_LEVELS,
        default="instrumented_tools",
    )
    tool_category = _enum(
        payload,
        "tool_category",
        AGENT_TOOL_CATEGORIES,
        default="none",
    )

    run_id = _text(payload.get("run_id"), limit=240)
    trace_id = _text(payload.get("trace_id"), limit=240)
    session_id = _text(payload.get("session_id"), limit=240) or run_id or trace_id
    if not session_id:
        raise AgentEvidenceError("session_id, run_id, or trace_id is required")

    metadata: dict[str, Any] = {
        "source": "agent",
        "actor_kind": "agent",
        "operation": operation,
        "status": status,
        "observation_level": observation_level,
        "agent": {
            "name": agent_name,
            "provider": _text(payload.get("provider"), limit=160),
            "framework": _text(payload.get("framework"), limit=160),
            "model": _text(payload.get("model"), limit=200),
        },
        "trace": {
            "run_id": run_id,
            "trace_id": trace_id,
            "span_id": _text(payload.get("span_id"), limit=240),
            "parent_span_id": _text(payload.get("parent_span_id"), limit=240),
            "workflow_id": _text(payload.get("workflow_id"), limit=240),
        },
        "tool": {
            "name": _text(payload.get("tool_name"), limit=200),
            "category": tool_category,
        },
        "usage": _usage(payload),
        "privacy": {
            "prompt_content_captured": False,
            "model_response_content_captured": False,
            "tool_arguments_captured": False,
            "tool_result_content_captured": False,
            "chain_of_thought_captured": False,
            "raw_native_payload_captured": False,
            "typed_values": False,
            "clipboard_contents": False,
        },
    }

    return {
        "event_id": _text(payload.get("event_id"), limit=240) or str(uuid.uuid4()),
        "observed_at": observed_at,
        "schema_version": "1.0",
        "organization_id": _text(payload.get("organization_id"), limit=240),
        "actor_id": _actor_id(payload, agent_name),
        "device_id": _text(payload.get("device_id"), limit=240) or "agent-local",
        "sensor_id": _text(payload.get("sensor_id"), limit=240) or "agent:adapter",
        "source": "agent",
        "session_id": session_id,
        "app": agent_name,
        "window_title": None,
        "event_type": f"agent_{operation}",
        "duration_seconds": _nonnegative_number(payload.get("duration_seconds"), maximum=7 * 24 * 60 * 60),
        "screenshot_path": None,
        "metadata": metadata,
    }
