from __future__ import annotations

"""Read-only business case handoff over *existing* OWG record-graph evidence.

Unlike Open Work inventory, this requires a previously observed opaque
owg:r: record. It is safe for existing local MCP clients with the usual
history lease and AI access. It does not grant source-inventory scanning,
business-system writes, or an agent's authority to execute work.
"""
from typing import Any, Callable

from .record_graph import record_graph


def observed_business_case_context(
    resource_ref: str, *, since: str | None = None,
    until: str | None = None, include_source_status: bool = False,
    record_reader: Callable[..., dict[str, Any]] = record_graph,
) -> dict[str, Any]:
    graph = record_reader(
        resource_ref, since=since, until=until,
        include_source_status=include_source_status,
    )
    if not isinstance(graph, dict):
        raise ValueError("invalid source record graph")
    source = graph.get("source_evidence")
    if not isinstance(source, dict):
        source = {}
    known = bool(graph.get("record_observed"))
    provider = (graph.get("record_type") or {}).get("provider") if isinstance(graph.get("record_type"), dict) else None
    kind = (graph.get("record_type") or {}).get("kind") if isinstance(graph.get("record_type"), dict) else None
    source_known = (source.get("availability") == "source_reported" and
                    source.get("source_state_reported") is True and
                    bool(source.get("verified_at")))
    # Preserve the difference between processor/provider completion and an
    # independently confirmed end-customer/business outcome.
    operation = source.get("verified_operational_outcome")
    safe_operation: dict[str, Any] = {}
    if isinstance(operation, dict) and operation.get("verified") is True:
        value = str(operation.get("kind") or "")
        scope = str(operation.get("scope") or "")
        if value in {"stripe_invoice_paid", "stripe_refund_processor_succeeded"} and scope == "stripe_processor_only":
            safe_operation = {"kind": value, "verified": True, "scope": scope}
    timeline = []
    for event in (graph.get("timeline") or [])[:100]:
        if not isinstance(event, dict):
            continue
        timeline.append({
            "event_id": event.get("event_id", ""),
            "observed_at": event.get("observed_at", ""),
            "observed_action_type": event.get("observed_action_type", "unknown"),
            "source": event.get("source", "unknown"),
        })

    return {
        "format": "openworkgraph.observed-case-context.v1",
        "record_ref": resource_ref,
        "record_observed": known,
        "record_type": {"provider": provider, "kind": kind} if known else None,
        "timeline": timeline,
        "timeline_truncated": bool(graph.get("timeline_truncated")),
        "record_identity_complete_within_retained_window": bool(
            graph.get("record_identity_complete_within_retained_window")
        ),
        "source_reported_status": {
            "availability": "source_reported" if source_known else "unknown",
            "state": source.get("source_state") if source_known else "unknown",
            "verified_at": source.get("verified_at") if source_known else None,
            "verified_operational_outcome": safe_operation or None,
            "end_customer_outcome_verified": False,
        },
        "business_objective": "not_declared_for_this_case",
        "task_expected_outcome": "not_declared_for_this_case",
        "business_policy": "not_inferred_from_observed_actions",
        "workflow_mapping": {
            "existing_workflow_discovery_remains_available": True,
            "find_repeated_workflows": "discover related candidate workflow families separately",
            "get_workflow_evidence": "inspect evidence before drafting procedures",
            "related_workflow_family_verified": False,
        },
        "confidence_and_unknowns": {
            "no_record_is_not_no_work": True,
            "no_claim_of_verified_handoff": True,
            "business_obligation": "unknown",
            "customer_acceptance": "unknown",
            "agent_completion_not_equivalent_to_business_success": True,
        },
        "authorization": {
            "read_only": True,
            "may_contact_customer": False,
            "may_send_invoice": False,
            "may_issue_refund": False,
            "agent_execution_authorized": False,
            "observed_behavior_is_authority": False,
        },
        "does_not_query_open_work_inventory": True,
        "canonical_evidence_unchanged": True,
    }


__all__ = ["observed_business_case_context"]
