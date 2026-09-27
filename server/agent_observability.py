from __future__ import annotations

"""Capability-aware summaries over canonical agent evidence.

Counts answer "what did OWG observe?" while capabilities answer "could the active
adapter observe this signal at all?" Keeping those separate prevents unsupported
signals from being rendered as misleading zeroes.
"""

from collections import Counter
from typing import Any

from .context_execution_linkage import _agent_groups, _meta, _one_execution


_SIGNAL_KEYS = (
    "model_call",
    "tool_call",
    "handoff",
    "human_approval_requested",
    "human_approval_received",
    "token_usage",
    "model_identity",
    "duration",
)


def _sensor_ids(events: list[dict[str, Any]]) -> set[str]:
    return {str(event.get("sensor_id") or "").strip() for event in events if event.get("sensor_id")}


def _framework(events: list[dict[str, Any]]) -> str:
    for event in events:
        meta, _trace = _meta(event)
        agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {}
        value = str(agent.get("framework") or "").strip().lower()
        if value:
            return value
    return ""


def _capability(status: str, basis: str) -> dict[str, str]:
    return {"status": status, "basis": basis}


def _capabilities(events: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    sensors = _sensor_ids(events)
    framework = _framework(events)
    caps = {key: _capability("unknown", "adapter_capability_not_declared") for key in _SIGNAL_KEYS}

    if framework == "claude-code":
        has_otel = "agent:claude-code-otel" in sensors
        has_hooks = "agent:claude-code-hook" in sensors
        if has_otel:
            for key in ("model_call", "tool_call", "handoff", "human_approval_received", "token_usage", "model_identity", "duration"):
                caps[key] = _capability("observable", "claude_code_otel_logs")
        if has_hooks:
            for key in ("tool_call", "handoff", "human_approval_requested", "duration"):
                caps[key] = _capability("observable", "claude_code_hooks")
        if not has_otel:
            for key in ("model_call", "human_approval_received", "token_usage"):
                caps[key] = _capability("not_observable", "claude_code_hooks_do_not_emit_signal")
        if not has_hooks:
            caps["human_approval_requested"] = _capability("not_observable", "claude_code_otel_reports_decision_not_prompt_open")
        return caps

    if framework == "codex":
        for key in ("model_call", "tool_call", "handoff", "human_approval_received", "model_identity", "duration"):
            caps[key] = _capability("observable", "codex_otel")
        caps["human_approval_requested"] = _capability("not_observable", "codex_otel_decision_surface_has_no_request_event")
        caps["token_usage"] = _capability("partial", "codex_usage_is_span_or_turn_dependent")
        return caps

    if framework.startswith("openai-agents"):
        for key in ("model_call", "tool_call", "handoff", "token_usage", "model_identity", "duration"):
            caps[key] = _capability("observable", "openai_agents_tracing_processor")
        for key in ("human_approval_requested", "human_approval_received"):
            caps[key] = _capability("not_observable", "openai_agents_processor_has_no_approval_lifecycle_signal")
        return caps

    if any(sensor.startswith("otel:") or sensor == "agent:otel" for sensor in sensors):
        for key in ("model_call", "tool_call", "token_usage", "model_identity", "duration"):
            caps[key] = _capability("observable", "generic_genai_otel_semantic_conventions")
        for key in ("handoff", "human_approval_requested", "human_approval_received"):
            caps[key] = _capability("not_observable", "generic_genai_otel_contract_has_no_portable_signal")
    return caps


def _effective_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer richer Claude OTel evidence where hooks report the same tool call.

    OWG intentionally keeps both canonical rows. This read-only projection avoids
    double-counting after a user upgrades an existing hook installation to OTel.
    Handoff initiation remains hook-preferred when both feeds are present, because
    the OTel subagent event is emitted on completion rather than delegation start.
    """
    framework = _framework(events)
    sensors = _sensor_ids(events)
    if framework != "claude-code" or not {"agent:claude-code-hook", "agent:claude-code-otel"}.issubset(sensors):
        return events

    hook_handoff_observed = any(
        str(event.get("sensor_id") or "") == "agent:claude-code-hook"
        and str(_meta(event)[0].get("operation") or "") == "handoff"
        for event in events
    )
    output: list[dict[str, Any]] = []
    for event in events:
        meta, _trace = _meta(event)
        operation = str(meta.get("operation") or "")
        sensor = str(event.get("sensor_id") or "")
        if operation == "tool_call" and sensor == "agent:claude-code-hook":
            continue
        if operation == "handoff" and hook_handoff_observed and sensor == "agent:claude-code-otel":
            continue
        output.append(event)
    return output


def _usage_totals(events: list[dict[str, Any]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for event in events:
        meta, _trace = _meta(event)
        usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
        for key in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"):
            try:
                value = int(usage.get(key))
            except Exception:
                continue
            if value >= 0:
                totals[key] += value
    return dict(totals)


def _models(events: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for event in events:
        meta, _trace = _meta(event)
        agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {}
        model = str(agent.get("model") or "").strip()
        if model:
            values.add(model[:200])
    return sorted(values)


def enrich_agent_execution_payload(payload: dict[str, Any], raw_events: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for events in _agent_groups(raw_events):
        if not events:
            continue
        execution = _one_execution(events)
        groups[str(execution.get("execution_id") or "")] = events

    for run in payload.get("executions") or []:
        if not isinstance(run, dict):
            continue
        events = groups.get(str(run.get("execution_id") or ""), [])
        if not events:
            continue
        effective = _effective_events(events)
        counts = Counter(str(_meta(event)[0].get("operation") or "unknown") for event in effective)
        caps = _capabilities(events)
        sensors = sorted(_sensor_ids(events))
        run["observed_operation_counts"] = dict(sorted(counts.items()))
        run["signal_capabilities"] = caps
        run["telemetry_sources"] = sensors
        run["usage_totals"] = _usage_totals(effective)
        run["models_observed"] = _models(effective)
        run["telemetry_depth"] = (
            "rich_native_events_plus_hooks"
            if {"agent:claude-code-hook", "agent:claude-code-otel"}.issubset(set(sensors))
            else "rich_native_events"
            if "agent:claude-code-otel" in sensors or _framework(events) in {"codex", "openai-agents-python"}
            else "hooks_only"
            if "agent:claude-code-hook" in sensors
            else "provider_neutral_structural"
        )

    payload["capability_semantics"] = {
        "observable": "the active adapter is designed to emit this structural signal; zero means none was observed in this evidence window",
        "partial": "the active adapter can expose the signal only on some runtime paths or span shapes",
        "not_observable": "the active adapter does not expose this signal; do not interpret absence as zero runtime activity",
        "unknown": "the integration did not declare whether this signal is observable",
    }
    return payload


__all__ = ["enrich_agent_execution_payload"]
