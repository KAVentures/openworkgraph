from __future__ import annotations

"""Read-only linkage between task-context preflight and later agent execution.

The linkage is derived from canonical agent evidence. A run_started event may carry
only privacy-safe preflight hashes/booleans. This module joins that structural
assertion to the run's observed structural outcome without claiming that the
server independently attested delivery of the context or that context caused the
outcome.
"""

from collections import defaultdict
import json
import re
from typing import Any

from .procedural_memory import (
    _agent_family,
    _agent_outcome,
    _agent_step,
    _event_ref,
    _execution_id,
)


_FAMILY_KEY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")
_EXPLICIT_FAILURES = frozenset({"error", "denied", "cancelled"})


def _meta(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    return meta, trace


def _agent_groups(raw_events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in raw_events:
        if str(event.get("source") or "") != "agent" and not str(event.get("event_type") or "").startswith("agent_"):
            continue
        _metadata, trace = _meta(event)
        native_run = str(trace.get("run_id") or trace.get("trace_id") or event.get("session_id") or "").strip()
        if not native_run:
            continue
        actor = str(event.get("actor_id") or "agent")
        groups[(actor, native_run)].append(event)
    output = []
    for events in groups.values():
        events.sort(key=lambda item: str(item.get("observed_at") or ""))
        output.append(events)
    output.sort(key=lambda events: str(events[0].get("observed_at") or "") if events else "", reverse=True)
    return output


def _normalized_link(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _link_for_run(events: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None, str | None]:
    links: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for event in events:
        meta, _trace = _meta(event)
        value = meta.get("task_context")
        if isinstance(value, dict):
            links.append((event, value))
    if not links:
        return "not_observed", None, None

    distinct = {_normalized_link(value) for _event, value in links}
    if len(distinct) != 1:
        return "conflicting_assertions", None, None

    event, link = links[0]
    if link.get("available") is not True:
        status = "preflight_unavailable"
    elif link.get("resolved") is True:
        status = "context_resolved"
    else:
        status = "context_available_unresolved"
    return status, dict(link), _event_ref(event.get("event_id"))


def _one_execution(events: list[dict[str, Any]]) -> dict[str, Any]:
    first = events[0]
    first_meta, first_trace = _meta(first)
    actor = str(first.get("actor_id") or "agent")
    native_run = str(first_trace.get("run_id") or first_trace.get("trace_id") or first.get("session_id") or "")
    started_at = first.get("observed_at")
    ended_at = events[-1].get("observed_at")
    execution_id = _execution_id("agent", f"{actor}|{native_run}", started_at)

    steps: list[str] = []
    approval_request_count = 0
    approval_received_count = 0
    for event in events:
        metadata, _trace = _meta(event)
        operation = str(metadata.get("operation") or "").strip().lower()
        if operation == "human_approval_requested":
            approval_request_count += 1
        elif operation == "human_approval_received":
            approval_received_count += 1
        step = _agent_step(event)
        if step and (not steps or steps[-1] != step):
            steps.append(step)
        if len(steps) >= 48:
            break

    observed_family = ""
    observed_family_basis = ""
    if steps:
        observed_family, observed_family_basis = _agent_family(events, steps)
    outcome, outcome_basis = _agent_outcome(events)
    link_status, link, link_event_ref = _link_for_run(events)

    preflight_family = str((link or {}).get("family_key") or "")
    family_consistent: bool | None = None
    if observed_family and preflight_family:
        family_consistent = observed_family == preflight_family

    return {
        "execution_id": execution_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "observation_level": str(first_meta.get("observation_level") or "unknown"),
        "outcome_status": outcome,
        "outcome_basis": outcome_basis,
        "explicit_failure": outcome in _EXPLICIT_FAILURES,
        "observed_family_key": observed_family or None,
        "observed_family_basis": observed_family_basis or None,
        "preflight_family_key": preflight_family or None,
        "family_consistent": family_consistent,
        "structural_step_count": len(steps),
        "approval_request_count": approval_request_count,
        "approval_received_count": approval_received_count,
        "approval_requested": approval_request_count > 0,
        "approval_received": approval_received_count > 0,
        "linkage_status": link_status,
        "preflight_attempted": link is not None or link_status == "conflicting_assertions",
        "context_available": bool((link or {}).get("available")),
        "context_resolved": bool((link or {}).get("resolved")),
        "context_sha256": str((link or {}).get("context_sha256") or "") or None,
        "policy_manifest_sha256": str((link or {}).get("policy_manifest_sha256") or "") or None,
        "linkage_assertion_source": (link or {}).get("linkage_assertion_source") if link else None,
        "context_snapshot_verified_by_server": bool((link or {}).get("context_snapshot_verified_by_server")) if link else False,
        "link_event_ref": link_event_ref,
        "derived": True,
        "causal_claim": False,
    }


def derive_context_executions(raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive privacy-safe agent execution/linkage records from canonical evidence.

    This is intentionally an internal-data primitive for read-only views. It
    returns no native run/session/trace/span identifiers and performs no writes.
    """
    return [_one_execution(events) for events in _agent_groups(raw_events) if events]


def context_execution_linkage(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str = "",
    limit: int = 50,
    include_unlinked: bool = True,
) -> dict[str, Any]:
    """Return bounded structural agent executions with optional task-context linkage."""
    family = str(family_key or "").strip().lower()
    if family and not _FAMILY_KEY_RE.fullmatch(family):
        raise ValueError("invalid family_key")

    executions = derive_context_executions(raw_events)
    if family:
        executions = [
            item for item in executions
            if item.get("observed_family_key") == family or item.get("preflight_family_key") == family
        ]
    if not include_unlinked:
        executions = [item for item in executions if item.get("linkage_status") != "not_observed"]

    bounded = executions[:max(1, min(int(limit), 200))]
    return {
        "executions": bounded,
        "returned": len(bounded),
        "agent_execution_count_considered": len(executions),
        "preflight_attempted_count": sum(1 for item in executions if item.get("preflight_attempted")),
        "context_available_count": sum(1 for item in executions if item.get("context_available")),
        "context_resolved_count": sum(1 for item in executions if item.get("context_resolved")),
        "unlinked_count": sum(1 for item in executions if item.get("linkage_status") == "not_observed"),
        "conflicting_linkage_count": sum(1 for item in executions if item.get("linkage_status") == "conflicting_assertions"),
        "derived": True,
        "authoritative": False,
        "source": "canonical_agent_evidence",
        "linkage_is_adapter_reported": True,
        "context_snapshot_server_attested": False,
        "causal_interpretation": False,
        "interpretation": "structural context-to-execution linkage only; no claim that context caused the observed outcome",
    }
