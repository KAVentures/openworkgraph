from __future__ import annotations

"""Translate OpenTelemetry GenAI spans into OpenWorkGraph structural agent events.

Only a small allowlist of structural attributes is read. Prompt/message contents,
tool arguments/results, native events and arbitrary span attributes are never
copied into OpenWorkGraph evidence.
"""

from datetime import datetime, timezone
import re
from typing import Any


_MODEL_OPERATIONS = {"chat", "generate_content", "text_completion", "embeddings"}
_RUN_OPERATIONS = {"invoke_agent", "invoke_workflow"}
_TOOL_OPERATIONS = {"execute_tool", "retrieval"}


def _value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
        if key in value:
            return value[key]
    array = value.get("arrayValue")
    if isinstance(array, dict) and isinstance(array.get("values"), list):
        return [_value(x) for x in array["values"]]
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


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except Exception:
        return None
    return number if 0 <= number <= 1_000_000_000 else None


def _iso_from_nanos(value: Any) -> str:
    try:
        nanos = int(value)
    except Exception:
        return ""
    return datetime.fromtimestamp(nanos / 1_000_000_000, timezone.utc).isoformat()


def _duration_seconds(start: Any, end: Any) -> float:
    try:
        value = (int(end) - int(start)) / 1_000_000_000
    except Exception:
        return 0.0
    return round(max(0.0, min(value, 7 * 24 * 60 * 60)), 6)


def _status(span: dict[str, Any]) -> str:
    status = span.get("status")
    if isinstance(status, dict):
        code = str(status.get("code") or "").lower()
        if "error" in code or code in {"2", "status_code_error"}:
            return "error"
    return "success"


def _tool_category(name: str, attrs: dict[str, Any], operation: str) -> str:
    if operation == "retrieval":
        return "search"
    tool_type = _text(attrs.get("gen_ai.tool.type"), 80).lower()
    haystack = " ".join([name.lower(), tool_type])
    rules = (
        (("shell", "terminal", "bash", "cmd", "powershell", "exec"), "shell"),
        (("file", "filesystem", "read_file", "write_file"), "filesystem"),
        (("browser", "playwright", "selenium", "web"), "browser"),
        (("search", "retrieval", "find", "lookup"), "search"),
        (("sql", "database", "postgres", "mysql", "sqlite", "supabase"), "database"),
        (("github", "git", "repository", "code"), "code"),
        (("slack", "email", "gmail", "message", "mail"), "messaging"),
        (("jira", "linear", "issue", "ticket"), "issue_tracker"),
        (("deploy", "vercel", "cloudflare", "release"), "deployment"),
        (("mcp",), "mcp"),
    )
    for tokens, category in rules:
        if any(token in haystack for token in tokens):
            return category
    return "other"


def _usage(attrs: dict[str, Any]) -> dict[str, int]:
    mapping = {
        "input_tokens": "gen_ai.usage.input_tokens",
        "output_tokens": "gen_ai.usage.output_tokens",
        "cached_input_tokens": "gen_ai.usage.cache_read.input_tokens",
    }
    out: dict[str, int] = {}
    for target, source in mapping.items():
        value = _int(attrs.get(source))
        if value is not None:
            out[target] = value
    if "input_tokens" in out or "output_tokens" in out:
        out["total_tokens"] = out.get("input_tokens", 0) + out.get("output_tokens", 0)
    return out


def _iter_spans(payload: dict[str, Any]):
    if isinstance(payload.get("spans"), list):
        defaults = _attrs(payload.get("resource", {}).get("attributes") if isinstance(payload.get("resource"), dict) else {})
        for span in payload["spans"]:
            if isinstance(span, dict):
                yield span, defaults, _text(payload.get("scope_name"), 160)
        return

    for resource_span in payload.get("resourceSpans") or []:
        if not isinstance(resource_span, dict):
            continue
        resource = resource_span.get("resource") if isinstance(resource_span.get("resource"), dict) else {}
        resource_attrs = _attrs(resource.get("attributes"))
        scope_groups = resource_span.get("scopeSpans") or resource_span.get("instrumentationLibrarySpans") or []
        for scope_group in scope_groups:
            if not isinstance(scope_group, dict):
                continue
            scope = scope_group.get("scope") if isinstance(scope_group.get("scope"), dict) else {}
            scope_name = _text(scope.get("name") or scope_group.get("instrumentationLibrary", {}).get("name"), 160)
            for span in scope_group.get("spans") or []:
                if isinstance(span, dict):
                    yield span, resource_attrs, scope_name


def _base_payload(
    span: dict[str, Any],
    resource_attrs: dict[str, Any],
    scope_name: str,
    defaults: dict[str, Any],
) -> tuple[dict[str, Any], str, str, str]:
    attrs = _attrs(span.get("attributes"))
    operation = _text(attrs.get("gen_ai.operation.name"), 80).lower()
    if not operation and attrs.get("gen_ai.tool.name"):
        operation = "execute_tool"

    trace_id = _text(span.get("traceId") or span.get("trace_id"), 240)
    span_id = _text(span.get("spanId") or span.get("span_id"), 240)
    parent_span_id = _text(span.get("parentSpanId") or span.get("parent_span_id"), 240)
    agent_name = _text(
        attrs.get("gen_ai.agent.name")
        or attrs.get("gen_ai.workflow.name")
        or defaults.get("agent_name")
        or resource_attrs.get("service.name")
        or "OTel Agent",
        160,
    )
    tool_name = _text(attrs.get("gen_ai.tool.name") or attrs.get("gen_ai.tool.call.name"), 200)
    conversation_id = _text(attrs.get("gen_ai.conversation.id"), 240)
    run_id = _text(attrs.get("openworkgraph.run.id") or defaults.get("run_id") or trace_id, 240)
    workflow_id = _text(attrs.get("openworkgraph.workflow.id") or defaults.get("workflow_id"), 240)
    trigger_event_id = _text(attrs.get("openworkgraph.trigger_event_id"), 240)

    start_raw = span.get("startTimeUnixNano") or span.get("start_time_unix_nano")
    end_raw = span.get("endTimeUnixNano") or span.get("end_time_unix_nano")
    started_at = _iso_from_nanos(start_raw) or _text(span.get("started_at"), 80)
    ended_at = _iso_from_nanos(end_raw) or _text(span.get("ended_at"), 80) or started_at

    payload = {
        "organization_id": _text(defaults.get("organization_id"), 240),
        "actor_id": _text(attrs.get("openworkgraph.actor.id") or defaults.get("actor_id"), 240),
        "device_id": _text(
            defaults.get("device_id")
            or resource_attrs.get("service.instance.id")
            or resource_attrs.get("host.id")
            or "agent-local",
            240,
        ),
        "sensor_id": f"otel:{scope_name}" if scope_name else "otel:genai",
        "agent_name": agent_name,
        "provider": _text(attrs.get("gen_ai.provider.name") or defaults.get("provider"), 160),
        "framework": _text(defaults.get("framework") or scope_name, 160),
        "model": _text(attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model"), 200),
        "observation_level": "native_trace",
        "run_id": run_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "workflow_id": workflow_id,
        "trigger_event_id": trigger_event_id,
        "session_id": conversation_id or run_id or trace_id,
        "usage": _usage(attrs),
        "duration_seconds": _duration_seconds(start_raw, end_raw),
    }
    return payload, operation, started_at, ended_at


def otel_payload_to_agent_events(
    payload: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
    max_spans: int = 1000,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Convert OTLP/HTTP JSON or a simplified span list to structural events."""
    if not isinstance(payload, dict):
        raise ValueError("OpenTelemetry payload must be an object")
    defaults = dict(defaults or {})
    events: list[dict[str, Any]] = []
    seen = 0
    ignored = 0

    for span, resource_attrs, scope_name in _iter_spans(payload):
        seen += 1
        if seen > max_spans:
            raise ValueError(f"OpenTelemetry batch exceeds {max_spans} spans")
        base, operation, started_at, ended_at = _base_payload(span, resource_attrs, scope_name, defaults)
        trace_id = base.get("trace_id") or "trace"
        span_id = base.get("span_id") or str(seen)
        prefix = f"otel:{trace_id}:{span_id}"
        status = _status(span)

        if operation in _RUN_OPERATIONS:
            if started_at:
                events.append({
                    **base,
                    "event_id": prefix + ":start",
                    "observed_at": started_at,
                    "operation": "run_started",
                    "status": "running",
                    "tool_name": "",
                    "tool_category": "none",
                    "duration_seconds": 0,
                })
            if ended_at:
                events.append({
                    **base,
                    "event_id": prefix + ":finish",
                    "observed_at": ended_at,
                    "operation": "run_finished",
                    "status": status,
                    "tool_name": "",
                    "tool_category": "none",
                })
            continue

        if operation in _MODEL_OPERATIONS:
            events.append({
                **base,
                "event_id": prefix,
                "observed_at": ended_at or started_at,
                "operation": "model_call",
                "status": status,
                "tool_name": "",
                "tool_category": "none",
            })
            continue

        if operation in _TOOL_OPERATIONS:
            attrs = _attrs(span.get("attributes"))
            tool_name = _text(attrs.get("gen_ai.tool.name") or attrs.get("gen_ai.tool.call.name") or span.get("name"), 200)
            events.append({
                **base,
                "event_id": prefix,
                "observed_at": ended_at or started_at,
                "operation": "tool_call",
                "status": status,
                "tool_name": tool_name,
                "tool_category": _tool_category(tool_name, attrs, operation),
            })
            continue

        ignored += 1

    return events, {"spans_seen": seen, "spans_ignored": ignored, "agent_events": len(events)}
