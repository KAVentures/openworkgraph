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
from .workflow_evidence import WorkflowEvidenceError, _ts

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
# Production adapters emit explicit timezone-aware ISO date-times. Python's
# fromisoformat also accepts week dates, basic dates and space separators,
# which are not an unambiguous source receipt contract.
_SOURCE_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)

# Caller-selected history windows must be unambiguous across host timezones.
# Allow minute precision (a supported existing caller form), but never accept
# date-only, week/basic dates, spaces, or timezone-naive datetimes.
_WINDOW_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]{1,9})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)



def _parse_source_timestamp(value: Any) -> datetime:
    # Reject non-JSON timestamps and unbounded or ambiguous strings *before*
    # parsing. Both verified_at and Jira resolution dates use this contract.
    if (not isinstance(value, str) or len(value) > 64 or
        _SOURCE_TIMESTAMP.fullmatch(value) is None):
        raise ValueError("invalid source timestamp format")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("source timestamp must have a timezone")
    return parsed


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
    # Age policy is expressed in whole seconds. Reject booleans (which are
    # Python integers), fractional floats and nonnumeric inputs before any
    # source reader can be invoked; never silently coerce a policy boundary.
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
        raise ValueError("invalid maximum source age")
    # The canonical record_graph validates the selected retained-history
    # window before its own source adapter dispatch. Apply the SAME existing
    # _ts/WorkflowEvidenceError contract here too, so an alternate authorized
    # record_reader cannot perform a provider read with an invalid or reversed
    # window before the verifier discovers the scope conflict afterward.
    for name, value in (("since", since), ("until", until)):
        # None means omitted. An explicitly supplied falsey value (0, False,
        # "", [], ...) is NOT an omitted scope: the canonical reader's
        # truthiness check would otherwise skip validation before an alternate
        # authorized source-capable reader is invoked.
        if value is not None and (
            not isinstance(value, str) or len(value) > 64 or
            _WINDOW_TIMESTAMP.fullmatch(value) is None or _ts(value) is None
        ):
            raise WorkflowEvidenceError(
                f"{name} must be a timezone-aware ISO-8601 timestamp"
            )
    if since is not None and until is not None and _ts(since) >= _ts(until):
        raise WorkflowEvidenceError("until must be after since")
    # A test/evaluation clock is not a source authorization. Refuse an invalid
    # explicit clock before any provider read rather than consuming source
    # privileges and only then returning an unknown receipt.
    if now is not None and (
        not isinstance(now, datetime) or now.tzinfo is None or
        now.utcoffset() is None
    ):
        raise ValueError("now must be a timezone-aware datetime")
    # Bind the requested operation to a provider/object family *before*
    # record_graph can invoke an opt-in source adapter. A mismatched retained
    # record must not cause an unrelated provider refresh merely to reject
    # the receipt afterward. Host and tenant checks remain adapter-owned.
    provider, kind, required_state = _OPS[expected_operation]
    # Source/transport outages must not become a 500 or disclose provider
    # exception text. Do not retry or widen the permitted read; leave invalid
    # requests and programming errors visible to their normal callers.
    try:
        graph = record_reader(
            resource_ref, since=since, until=until,
            include_source_status=True,
            required_source_provider_kind=(provider, kind),
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
    # Current adapters omit a nested resource_ref: the canonical record
    # graph already binds the requested exact opaque ref. If an integration
    # reader supplies one, however, a contradictory or malformed reference
    # cannot attest a different record under the same provider and kind.
    # Do not echo that potentially sensitive mismatched value.
    if (source.get("provider") != provider or
        ("object_type" in source and source["object_type"] != kind) or
        ("resource_ref" in source and source["resource_ref"] != resource_ref)):
        return _unknown(resource_ref, expected_operation, "provider_receipt_identity_conflict")
    # The reported state is independently verified only when it comes from
    # the already-permitted authenticated source-read path. A missing,
    # malformed or different method is not proof of that provenance.
    if source.get("verification_method") != _VERIFICATION_METHODS[provider]:
        return _unknown(resource_ref, expected_operation, "source_verification_provenance_conflict")
    # Existing provider adapters explicitly bound what their operation state
    # does NOT prove: GitHub/Jira do not attest business success, and Stripe
    # processor success is not customer-bank settlement. A cache or injected
    # reader may omit these optional legacy flags, but if it supplies one
    # that contradicts the provider's scope, it cannot attest this operation.
    # Use literal True, not truthiness (1/"true" are not provenance).
    scope_flag = (
        "not_a_claim_of_customer_bank_settlement" if provider == "stripe"
        else "not_a_claim_of_business_success"
    )
    if scope_flag in source and source[scope_flag] is not True:
        return _unknown(resource_ref, expected_operation, "provider_receipt_scope_conflict")
    stamp = source.get("verified_at")
    try:
        when = _parse_source_timestamp(stamp)
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

    # The production GitHub adapter proves its merge state directly and
    # does not emit a nested operation detail. Preserve that contract, but
    # never ignore a contradictory detail supplied by another authorized
    # reader/cache: explicit unverified or wrong-operation evidence cannot
    # coexist with a successful operation receipt.
    if expected_operation == "github_pr_merged" and "verified_operational_outcome" in source:
        github_detail = source["verified_operational_outcome"]
        if (not isinstance(github_detail, dict) or
            github_detail.get("verified") is not True or
            github_detail.get("kind") != expected_operation):
            return _unknown(resource_ref, expected_operation, "provider_operation_not_independently_verified")

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
            try:
                resolved_at = _parse_source_timestamp(resolution_stamp)
                # The source's resolution event cannot occur more than the
                # allowed clock skew AFTER the authenticated status read.
                # Compare against BOTH clocks: source verified_at may itself
                # be up to five minutes ahead of the local clock. Otherwise
                # stacking two independent skew allowances would accept a
                # purported resolution almost ten minutes in the future.
                if ((resolved_at - when).total_seconds() > 300 or
                    (resolved_at - current).total_seconds() > 300):
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
