from __future__ import annotations

"""Translate Codex OTLP JSON into structural OpenWorkGraph agent evidence.

Codex can export diagnostic log records containing prompts, account identifiers,
tool arguments, tool output, and error strings. This module intentionally reads
only a small structural allowlist. It never copies OTLP record bodies or arbitrary
attributes into canonical evidence.
"""

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Iterator


_SUPPORTED_EVENTS = frozenset({
    "codex.conversation_starts",
    "codex.tool_result",
    "codex.tool_decision",
    "codex.api_request",
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
        return int(value)
    except Exception:
        return None


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


def _safe_label(value: Any, *, default: str = "", limit: int = 160) -> str:
    raw = _text(value, min(limit, 200))
    if not raw or not _SAFE_LABEL.fullmatch(raw):
        return default
    return raw[:limit]


def _hash_part(value: Any) -> str:
    raw = _text(value, 400)
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _event_id(conversation_id: str, event_name: str, attrs: dict[str, Any]) -> str:
    """Stable across Codex log + trace copies without exposing native IDs."""
    if event_name == "codex.conversation_starts":
        discriminator = "conversation-start"
    elif event_name == "codex.tool_result":
        discriminator = "tool-result|" + "|".join([
            _text(attrs.get("tool_result_seq"), 80),
            _hash_part(attrs.get("call_id")),
        ])
    elif event_name == "codex.tool_decision":
        discriminator = "tool-decision|" + "|".join([
            _hash_part(attrs.get("call_id")),
            _text(attrs.get("decision"), 80),
            _text(attrs.get("source"), 80),
        ])
    elif event_name == "codex.api_request":
        request_hash = _hash_part(attrs.get("auth.request_id"))
        discriminator = "api-request|" + "|".join([
            request_hash,
            _text(attrs.get("attempt"), 40),
            "" if request_hash else _text(attrs.get("event.timestamp"), 80),
        ])
    else:
        discriminator = _text(attrs.get("event.timestamp"), 80)
    material = f"{conversation_id}\x1f{event_name}\x1f{discriminator}".encode("utf-8")
    return "codex-otel:" + hashlib.sha256(material).hexdigest()[:40]


def _tool_category(name: str, namespace: str = "") -> str:
    low = f"{namespace} {name}".lower()
    if "mcp__" in low or namespace.lower().startswith("mcp"):
        return "mcp"
    if any(token in low for token in ("exec", "shell", "terminal", "bash", "powershell", "command")):
        return "shell"
    if any(token in low for token in ("read_file", "write_file", "edit", "filesystem", "file")):
        return "filesystem"
    if any(token in low for token in ("search", "grep", "find", "lookup", "retrieval")):
        return "search"
    if any(token in low for token in ("browser", "playwright", "selenium", "web")):
        return "browser"
    if any(token in low for token in ("github", "git", "repository", "code")):
        return "code"
    return "other" if name else "none"


def _iter_records(payload: dict[str, Any], max_records: int) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    seen = 0

    for resource_log in payload.get("resourceLogs") or []:
        if not isinstance(resource_log, dict):
            continue
        for scope_log in resource_log.get("scopeLogs") or []:
            if not isinstance(scope_log, dict):
                continue
            for record in scope_log.get("logRecords") or []:
                if not isinstance(record, dict):
                    continue
                seen += 1
                if seen > max_records:
                    raise ValueError(f"Codex OTLP payload exceeds {max_records} records")
                yield record, _attrs(record.get("attributes"))

    # Codex also emits trace-safe telemetry as span events. Read event attributes,
    # never arbitrary span attributes, span names, or native event bodies.
    for resource_span in payload.get("resourceSpans") or []:
        if not isinstance(resource_span, dict):
            continue
        for scope_span in resource_span.get("scopeSpans") or []:
            if not isinstance(scope_span, dict):
                continue
            for span in scope_span.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                for event in span.get("events") or []:
                    if not isinstance(event, dict):
                        continue
                    seen += 1
                    if seen > max_records:
                        raise ValueError(f"Codex OTLP payload exceeds {max_records} records")
                    synthetic = dict(event)
                    if "timeUnixNano" not in synthetic and span.get("endTimeUnixNano"):
                        synthetic["timeUnixNano"] = span.get("endTimeUnixNano")
                    yield synthetic, _attrs(event.get("attributes"))

    # Small simplified form for deterministic adapter tests/integrators.
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        seen += 1
        if seen > max_records:
            raise ValueError(f"Codex OTLP payload exceeds {max_records} records")
        yield record, _attrs(record.get("attributes"))


def _base(
    attrs: dict[str, Any],
    native: dict[str, Any],
    *,
    defaults: dict[str, Any],
    event_name: str,
    operation: str,
    status: str,
    tool_name: str = "",
    tool_category: str = "none",
    span_id: str = "",
) -> dict[str, Any] | None:
    conversation_id = _text(attrs.get("conversation.id"), 240)
    if not conversation_id:
        return None
    model = _safe_label(attrs.get("model"), limit=200)
    return {
        "event_id": _event_id(conversation_id, event_name, attrs),
        "observed_at": _observed_at(attrs, native),
        "organization_id": _text(defaults.get("organization_id"), 240),
        "actor_id": _text(defaults.get("actor_id"), 240),
        "device_id": _text(defaults.get("device_id"), 240) or "codex-local",
        "sensor_id": "agent:codex-otel",
        "agent_name": _safe_label(defaults.get("agent_name"), default="Codex", limit=160),
        "provider": "openai",
        "framework": "codex",
        "model": model,
        "operation": operation,
        "status": status,
        "observation_level": "native_trace",
        "run_id": conversation_id,
        "trace_id": conversation_id,
        "span_id": span_id,
        "workflow_id": _text(defaults.get("workflow_id"), 240),
        "tool_name": tool_name,
        "tool_category": tool_category,
        "duration_seconds": _duration_seconds(attrs.get("duration_ms")),
    }


def codex_otel_to_agent_events(
    payload: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
    max_records: int = 1000,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not isinstance(payload, dict):
        raise ValueError("Codex OpenTelemetry payload must be an object")
    defaults = dict(defaults or {})
    events: list[dict[str, Any]] = []
    seen = 0
    ignored = 0

    for native, attrs in _iter_records(payload, max_records):
        seen += 1
        event_name = _text(attrs.get("event.name"), 100)
        if event_name not in _SUPPORTED_EVENTS:
            ignored += 1
            continue

        projected: dict[str, Any] | None = None
        if event_name == "codex.conversation_starts":
            projected = _base(
                attrs,
                native,
                defaults=defaults,
                event_name=event_name,
                operation="run_started",
                status="running",
            )

        elif event_name == "codex.tool_result":
            tool_name = _safe_label(attrs.get("tool_name"), default="unknown-tool", limit=160)
            namespace = _safe_label(attrs.get("tool_namespace"), limit=160)
            success = _bool(attrs.get("success"))
            projected = _base(
                attrs,
                native,
                defaults=defaults,
                event_name=event_name,
                operation="tool_call",
                status="success" if success is True else "error" if success is False else "unknown",
                tool_name=tool_name,
                tool_category=_tool_category(tool_name, namespace),
                span_id=_text(attrs.get("call_id"), 240),
            )

        elif event_name == "codex.tool_decision":
            # Codex can resolve approvals through a user OR an automated reviewer.
            # Only explicit user-sourced decisions are human approval evidence.
            source = _text(attrs.get("source"), 80).lower()
            if source != "user":
                ignored += 1
                continue
            tool_name = _safe_label(attrs.get("tool_name"), default="unknown-tool", limit=160)
            namespace = _safe_label(attrs.get("tool_namespace"), limit=160)
            decision = _text(attrs.get("decision"), 80).lower()
            denied = any(token in decision for token in ("deny", "denied", "reject", "cancel"))
            approved = any(token in decision for token in ("approve", "approved", "allow"))
            projected = _base(
                attrs,
                native,
                defaults=defaults,
                event_name=event_name,
                operation="human_approval_received",
                status="denied" if denied else "success" if approved else "unknown",
                tool_name=tool_name,
                tool_category=_tool_category(tool_name, namespace),
                span_id=_text(attrs.get("call_id"), 240),
            )

        elif event_name == "codex.api_request":
            status_code = _int(attrs.get("http.response.status_code"))
            # We only inspect whether an error exists; its text is never copied.
            has_error = bool(_text(attrs.get("error.message"), 1))
            success = status_code is not None and 200 <= status_code <= 299 and not has_error
            projected = _base(
                attrs,
                native,
                defaults=defaults,
                event_name=event_name,
                operation="model_call",
                status="success" if success else "error" if has_error or (status_code or 0) >= 400 else "unknown",
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
