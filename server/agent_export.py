from __future__ import annotations

"""Privacy-safe export rows derived from canonical agent execution evidence."""

from typing import Any

from .agent_execution_traces import _one_trace
from .context_execution_linkage import _agent_groups


AGENT_RUN_EXPORT_FIELDS = (
    "execution_id",
    "started_at",
    "ended_at",
    "agent_name",
    "provider",
    "framework",
    "observation_level",
    "observation_levels_observed",
    "mixed_observation_levels",
    "outcome_status",
    "outcome_basis",
    "observed_family_key",
    "observed_family_basis",
    "run_start_observed",
    "run_finish_observed",
    "complete_boundary_observed",
    "event_count_total",
    "model_call_count",
    "tool_call_count",
    "handoff_count",
    "human_approval_requested_count",
    "human_approval_received_count",
    "error_event_count",
    "failure_status_observed",
    "task_context_linkage_status",
    "tool_category_counts",
    "observed_signal_names",
    "unobserved_signal_names",
    "coverage_absence_means",
    "hidden_reasoning_observed",
    "derived",
    "authoritative",
    "source",
)


def agent_run_export_rows(raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one flat, privacy-safe export row per observed agent execution.

    This intentionally reuses the execution-trace projection instead of exporting
    native trace metadata. The returned execution ID is an opaque OpenWorkGraph
    reference; native run/trace/span IDs and event-level payloads are omitted.
    """
    exported: list[dict[str, Any]] = []
    for events in _agent_groups(raw_events):
        if not events:
            continue
        trace = _one_trace(events, max_events=1)
        agent = trace.get("agent") if isinstance(trace.get("agent"), dict) else {}
        counts = trace.get("operation_counts") if isinstance(trace.get("operation_counts"), dict) else {}
        coverage = trace.get("observed_coverage") if isinstance(trace.get("observed_coverage"), dict) else {}
        signals = coverage.get("signals_observed") if isinstance(coverage.get("signals_observed"), dict) else {}

        exported.append({
            "execution_id": trace.get("execution_id"),
            "started_at": trace.get("started_at"),
            "ended_at": trace.get("ended_at"),
            "agent_name": str(agent.get("name") or "Agent"),
            "provider": str(agent.get("provider") or ""),
            "framework": str(agent.get("framework") or ""),
            "observation_level": trace.get("observation_level"),
            "observation_levels_observed": list(coverage.get("observation_levels_observed") or []),
            "mixed_observation_levels": coverage.get("mixed_observation_levels") is True,
            "outcome_status": trace.get("outcome_status"),
            "outcome_basis": trace.get("outcome_basis"),
            "observed_family_key": trace.get("observed_family_key"),
            "observed_family_basis": trace.get("observed_family_basis"),
            "run_start_observed": trace.get("run_start_observed") is True,
            "run_finish_observed": trace.get("run_finish_observed") is True,
            "complete_boundary_observed": trace.get("complete_boundary_observed") is True,
            "event_count_total": int(trace.get("event_count_total") or 0),
            "model_call_count": int(counts.get("model_call") or 0),
            "tool_call_count": int(counts.get("tool_call") or 0),
            "handoff_count": int(counts.get("handoff") or 0),
            "human_approval_requested_count": int(trace.get("approval_request_count") or 0),
            "human_approval_received_count": int(trace.get("approval_received_count") or 0),
            "error_event_count": int(counts.get("error") or 0),
            "failure_status_observed": signals.get("failure_status") is True,
            "task_context_linkage_status": trace.get("task_context_linkage_status"),
            "tool_category_counts": dict(trace.get("tool_category_counts") or {}),
            "observed_signal_names": list(coverage.get("observed_signal_names") or []),
            "unobserved_signal_names": list(coverage.get("unobserved_signal_names") or []),
            "coverage_absence_means": coverage.get("absence_means"),
            "hidden_reasoning_observed": False,
            "derived": True,
            "authoritative": False,
            "source": "canonical_agent_evidence",
        })
    return exported
