from __future__ import annotations

"""Independent source-operation receipts for previously observed OWG records.

This verifies *provider-defined operational states* from an authorized
fresh source read, never model self-reported success or end-customer outcomes.
It does not verify any agent actually caused the state change.
"""
from datetime import datetime, timezone
import re
import httpx
from typing import Any, Callable

from .record_graph import record_graph

_OPS = {
    "github_pr_merged": ("github", "pull_request", "merged"),
    "stripe_invoice_paid": ("stripe", "invoice", "paid"),
    "stripe_refund_processor_succeeded": ("stripe", "refund", "succeeded"),
    "jira_issue_resolved": ("jira", "issue", "done"),
}
# Exact provenance emitted by the existing opt-in source adapters. A source
# status without its authenticated read path cannot verify an operation.
_VERIFICATION_METHODS = {
    "github": "explicit_opt_in_authenticated_local_github_cli_read",
    "jira": "explicit_opt_in_site_verified_oauth_jira_issue_status_read",
    "stripe": "opted_in_restricted_key_stripe_account_and_record_get",
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
    # Source/transport outages must not become a 500 or disclose provider
    # exception text. Do not retry or widen the permitted read; leave invalid
    # requests and programming errors visible to their normal callers.
    try:
        graph = record_reader(
            resource_ref, since=since, until=until,
            include_source_status=True,
        )
    except (OSError, httpx.HTTPError):
        return _unknown(resource_ref, expected_operation, "source_read_unavailable")
    if not isinstance(graph, dict):
        return _unknown(resource_ref, expected_operation, "record_not_observed_in_permitted_window")
    # A source receipt for a different record (or from an unscoped reader)
    # cannot attest the requested exact case, even if its provider state and
    # other completeness flags look valid. Production record_graph supplies
    # both fields from the exact retained-record query.
    if (graph.get("resource_ref") != resource_ref or
        graph.get("source_rows_scoped_to_exact_record") is not True):
        return _unknown(resource_ref, expected_operation, "record_reference_scope_conflict")
    # A cached graph for a broader or different retained-history window
    # cannot attest the caller's currently permitted selection. The normal
    # record_graph echoes these exact arguments, including None boundaries.
    # Do not echo an unexpected window: it may contain sensitive context.
    if graph.get("source_window") != {"since": since, "until": until}:
        return _unknown(resource_ref, expected_operation, "source_window_scope_conflict")
    # The retained record graph independently allowlists provider, object
    # kind and source host before permitting a source-log join. Do not promote
    # a source status for an unsupported host or an injected graph whose join
    # eligibility was absent/malformed. Production record_graph emits this
    # literal Boolean from its exact observed source identity.
    if graph.get("source_log_join_supported") is not True:
        return _unknown(resource_ref, expected_operation, "source_join_not_supported")
    if graph.get("record_observed") is not True:
        return _unknown(resource_ref, expected_operation, "record_not_observed_in_permitted_window")
    # These are positive evidence assertions, not merely truthy metadata.
    # Malformed strings/integers must not attest retained record identity.
    # Missing/malformed truncation metadata is not affirmative completeness.
    # Only a literal False proves this exact retained history was untruncated.
    if (graph.get("source_event_limit_reached") is not False or
        graph.get("record_identity_complete_within_retained_window") is not True):
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
    # The reported state is independently verified only when it comes from
    # the already-permitted authenticated source-read path. A missing,
    # malformed or different method is not proof of that provenance.
    if source.get("verification_method") != _VERIFICATION_METHODS[provider]:
        return _unknown(resource_ref, expected_operation, "source_verification_provenance_conflict")
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
        if provider == "jira":
            # The adapter's independently verified resolution is scoped to
            # this Jira issue, never to customer acknowledgement or refund.
            # A missing or conflicting scope must not acquire authority from
            # the otherwise plausible status and timestamp fields.
            if detail.get("scope") != "jira_issue_only_not_customer_acknowledgement_or_refund":
                return _unknown(resource_ref, expected_operation, "provider_receipt_scope_conflict")
            # The existing Jira adapter reports a verified resolution only
            # with BOTH a resolved detail status and a timezone-aware source
            # resolution timestamp. A contradictory or incomplete detail
            # must not be promoted merely because its verified flag is true.
            if detail.get("status") != "resolved":
                return _unknown(resource_ref, expected_operation, "provider_resolution_detail_conflict")
            resolution_stamp = detail.get("source_timestamp")
            if not isinstance(resolution_stamp, str) or len(resolution_stamp) > 64:
                return _unknown(resource_ref, expected_operation, "provider_resolution_detail_conflict")
            try:
                resolved_at = datetime.fromisoformat(resolution_stamp.replace("Z", "+00:00"))
                if resolved_at.tzinfo is None or resolved_at.utcoffset() is None:
                    raise ValueError("resolution timestamp must be timezone-aware")
                # The source's resolution event cannot occur more than the
                # allowed clock skew AFTER the authenticated status read.
                if (resolved_at - when).total_seconds() > 300:
                    return _unknown(resource_ref, expected_operation, "provider_resolution_detail_conflict")
            except (ValueError, TypeError, OverflowError):
                return _unknown(resource_ref, expected_operation, "provider_resolution_detail_conflict")

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
