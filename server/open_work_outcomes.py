from __future__ import annotations

"""Independent source-operation receipts for previously observed OWG records.

This verifies *provider-defined operational states* from an authorized
fresh source read, never model self-reported success or end-customer outcomes.
It does not verify any agent actually caused the state change.
"""
from datetime import datetime, timezone
import re
from typing import Any, Callable

from .record_graph import record_graph

_OPS = {
    "github_pr_merged": ("github", "pull_request", "merged"),
    "stripe_invoice_paid": ("stripe", "invoice", "paid"),
    "stripe_refund_processor_succeeded": ("stripe", "refund", "succeeded"),
    "jira_issue_resolved": ("jira", "issue", "done"),
}
_REF = re.compile(r"^owg:r:[0-9a-f]{32}$")


def _unknown(ref: str, op: str, reason: str) -> dict[str, Any]:
    return {
        "format": "openworkgraph.source-operation-receipt.v1",
        "resource_ref": ref, "expected_operation": op,
        "availability": "unknown", "reason": reason,
        "operation_verified": False,
        "business_objective_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
        "read_only": True,
    }


def source_operation_receipt(
    resource_ref: str, expected_operation: str, *,
    since: str | None = None, until: str | None = None,
    record_reader: Callable[..., dict[str, Any]] = record_graph,
    now: datetime | None = None, max_age_seconds: int = 900,
) -> dict[str, Any]:
    if not _REF.fullmatch(str(resource_ref or "")):
        raise ValueError("resource_ref must be a normalized OWG record reference")
    if expected_operation not in _OPS:
        raise ValueError("unsupported provider operation")
    if not 1 <= max_age_seconds <= 3600:
        raise ValueError("invalid maximum source age")
    graph = record_reader(
        resource_ref, since=since, until=until,
        include_source_status=True,
    )
    if not isinstance(graph, dict) or not graph.get("record_observed"):
        return _unknown(resource_ref, expected_operation, "record_not_observed_in_permitted_window")
    if (graph.get("source_event_limit_reached") or
        not graph.get("record_identity_complete_within_retained_window", False)):
        return _unknown(resource_ref, expected_operation, "incomplete_or_conflicting_record_identity")
    provider, kind, required_state = _OPS[expected_operation]
    if graph.get("record_type") != {"provider": provider, "kind": kind}:
        return _unknown(resource_ref, expected_operation, "provider_record_type_mismatch")
    source = graph.get("source_evidence")
    if not isinstance(source, dict) or source.get("availability") != "source_reported" or source.get("source_state_reported") is not True:
        return _unknown(resource_ref, expected_operation, "authoritative_provider_status_unavailable")
    # Record identity and the independently refreshed source receipt are two
    # separate pieces of evidence. Do not promote a mixed-provider receipt
    # (or a receipt for a different object kind) into an operation success.
    # Older Stripe receipts omit object_type; their exact record kind is
    # already established above by the retained record graph. When a source
    # supplies object_type, it must agree rather than being silently ignored.
    if (source.get("provider") != provider or
        ("object_type" in source and source["object_type"] != kind)):
        return _unknown(resource_ref, expected_operation, "provider_receipt_identity_conflict")
    stamp = source.get("verified_at")
    try:
        when = datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("missing timezone")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("current timezone missing")
        age = (current - when).total_seconds()
        if age < -300 or age > max_age_seconds:
            return _unknown(resource_ref, expected_operation, "source_receipt_stale_or_clock_conflict")
    except (ValueError, TypeError, OverflowError):
        return _unknown(resource_ref, expected_operation, "invalid_provider_receipt_timestamp")
    state = source.get("source_state")
    if state != required_state:
        return _unknown(resource_ref, expected_operation, "provider_state_not_expected_operation")

    if expected_operation != "github_pr_merged":
        detail = source.get("verified_operational_outcome")
        if not isinstance(detail, dict) or detail.get("verified") is not True or detail.get("kind") != expected_operation:
            return _unknown(resource_ref, expected_operation, "provider_operation_not_independently_verified")
        if provider == "stripe" and detail.get("scope") != "stripe_processor_only":
            return _unknown(resource_ref, expected_operation, "provider_receipt_scope_conflict")

    return {
        "format": "openworkgraph.source-operation-receipt.v1",
        "resource_ref": resource_ref,
        "expected_operation": expected_operation,
        "availability": "source_reported",
        "provider": provider,
        "provider_record_kind": kind,
        "provider_reported_state": state,
        "verified_at": when.astimezone(timezone.utc).isoformat(),
        "operation_verified": True,
        "verification_scope": "authenticated_source_provider_operational_state",
        "business_objective_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
        "no_customer_identity_or_native_record_id_included": True,
        "read_only": True,
    }


__all__ = ["source_operation_receipt"]
