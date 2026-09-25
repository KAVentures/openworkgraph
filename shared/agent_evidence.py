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
_ALLOWED_TASK_CONTEXT_KEYS = frozenset({
    "preflight_attempted",
    "available",
    "resolved",
    "context_sha256",
    "policy_manifest_sha256",
    "family_key",
})
_ALLOWED_SHADOW_ENFORCEMENT_KEYS = frozenset({
    "profile_id",
    "available",
    "candidate_disposition",
    "family_key",
    "policy_manifest_sha256",
    "simulated_only",
    "actual_enforcement_enabled",
    "actual_blocking",
})
_SHADOW_PROFILE_ID = "declared-policy-shadow-v1"
_SHADOW_DISPOSITIONS = frozenset({
    "candidate_deny",
    "candidate_pause_for_human_approval",
    "candidate_pause_for_prerequisite",
    "candidate_warn",
    "no_declared_enforcement_decision",
    "no_blocking_condition_observed",
    "indeterminate_policy_unavailable",
})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FAMILY_KEY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")


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


def _task_context(payload: dict[str, Any], *, operation: str) -> dict[str, Any]:
    raw = payload.get("task_context")
    if raw in (None, {}):
        return {}
    if operation != "run_started":
        raise AgentEvidenceError("task_context is only allowed on run_started events")
    if not isinstance(raw, dict):
        raise AgentEvidenceError("task_context must be an object")
    unknown = set(raw) - _ALLOWED_TASK_CONTEXT_KEYS
    if unknown:
        raise AgentEvidenceError(f"unsupported task_context fields: {', '.join(sorted(unknown))}")
    if raw.get("preflight_attempted") is not True:
        raise AgentEvidenceError("task_context.preflight_attempted must be true")
    available = raw.get("available")
    resolved = raw.get("resolved")
    if not isinstance(available, bool) or not isinstance(resolved, bool):
        raise AgentEvidenceError("task_context available/resolved must be booleans")

    context_sha = _text(raw.get("context_sha256"), limit=80).lower()
    policy_sha = _text(raw.get("policy_manifest_sha256"), limit=80).lower()
    family_key = _text(raw.get("family_key"), limit=200).lower()
    if context_sha and not _SHA256_RE.fullmatch(context_sha):
        raise AgentEvidenceError("invalid task_context.context_sha256")
    if policy_sha and not _SHA256_RE.fullmatch(policy_sha):
        raise AgentEvidenceError("invalid task_context.policy_manifest_sha256")
    if family_key and not _FAMILY_KEY_RE.fullmatch(family_key):
        raise AgentEvidenceError("invalid task_context.family_key")

    if available and not context_sha:
        raise AgentEvidenceError("available task context requires context_sha256")
    if not available and (context_sha or policy_sha or family_key or resolved):
        raise AgentEvidenceError("unavailable task context cannot claim snapshot, policy, family, or resolution")
    if resolved and not family_key:
        raise AgentEvidenceError("resolved task context requires family_key")

    out: dict[str, Any] = {
        "preflight_attempted": True,
        "available": available,
        "resolved": resolved,
        "context_sha256": context_sha,
        "policy_manifest_sha256": policy_sha,
        "family_key": family_key,
        "linkage_assertion_source": "agent_adapter",
        "context_snapshot_verified_by_server": False,
    }
    return out


def _shadow_enforcement(payload: dict[str, Any], *, operation: str) -> dict[str, Any]:
    """Validate an optional shadow-enforcement assertion on a tool-call event.

    The assertion is intentionally tiny. It records only the fixed preview
    profile, candidate disposition, structural workflow family, optional policy
    manifest hash and explicit simulation flags. It never carries policy text,
    rule text, action arguments/results, a human identity, or an authorization
    decision.
    """
    raw = payload.get("shadow_enforcement")
    if raw in (None, {}):
        return {}
    if operation != "tool_call":
        raise AgentEvidenceError("shadow_enforcement is only allowed on tool_call events")
    if not isinstance(raw, dict):
        raise AgentEvidenceError("shadow_enforcement must be an object")
    unknown = set(raw) - _ALLOWED_SHADOW_ENFORCEMENT_KEYS
    if unknown:
        raise AgentEvidenceError(f"unsupported shadow_enforcement fields: {', '.join(sorted(unknown))}")

    profile_id = _text(raw.get("profile_id"), limit=120).lower()
    if profile_id != _SHADOW_PROFILE_ID:
        raise AgentEvidenceError("invalid shadow_enforcement.profile_id")
    available = raw.get("available")
    if not isinstance(available, bool):
        raise AgentEvidenceError("shadow_enforcement.available must be boolean")
    disposition = _text(raw.get("candidate_disposition"), limit=120).lower()
    if disposition not in _SHADOW_DISPOSITIONS:
        raise AgentEvidenceError("invalid shadow_enforcement.candidate_disposition")
    if available and disposition == "indeterminate_policy_unavailable":
        raise AgentEvidenceError("available shadow preview cannot be indeterminate_policy_unavailable")
    if not available and disposition != "indeterminate_policy_unavailable":
        raise AgentEvidenceError("unavailable shadow preview must be indeterminate_policy_unavailable")

    family_key = _text(raw.get("family_key"), limit=200).lower()
    if not family_key or not _FAMILY_KEY_RE.fullmatch(family_key):
        raise AgentEvidenceError("invalid shadow_enforcement.family_key")
    policy_sha = _text(raw.get("policy_manifest_sha256"), limit=80).lower()
    if policy_sha and not _SHA256_RE.fullmatch(policy_sha):
        raise AgentEvidenceError("invalid shadow_enforcement.policy_manifest_sha256")

    if raw.get("simulated_only") is not True:
        raise AgentEvidenceError("shadow_enforcement.simulated_only must be true")
    if raw.get("actual_enforcement_enabled") is not False:
        raise AgentEvidenceError("shadow_enforcement.actual_enforcement_enabled must be false")
    if raw.get("actual_blocking") is not False:
        raise AgentEvidenceError("shadow_enforcement.actual_blocking must be false")

    return {
        "profile_id": _SHADOW_PROFILE_ID,
        "available": available,
        "candidate_disposition": disposition,
        "family_key": family_key,
        "policy_manifest_sha256": policy_sha,
        "simulated_only": True,
        "actual_enforcement_enabled": False,
        "actual_blocking": False,
        "preview_assertion_source": "agent_adapter",
        "preview_verified_by_server": False,
    }


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

    trace = {
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": _text(payload.get("span_id"), limit=240),
        "parent_span_id": _text(payload.get("parent_span_id"), limit=240),
        "workflow_id": _text(payload.get("workflow_id"), limit=240),
    }
    trigger_event_id = _text(payload.get("trigger_event_id"), limit=240)
    if trigger_event_id:
        trace["trigger_event_id"] = trigger_event_id

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
        "trace": trace,
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
    task_context = _task_context(payload, operation=operation)
    if task_context:
        metadata["task_context"] = task_context
    shadow_enforcement = _shadow_enforcement(payload, operation=operation)
    if shadow_enforcement:
        metadata["shadow_enforcement"] = shadow_enforcement

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
