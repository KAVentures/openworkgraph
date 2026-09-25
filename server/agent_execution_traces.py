from __future__ import annotations

"""Privacy-safe, provider-neutral structural traces for observed agent runs."""

from collections import Counter
import hashlib
import re
from typing import Any

from .context_execution_linkage import _agent_groups, _meta, _one_execution
from .procedural_memory import _agent_step, _event_ref


_FAMILY_KEY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")
_EXECUTION_ID_RE = re.compile(r"^execution:[0-9a-f]{16}$")


def _opaque_ref(prefix: str, value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return f"{prefix}:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _agent_descriptor(events: list[dict[str, Any]]) -> dict[str, str]:
    for event in events:
        meta, _trace = _meta(event)
        agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {}
        if agent:
            return {
                "name": str(agent.get("name") or "Agent")[:160],
                "provider": str(agent.get("provider") or "")[:160],
                "framework": str(agent.get("framework") or "")[:160],
            }
    return {"name": "Agent", "provider": "", "framework": ""}


def _safe_usage(meta: dict[str, Any]) -> dict[str, int]:
    raw = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    out: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"):
        try:
            amount = int(raw.get(key))
        except Exception:
            continue
        if 0 <= amount <= 1_000_000_000:
            out[key] = amount
    return out


def _event_projection(event: dict[str, Any]) -> dict[str, Any]:
    meta, trace = _meta(event)
    agent = meta.get("agent") if isinstance(meta.get("agent"), dict) else {}
    tool = meta.get("tool") if isinstance(meta.get("tool"), dict) else {}
    task_context = meta.get("task_context") if isinstance(meta.get("task_context"), dict) else {}
    shadow = meta.get("shadow_enforcement") if isinstance(meta.get("shadow_enforcement"), dict) else {}
    operation = str(meta.get("operation") or "unknown")
    status = str(meta.get("status") or "unknown")

    item: dict[str, Any] = {
        "event_ref": _event_ref(event.get("event_id")),
        "observed_at": event.get("observed_at"),
        "operation": operation,
        "status": status,
        "duration_seconds": event.get("duration_seconds") or 0.0,
        "span_ref": _opaque_ref("span", trace.get("span_id")),
        "parent_span_ref": _opaque_ref("span", trace.get("parent_span_id")),
        "structural_step": _agent_step(event) or None,
    }

    model = str(agent.get("model") or "")[:200]
    if model:
        item["model"] = model

    tool_name = str(tool.get("name") or "")[:200]
    tool_category = str(tool.get("category") or "none")[:80]
    if operation in {"tool_call", "human_approval_requested", "human_approval_received"} or tool_name:
        item["tool"] = {
            "name": tool_name or None,
            "category": tool_category,
        }

    usage = _safe_usage(meta)
    if usage:
        item["usage"] = usage

    if task_context:
        item["task_context"] = {
            "preflight_attempted": task_context.get("preflight_attempted") is True,
            "available": task_context.get("available") is True,
            "resolved": task_context.get("resolved") is True,
            "family_key": str(task_context.get("family_key") or "") or None,
            "adapter_reported": str(task_context.get("linkage_assertion_source") or "") == "agent_adapter",
            "server_attested": task_context.get("context_snapshot_verified_by_server") is True,
        }

    if shadow:
        item["shadow_enforcement"] = {
            "profile_id": str(shadow.get("profile_id") or "") or None,
            "available": shadow.get("available") is True,
            "candidate_disposition": str(shadow.get("candidate_disposition") or "") or None,
            "family_key": str(shadow.get("family_key") or "") or None,
            "simulated_only": shadow.get("simulated_only") is True,
            "actual_enforcement_enabled": shadow.get("actual_enforcement_enabled") is True,
            "actual_blocking": shadow.get("actual_blocking") is True,
            "adapter_reported": str(shadow.get("preview_assertion_source") or "") == "agent_adapter",
            "server_attested": shadow.get("preview_verified_by_server") is True,
        }

    return item


def _one_trace(events: list[dict[str, Any]], *, max_events: int) -> dict[str, Any]:
    base = _one_execution(events)
    projected = [_event_projection(event) for event in events]
    operation_counts = Counter(str(item.get("operation") or "unknown") for item in projected)
    tool_category_counts = Counter(
        str((item.get("tool") or {}).get("category") or "none")
        for item in projected
        if isinstance(item.get("tool"), dict)
    )
    structural_steps = [
        str(item.get("structural_step"))
        for item in projected
        if item.get("structural_step")
    ]
    run_start_observed = any(item.get("operation") == "run_started" for item in projected)
    run_finish_observed = any(item.get("operation") == "run_finished" for item in projected)
    bounded_events = projected[:max_events]

    return {
        "execution_id": base["execution_id"],
        "started_at": base.get("started_at"),
        "ended_at": base.get("ended_at"),
        "agent": _agent_descriptor(events),
        "observation_level": base.get("observation_level"),
        "outcome_status": base.get("outcome_status"),
        "outcome_basis": base.get("outcome_basis"),
        "observed_family_key": base.get("observed_family_key"),
        "observed_family_basis": base.get("observed_family_basis"),
        "run_start_observed": run_start_observed,
        "run_finish_observed": run_finish_observed,
        "complete_boundary_observed": run_start_observed and run_finish_observed,
        "event_count_total": len(projected),
        "event_count_returned": len(bounded_events),
        "events_truncated": len(projected) > len(bounded_events),
        "operation_counts": dict(sorted(operation_counts.items())),
        "tool_category_counts": dict(sorted(tool_category_counts.items())),
        "structural_steps": structural_steps[:96],
        "structural_steps_truncated": len(structural_steps) > 96,
        "approval_request_count": base.get("approval_request_count", 0),
        "approval_received_count": base.get("approval_received_count", 0),
        "task_context_linkage_status": base.get("linkage_status"),
        "events": bounded_events,
        "derived": True,
        "authoritative": False,
    }


def agent_execution_traces(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str = "",
    execution_id: str = "",
    limit: int = 20,
    max_events_per_execution: int = 100,
) -> dict[str, Any]:
    """Return bounded structural traces without native run/trace/span identifiers."""
    family = str(family_key or "").strip().lower()
    if family and not _FAMILY_KEY_RE.fullmatch(family):
        raise ValueError("invalid family_key")
    execution = str(execution_id or "").strip().lower()
    if execution and not _EXECUTION_ID_RE.fullmatch(execution):
        raise ValueError("invalid execution_id")

    run_limit = max(1, min(int(limit), 100))
    event_limit = max(1, min(int(max_events_per_execution), 500))

    traces: list[dict[str, Any]] = []
    considered = 0
    for events in _agent_groups(raw_events):
        if not events:
            continue
        candidate = _one_trace(events, max_events=event_limit)
        if family and candidate.get("observed_family_key") != family:
            continue
        if execution and candidate.get("execution_id") != execution:
            continue
        considered += 1
        if len(traces) < run_limit:
            traces.append(candidate)

    return {
        "executions": traces,
        "returned": len(traces),
        "agent_execution_count_considered": considered,
        "max_events_per_execution": event_limit,
        "native_run_ids_exposed": False,
        "native_trace_ids_exposed": False,
        "native_span_ids_exposed": False,
        "prompt_content_exposed": False,
        "model_response_content_exposed": False,
        "tool_arguments_exposed": False,
        "tool_results_exposed": False,
        "chain_of_thought_exposed": False,
        "derived": True,
        "authoritative": False,
        "source": "canonical_agent_evidence",
        "interpretation": "provider-neutral structural execution traces; completeness depends on which lifecycle events the instrumented agent runtime exposed",
    }
