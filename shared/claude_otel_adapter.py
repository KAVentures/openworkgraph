from __future__ import annotations

"""Translate Claude Code OTLP log events into privacy-safe OWG agent evidence.

Claude Code can emit prompt/response text, tool parameters/content, raw API bodies,
filesystem paths and identity attributes. This adapter deliberately ignores all of
that. It reads only a small structural allowlist from documented Claude Code events.
"""

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Iterator


_SUPPORTED_EVENTS = frozenset({
    "claude_code.user_prompt",
    "claude_code.api_request",
    "claude_code.api_error",
    "claude_code.api_refusal",
    "claude_code.tool_result",
    "claude_code.tool_decision",
    "claude_code.api_retries_exhausted",
    "claude_code.subagent_completed",
})
_HUMAN_DECISION_SOURCES = frozenset({
    "user_permanent",
    "user_temporary",
    "user_abort",
    "user_reject",
})
_SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,199}$")


def _value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
        if key in value:
            return value[key]
    return None


def _attrs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    out: dict[str, Any] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key:
                out[key] = _value(item.get("value"))
    return out


def _text(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_label(value: Any, *, default: str = "", limit: int = 160) -> str:
    raw = _text(value, min(limit, 200))
    if not raw or not _SAFE_LABEL.fullmatch(raw):
        return default
    return raw[:limit]


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    low = _text(value, 20).lower()
    if low in {"true", "1", "yes"}:
        return True
    if low in {"false", "0", "no"}:
        return False
    return None


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except Exception:
        return None
    return number if 0 <= number <= 1_000_000_000 else None


def _duration_seconds(value: Any) -> float:
    try:
        millis = float(value or 0)
    except Exception:
        return 0.0
    return round(max(0.0, min(millis / 1000.0, 7 * 24 * 60 * 60)), 6)


def _iso_from_nanos(value: Any) -> str:
    try:
        nanos = int(value)
    except Exception:
        return ""
    try:
        return datetime.fromtimestamp(nanos / 1_000_000_000, timezone.utc).isoformat()
    except Exception:
        return ""


def _observed_at(attrs: dict[str, Any], native: dict[str, Any]) -> str:
    timestamp = _text(attrs.get("event.timestamp"), 80)
    if timestamp:
        return timestamp
    for key in ("timeUnixNano", "time_unix_nano", "observedTimeUnixNano", "observed_time_unix_nano"):
        parsed = _iso_from_nanos(native.get(key))
        if parsed:
            return parsed
    return datetime.now(timezone.utc).isoformat()


def _hash(value: Any) -> str:
    raw = _text(value, 500)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] if raw else ""


def _body_text(body: Any) -> str:
    value = _value(body)
    return _text(value, 120) if isinstance(value, (str, int, float, bool)) else ""


def _event_name(record: dict[str, Any], attrs: dict[str, Any]) -> str:
    raw = _text(attrs.get("event.name"), 120).lower()
    if raw.startswith("claude_code."):
        return raw
    if raw:
        candidate = f"claude_code.{raw}"
        if candidate in _SUPPORTED_EVENTS:
            return candidate
    body = _body_text(record.get("body")).lower()
    if body.startswith("claude_code."):
        return body
    return ""


def _iter_records(payload: dict[str, Any], max_records: int) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    seen = 0
    for resource_log in payload.get("resourceLogs") or []:
        if not isinstance(resource_log, dict):
            continue
        resource = resource_log.get("resource") if isinstance(resource_log.get("resource"), dict) else {}
        resource_attrs = _attrs(resource.get("attributes"))
        for scope_log in resource_log.get("scopeLogs") or []:
            if not isinstance(scope_log, dict):
                continue
            for record in scope_log.get("logRecords") or []:
                if not isinstance(record, dict):
                    continue
                seen += 1
                if seen > max_records:
                    raise ValueError(f"Claude Code OTLP payload exceeds {max_records} records")
                yield record, _attrs(record.get("attributes")), resource_attrs

    # Deterministic small form for adapter tests and custom relays.
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        seen += 1
        if seen > max_records:
            raise ValueError(f"Claude Code OTLP payload exceeds {max_records} records")
        yield record, _attrs(record.get("attributes")), _attrs(payload.get("resource"))


def _usage(attrs: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    mapping = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cached_input_tokens": "cache_read_tokens",
    }
    for target, source in mapping.items():
        amount = _int(attrs.get(source))
        if amount is not None:
            out[target] = amount
    if "input_tokens" in out or "output_tokens" in out:
        out["total_tokens"] = out.get("input_tokens", 0) + out.get("output_tokens", 0)
    return out


def _tool_category(name: str, attrs: dict[str, Any]) -> str:
    low = name.lower()
    tool_source = _text(attrs.get("tool_source"), 80).lower()
    if tool_source in {"mcp", "sdk_host_builtin_mcp"} or low.startswith("mcp__") or low == "mcp_tool":
        return "mcp"
    if low in {"bash", "shell", "terminal", "computer"} or any(token in low for token in ("exec", "command", "powershell")):
        return "shell"
    if any(token in low for token in ("read", "write", "edit", "file", "notebook")):
        return "filesystem"
    if any(token in low for token in ("search", "grep", "glob", "find", "lookup")):
        return "search"
    if any(token in low for token in ("webfetch", "browser", "playwright", "chrome")):
        return "browser"
    if any(token in low for token in ("github", "git", "code")):
        return "code"
    return "other" if name else "none"


def _event_id(run_id: str, event_name: str, attrs: dict[str, Any]) -> str:
    if event_name in {"claude_code.tool_result", "claude_code.tool_decision"}:
        discriminator = _text(attrs.get("tool_use_id"), 240) or _text(attrs.get("event.sequence"), 80)
    elif event_name in {"claude_code.api_request", "claude_code.api_error", "claude_code.api_refusal"}:
        discriminator = (
            _text(attrs.get("request_id"), 240)
            or _text(attrs.get("client_request_id"), 240)
            or _text(attrs.get("event.sequence"), 80)
        )
    elif event_name == "claude_code.subagent_completed":
        discriminator = "|".join([
            _text(attrs.get("event.sequence"), 80),
            _safe_label(attrs.get("agent_type"), default="subagent", limit=80),
        ])
    else:
        discriminator = _text(attrs.get("event.sequence"), 80) or _text(attrs.get("event.timestamp"), 80)
    material = f"{run_id}\x1f{event_name}\x1f{discriminator}".encode("utf-8")
    return "claude-otel:" + hashlib.sha256(material).hexdigest()[:40]


def _base(
    *,
    attrs: dict[str, Any],
    resource_attrs: dict[str, Any],
    native: dict[str, Any],
    event_name: str,
    operation: str,
    status: str,
    defaults: dict[str, Any],
    tool_name: str = "",
    span_id: str = "",
) -> dict[str, Any] | None:
    session_id = _text(attrs.get("session.id") or resource_attrs.get("session.id"), 128)
    prompt_id = _text(attrs.get("prompt.id"), 128)
    run_id = prompt_id or session_id
    if not run_id:
        return None
    model = _safe_label(attrs.get("model"), limit=200)
    return {
        "event_id": _event_id(run_id, event_name, attrs),
        "observed_at": _observed_at(attrs, native),
        "organization_id": _text(defaults.get("organization_id"), 128),
        "actor_id": _text(defaults.get("actor_id"), 128),
        "device_id": _text(defaults.get("device_id"), 128) or "claude-local",
        "sensor_id": "agent:claude-code-otel",
        "session_id": session_id or run_id,
        "agent_name": _safe_label(defaults.get("agent_name"), default="Claude-Code", limit=160),
        "provider": "anthropic",
        "framework": "claude-code",
        "model": model,
        "operation": operation,
        "status": status,
        "observation_level": "native_trace",
        "run_id": run_id,
        "trace_id": session_id or run_id,
        "span_id": span_id,
        "tool_name": tool_name,
        "tool_category": _tool_category(tool_name, attrs),
        "duration_seconds": _duration_seconds(attrs.get("duration_ms")),
        "usage": _usage(attrs),
    }


def claude_otel_to_agent_events(
    payload: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
    max_records: int = 1000,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Project documented Claude Code log events without copying event content."""
    if not isinstance(payload, dict):
        raise ValueError("Claude Code OpenTelemetry payload must be an object")
    defaults = dict(defaults or {})
    events: list[dict[str, Any]] = []
    seen = 0
    ignored = 0

    for native, attrs, resource_attrs in _iter_records(payload, max_records):
        seen += 1
        event_name = _event_name(native, attrs)
        if event_name not in _SUPPORTED_EVENTS:
            ignored += 1
            continue

        projected: dict[str, Any] | None = None
        if event_name == "claude_code.user_prompt":
            projected = _base(
                attrs=attrs,
                resource_attrs=resource_attrs,
                native=native,
                event_name=event_name,
                operation="run_started",
                status="running",
                defaults=defaults,
            )

        elif event_name in {"claude_code.api_request", "claude_code.api_error", "claude_code.api_refusal"}:
            status = "success" if event_name == "claude_code.api_request" else "error" if event_name == "claude_code.api_error" else "denied"
            request_id = _text(attrs.get("request_id") or attrs.get("client_request_id"), 128)
            projected = _base(
                attrs=attrs,
                resource_attrs=resource_attrs,
                native=native,
                event_name=event_name,
                operation="model_call",
                status=status,
                defaults=defaults,
                span_id=("request:" + _hash(request_id)) if request_id else "",
            )

        elif event_name == "claude_code.tool_result":
            tool_name = _safe_label(attrs.get("tool_name"), default="unknown-tool", limit=160)
            success = _bool(attrs.get("success"))
            tool_id = _text(attrs.get("tool_use_id"), 128)
            projected = _base(
                attrs=attrs,
                resource_attrs=resource_attrs,
                native=native,
                event_name=event_name,
                operation="tool_call",
                status="success" if success is True else "error" if success is False else "unknown",
                defaults=defaults,
                tool_name=tool_name,
                span_id=("tool:" + _hash(tool_id)) if tool_id else "",
            )

        elif event_name == "claude_code.tool_decision":
            source = _text(attrs.get("source"), 80).lower()
            if source not in _HUMAN_DECISION_SOURCES:
                ignored += 1
                continue
            decision = _text(attrs.get("decision"), 40).lower()
            tool_name = _safe_label(attrs.get("tool_name"), default="unknown-tool", limit=160)
            tool_id = _text(attrs.get("tool_use_id"), 128)
            projected = _base(
                attrs=attrs,
                resource_attrs=resource_attrs,
                native=native,
                event_name=event_name,
                operation="human_approval_received",
                status="success" if decision == "accept" else "denied" if decision == "reject" else "unknown",
                defaults=defaults,
                tool_name=tool_name,
                span_id=("tool:" + _hash(tool_id)) if tool_id else "",
            )

        elif event_name == "claude_code.subagent_completed":
            agent_type = _safe_label(attrs.get("agent_type"), default="subagent", limit=80)
            projected = _base(
                attrs=attrs,
                resource_attrs=resource_attrs,
                native=native,
                event_name=event_name,
                operation="handoff",
                status="success",
                defaults=defaults,
                tool_name=f"subagent:{agent_type}",
            )

        elif event_name == "claude_code.api_retries_exhausted":
            projected = _base(
                attrs=attrs,
                resource_attrs=resource_attrs,
                native=native,
                event_name=event_name,
                operation="error",
                status="error",
                defaults=defaults,
            )

        if projected is None:
            ignored += 1
            continue
        events.append(projected)

    unique: dict[str, dict[str, Any]] = {}
    for event in events:
        unique.setdefault(str(event["event_id"]), event)
    return list(unique.values()), {
        "records_seen": seen,
        "records_ignored": ignored,
        "agent_events": len(unique),
    }
