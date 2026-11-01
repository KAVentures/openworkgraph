"""Independent provider operation receipts: never agent-self-reported success."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import httpx

from server.open_work_outcomes import source_operation_receipt

REF = "owg:r:" + "a" * 32
WHEN = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)

# These are the current opt-in read paths emitted by production adapters,
# not inferred authority from source status or observed human activity.
SOURCE_READ_METHOD = {
    "github": "explicit_opt_in_authenticated_local_github_cli_read",
    "jira": "explicit_opt_in_site_verified_oauth_jira_issue_status_read",
    "stripe": "opted_in_restricted_key_stripe_account_and_record_get",
}


def response(provider="stripe", kind="invoice", state="paid",
             operation="stripe_invoice_paid", verified=True):
    detail = {
        "kind": operation, "scope": "stripe_processor_only",
        "verified": verified,
    }
    if provider == "jira":
        # Match the existing production Jira adapter's independent
        # resolution-detail contract, not just its top-level "done" state.
        detail.update(
            status="resolved" if verified else "unverified",
            source_timestamp=WHEN.isoformat() if verified else None,
            scope="jira_issue_only_not_customer_acknowledgement_or_refund",
        )
    return {
        "resource_ref": REF,
        "source_rows_scoped_to_exact_record": True,
        "source_log_join_supported": True,
        "source_window": {"since": None, "until": None},
        "record_observed": True,
        "record_type": {"provider": provider, "kind": kind},
        "record_identity_complete_within_retained_window": True,
        "source_event_limit_reached": False,
        "source_evidence": {
            "provider": provider, "availability": "source_reported",
            "source_state_reported": True, "source_state": state,
            "verified_at": WHEN.isoformat(),
            "verification_method": SOURCE_READ_METHOD[provider],
            "verified_operational_outcome": detail,
            "private_customer": "VERY_PRIVATE_CUSTOMER",
        },
    }


def reader(value, calls):
    def lookup(ref, **kwargs):
        calls.append((ref, kwargs))
        return value
    return lookup


def test_verified_invoice_payment_is_processor_state_only():
    calls = []
    result = source_operation_receipt(
        REF, "stripe_invoice_paid",
        record_reader=reader(response(), calls), now=WHEN,
    )
    assert result["operation_verified"] is True
    assert result["provider_reported_state"] == "paid"
    assert result["customer_outcome_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert calls[0][1]["include_source_status"] is True
    assert "VERY_PRIVATE" not in str(result)


def test_github_merge_provider_status_not_deployment_or_customer_outcome():
    result = source_operation_receipt(REF, "github_pr_merged", now=WHEN,
        record_reader=lambda *_a, **_kw: response(
            provider="github", kind="pull_request", state="merged",
        ))
    assert result["operation_verified"] is True
    assert result["customer_outcome_verified"] is False


def test_jira_requires_explicit_operational_resolution_receipt():
    data = response(provider="jira", kind="issue", state="done",
                    operation="jira_issue_resolved")
    assert data["source_evidence"]["verified_operational_outcome"]["scope"] == (
        "jira_issue_only_not_customer_acknowledgement_or_refund"
    )
    r = source_operation_receipt(
        REF, "jira_issue_resolved",
        record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert r["operation_verified"] is True


@pytest.mark.parametrize("delta", [-301, 901])
def test_stale_or_future_source_clock_is_unknown(delta):
    altered = response()
    altered["source_evidence"]["verified_at"] = (
        WHEN.replace(second=0) -
        __import__("datetime").timedelta(seconds=delta)
    ).isoformat()
    r = source_operation_receipt(
        REF, "stripe_invoice_paid",
        record_reader=lambda *_a, **_kw: altered, now=WHEN,
    )
    assert r["availability"] == "unknown"


@pytest.mark.parametrize("alter", [
    lambda d: d.update(record_observed=False),
    lambda d: d.update(source_event_limit_reached=True),
    lambda d: d.update(record_identity_complete_within_retained_window=False),
    lambda d: d.update(record_type={"provider": "jira", "kind": "issue"}),
    lambda d: d["source_evidence"].update(availability="unknown"),
    lambda d: d["source_evidence"].update(source_state="open"),
    lambda d: d["source_evidence"]["verified_operational_outcome"].update(verified=False),
    lambda d: d["source_evidence"]["verified_operational_outcome"].update(kind="jira_issue_resolved"),
    lambda d: d["source_evidence"]["verified_operational_outcome"].update(scope="unknown"),
])
def test_unverified_or_conflicting_states_never_become_success(alter):
    state = response()
    alter(state)
    r = source_operation_receipt(
        REF, "stripe_invoice_paid",
        record_reader=lambda *_a, **_kw: state, now=WHEN,
    )
    assert r["operation_verified"] is False
    assert r["business_objective_verified"] is False


def test_invalid_request_does_not_refresh_source():
    with pytest.raises(ValueError):
        source_operation_receipt("in_native_12345", "stripe_invoice_paid",
            record_reader=lambda *_a, **_kw: pytest.fail("provider read"))
    with pytest.raises(ValueError):
        source_operation_receipt(REF, "send_customer_email",
            record_reader=lambda *_a, **_kw: pytest.fail("provider read"))


def test_new_receipt_mcp_uses_authorize_tool_and_secure_get(monkeypatch):
    from mcp_server.outcome_receipt_tools import register_outcome_receipt_tools
    from mcp_server import outcome_receipt_tools as optional_tool
    monkeypatch.setattr(optional_tool, "experimental_tools_enabled", lambda: True)
    calls = []
    class Stub:
        def tool(self, **kw):
            def wrap(fn):
                self.fn = fn
                return fn
            return wrap
    mcp=Stub()
    runtime=SimpleNamespace(
        authorize_tool=lambda name: calls.append(("authorize", name)),
        secure_get=lambda path, params: calls.append(("get", path, params)) or {"operation_verified": False},
        core=SimpleNamespace(_finish=lambda name, value: calls.append(("finish", name)) or value),
    )
    register_outcome_receipt_tools(mcp, runtime)
    with pytest.raises(Exception):
        mcp.fn(REF, "delete_everything")
    assert calls == []
    mcp.fn(REF, "github_pr_merged")
    assert calls[0] == ("authorize", "verify_case_operation")
    assert calls[1][1] == "/v1/record-graph/operation-receipt"


def test_history_guard_applies_to_receipts_too():
    from mcp_server.history_guard import install_history_guard
    calls=[]
    def get(path, params=None):
        if path == "/v1/history/ai-access":
            return {"access": {"mode": "off"}}
        if path == "/v1/capture/status":
            return {"run_started_at": "2026-10-19T09:00:00Z"}
        calls.append((path, params))
        return {}
    runtime=SimpleNamespace(secure_get=get,core=SimpleNamespace(_get=get))
    install_history_guard(runtime)
    runtime.secure_get("/v1/record-graph/operation-receipt", {
        "resource_ref": REF, "expected_operation": "stripe_invoice_paid",
        "since": "2026-01-01T00:00:00Z",
    })
    assert calls == [("/v1/record-graph/operation-receipt", {
        "resource_ref": REF, "expected_operation": "stripe_invoice_paid",
        "since": "2026-10-19T09:00:00+00:00",
    })]


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), [
    ("github_pr_merged", "github", "pull_request", "merged"),
    ("stripe_invoice_paid", "stripe", "invoice", "paid"),
    ("stripe_refund_processor_succeeded", "stripe", "refund", "succeeded"),
    ("jira_issue_resolved", "jira", "issue", "done"),
])
def test_receipt_requires_independent_matching_source_provider_identity(
    operation, provider, kind, state,
):
    # The observed record type alone cannot authorize a source receipt.
    # All four provider operation families must reject a separately supplied
    # source_evidence identity conflict, even when state/detail say verified.
    for bad_provider in ("other_provider", None, False, {"name": provider}):
        data = response(provider=provider, kind=kind, state=state, operation=operation)
        data["source_evidence"]["provider"] = bad_provider
        calls = []
        result = source_operation_receipt(
            REF, operation, record_reader=reader(data, calls), now=WHEN,
        )
        assert result["availability"] == "unknown"
        assert result["reason"] == "provider_receipt_identity_conflict"
        assert result["operation_verified"] is False
        assert result["business_objective_verified"] is False
        assert result["customer_outcome_verified"] is False
        assert result["agent_execution_authorized"] is False
        assert len(calls) == 1
        assert calls[0][1]["include_source_status"] is True
        assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state", "wrong_kind"), [
    ("github_pr_merged", "github", "pull_request", "merged", "issue"),
    ("stripe_invoice_paid", "stripe", "invoice", "paid", "refund"),
    ("stripe_refund_processor_succeeded", "stripe", "refund", "succeeded", "invoice"),
    ("jira_issue_resolved", "jira", "issue", "done", "pull_request"),
])
def test_receipt_rejects_conflicting_optional_source_object_kind(
    operation, provider, kind, state, wrong_kind,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_evidence"]["object_type"] = wrong_kind
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "provider_receipt_identity_conflict"
    assert result["operation_verified"] is False

    # Source adapters differ: Stripe's older receipt has no object_type,
    # while Jira/GitHub include it. Both valid forms remain compatible.
    data["source_evidence"]["object_type"] = kind
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["operation_verified"] is True
    del data["source_evidence"]["object_type"]
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["operation_verified"] is True
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), [
    ("github_pr_merged", "github", "pull_request", "merged"),
    ("stripe_invoice_paid", "stripe", "invoice", "paid"),
    ("stripe_refund_processor_succeeded", "stripe", "refund", "succeeded"),
    ("jira_issue_resolved", "jira", "issue", "done"),
])
@pytest.mark.parametrize("field,reason", [
    ("record_observed", "record_not_observed_in_permitted_window"),
    ("record_identity_complete_within_retained_window",
     "incomplete_or_conflicting_record_identity"),
])
@pytest.mark.parametrize("malformed", [None, False, 0, 1, "true", "false", [], {}])
def test_receipt_requires_literal_positive_record_evidence_flags(
    operation, provider, kind, state, field, reason, malformed,
):
    # A malformed upstream graph must not attest a source operation.
    # In particular 1 and "true" are truthy but are not verified booleans.
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data[field] = malformed
    calls = []
    result = source_operation_receipt(
        REF, operation, record_reader=reader(data, calls), now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == reason
    assert result["operation_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert len(calls) == 1
    assert calls[0][1]["include_source_status"] is True
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), [
    ("github_pr_merged", "github", "pull_request", "merged"),
    ("stripe_invoice_paid", "stripe", "invoice", "paid"),
    ("stripe_refund_processor_succeeded", "stripe", "refund", "succeeded"),
    ("jira_issue_resolved", "jira", "issue", "done"),
])
def test_literal_true_record_evidence_preserves_valid_provider_receipts(
    operation, provider, kind, state,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    assert data["record_observed"] is True
    assert data["record_identity_complete_within_retained_window"] is True
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["customer_outcome_verified"] is False
    assert result["agent_execution_authorized"] is False


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), [
    ("github_pr_merged", "github", "pull_request", "merged"),
    ("stripe_invoice_paid", "stripe", "invoice", "paid"),
    ("stripe_refund_processor_succeeded", "stripe", "refund", "succeeded"),
    ("jira_issue_resolved", "jira", "issue", "done"),
])
@pytest.mark.parametrize("malformed", [
    None, 0, 1, "false", "true", "", "0", -1, [], {}, True,
])
def test_receipt_requires_literal_false_history_truncation_flag(
    operation, provider, kind, state, malformed,
):
    # Even falsey 0, empty text, and null are not affirmative proof that
    # the exact record history was fully retained and checked for conflicts.
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_event_limit_reached"] = malformed
    calls = []
    result = source_operation_receipt(
        REF, operation, record_reader=reader(data, calls), now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "incomplete_or_conflicting_record_identity"
    assert result["operation_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert len(calls) == 1
    assert calls[0][1]["include_source_status"] is True
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), [
    ("github_pr_merged", "github", "pull_request", "merged"),
    ("stripe_invoice_paid", "stripe", "invoice", "paid"),
    ("stripe_refund_processor_succeeded", "stripe", "refund", "succeeded"),
    ("jira_issue_resolved", "jira", "issue", "done"),
])
def test_receipt_missing_history_truncation_flag_is_unknown(
    operation, provider, kind, state,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    del data["source_event_limit_reached"]
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "incomplete_or_conflicting_record_identity"
    assert result["operation_verified"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), [
    ("github_pr_merged", "github", "pull_request", "merged"),
    ("stripe_invoice_paid", "stripe", "invoice", "paid"),
    ("stripe_refund_processor_succeeded", "stripe", "refund", "succeeded"),
    ("jira_issue_resolved", "jira", "issue", "done"),
])
def test_literal_false_history_truncation_flag_preserves_valid_receipt(
    operation, provider, kind, state,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    assert data["source_event_limit_reached"] is False
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["customer_outcome_verified"] is False
    assert result["agent_execution_authorized"] is False


_OPERATION_FAMILIES = [
    ("github_pr_merged", "github", "pull_request", "merged"),
    ("stripe_invoice_paid", "stripe", "invoice", "paid"),
    ("stripe_refund_processor_succeeded", "stripe", "refund", "succeeded"),
    ("jira_issue_resolved", "jira", "issue", "done"),
]


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("wrong_ref", [
    "owg:r:" + "b" * 32, None, 7, {}, REF.upper(),
])
def test_receipt_rejects_wrong_record_reference_even_with_valid_provider_state(
    operation, provider, kind, state, wrong_ref,
):
    # Cached or injected evidence for a different exact record is never
    # proof of the requested record, even with matching provider/state.
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["resource_ref"] = wrong_ref
    calls = []
    result = source_operation_receipt(
        REF, operation, record_reader=reader(data, calls), now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "record_reference_scope_conflict"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["resource_ref"] == REF
    assert len(calls) == 1
    assert calls[0][1]["include_source_status"] is True
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)
    if isinstance(wrong_ref, str):
        assert wrong_ref not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("malformed", [None, False, 0, 1, "true", [], {}])
def test_receipt_requires_literal_true_exact_record_query_scope(
    operation, provider, kind, state, malformed,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_rows_scoped_to_exact_record"] = malformed
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "record_reference_scope_conflict"
    assert result["operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("missing", ["resource_ref", "source_rows_scoped_to_exact_record"])
def test_receipt_missing_exact_record_provenance_is_unknown(
    operation, provider, kind, state, missing,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    del data[missing]
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "record_reference_scope_conflict"
    assert result["operation_verified"] is False


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
def test_exact_record_provenance_preserves_valid_source_receipt(
    operation, provider, kind, state,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    assert data["resource_ref"] == REF
    assert data["source_rows_scoped_to_exact_record"] is True
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("wrong_window", [
    None, {}, [], "VERY_PRIVATE_CUSTOMER",
    {"since": "2026-01-01T00:00:00Z", "until": None},
    {"since": None, "until": "2026-10-19T10:00:00Z"},
    {"since": None, "until": None, "extra": "VERY_PRIVATE_CUSTOMER"},
])
def test_receipt_rejects_missing_or_different_returned_history_window(
    operation, provider, kind, state, wrong_window,
):
    # Cached wider/different history is not evidence for this selection.
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_window"] = wrong_window
    calls = []
    result = source_operation_receipt(
        REF, operation, record_reader=reader(data, calls), now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "source_window_scope_conflict"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["resource_ref"] == REF
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
    })]


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
def test_receipt_missing_history_window_is_unknown(
    operation, provider, kind, state,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    del data["source_window"]
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "source_window_scope_conflict"
    assert result["operation_verified"] is False


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("requested,returned", [
    (
        {"since": "2026-10-18T00:00:00Z", "until": None},
        {"since": None, "until": None},
    ),
    (
        {"since": None, "until": "2026-10-20T00:00:00Z"},
        {"since": None, "until": None},
    ),
    (
        {"since": "2026-10-18T00:00:00Z", "until": "2026-10-20T00:00:00Z"},
        {"since": "2026-10-18T00:00:00Z", "until": None},
    ),
    (
        {"since": "2026-10-18T00:00:00Z", "until": "2026-10-20T00:00:00Z"},
        {"since": "2026-10-20T00:00:00Z", "until": "2026-10-18T00:00:00Z"},
    ),
])
def test_receipt_rejects_cached_window_when_caller_requests_bounded_history(
    operation, provider, kind, state, requested, returned,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_window"] = returned
    calls = []
    result = source_operation_receipt(
        REF, operation, record_reader=reader(data, calls), now=WHEN, **requested,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "source_window_scope_conflict"
    assert result["operation_verified"] is False
    assert calls == [(REF, {**requested, "include_source_status": True})]
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("window", [
    {"since": None, "until": None},
    {"since": "2026-10-18T00:00:00Z", "until": None},
    {"since": None, "until": "2026-10-20T00:00:00Z"},
    {"since": "2026-10-18T00:00:00Z", "until": "2026-10-20T00:00:00Z"},
])
def test_receipt_accepts_exact_history_window_without_new_authority(
    operation, provider, kind, state, window,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_window"] = window
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN, **window,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)



@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("untrusted_method", [
    None, False, 0, 1, "", [], {},
    "untrusted_cached_export",
    SOURCE_READ_METHOD["github"],
    SOURCE_READ_METHOD["jira"],
    SOURCE_READ_METHOD["stripe"],
])
def test_receipt_requires_exact_authenticated_source_read_method(
    operation, provider, kind, state, untrusted_method,
):
    # A valid state, timestamp and provider label are insufficient without
    # provenance from the matching existing authenticated opt-in source path.
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_evidence"]["verification_method"] = untrusted_method
    calls = []
    result = source_operation_receipt(
        REF, operation, record_reader=reader(data, calls), now=WHEN,
    )
    if untrusted_method == SOURCE_READ_METHOD[provider]:
        assert result["availability"] == "source_reported"
        assert result["operation_verified"] is True
    else:
        assert result["availability"] == "unknown"
        assert result["reason"] == "source_verification_provenance_conflict"
        assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["read_only"] is True
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
    })]
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)
    assert "untrusted_cached_export" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
def test_receipt_missing_source_read_method_is_unknown(
    operation, provider, kind, state,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    del data["source_evidence"]["verification_method"]
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "source_verification_provenance_conflict"
    assert result["operation_verified"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
def test_matching_authenticated_source_read_method_keeps_provider_only_scope(
    operation, provider, kind, state,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    assert data["source_evidence"]["verification_method"] == SOURCE_READ_METHOD[provider]
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)



@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("untrusted_join", [None, False, 0, 1, "true", [], {}])
def test_receipt_rejects_unsupported_or_malformed_source_log_join(
    operation, provider, kind, state, untrusted_join,
):
    # A provider state cannot attest an operation for an unallowlisted
    # observed provider/kind/host, even when the other receipt fields match.
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_log_join_supported"] = untrusted_join
    calls = []
    result = source_operation_receipt(
        REF, operation, record_reader=reader(data, calls), now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "source_join_not_supported"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["read_only"] is True
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
    })]
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
def test_receipt_missing_source_log_join_is_unknown(
    operation, provider, kind, state,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    del data["source_log_join_supported"]
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "source_join_not_supported"
    assert result["operation_verified"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
def test_allowlisted_source_log_join_preserves_provider_only_receipt(
    operation, provider, kind, state,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    assert data["source_log_join_supported"] is True
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("failure", [
    OSError, TimeoutError, ConnectionError, httpx.ConnectError, httpx.ReadTimeout,
])
def test_source_reader_transport_outage_is_privacy_safe_unknown(
    operation, provider, kind, state, failure,
):
    # Provider failures can surface from the trusted graph reader itself,
    # not just from its existing adapters. Never turn a transport exception
    # into an operation success or leak the exception's native record details.
    calls = []
    def failing_reader(ref, **kwargs):
        calls.append((ref, kwargs))
        raise failure("PRIVATE_NATIVE_RECORD_AND_CUSTOMER")
    result = source_operation_receipt(
        REF, operation, record_reader=failing_reader, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "source_read_unavailable"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["read_only"] is True
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
    })]
    assert "PRIVATE_NATIVE_RECORD_AND_CUSTOMER" not in str(result)


@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_source_reader_programming_and_request_errors_are_not_masked(failure):
    # Invalid history windows and programming bugs are not provider outages.
    # Preserve their normal error handling instead of claiming an outage.
    calls = []
    def failing_reader(ref, **kwargs):
        calls.append((ref, kwargs))
        raise failure("invalid_request_or_programming_error")
    with pytest.raises(failure, match="invalid_request_or_programming_error"):
        source_operation_receipt(
            REF, "stripe_invoice_paid", record_reader=failing_reader, now=WHEN,
        )
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
    })]


@pytest.mark.parametrize("bad_status", [
    None, False, 0, "unverified", "RESOLVED", "done", {}, [],
])
def test_jira_verified_flag_cannot_override_contradictory_resolution_status(bad_status):
    data = response(provider="jira", kind="issue", state="done",
                    operation="jira_issue_resolved")
    data["source_evidence"]["verified_operational_outcome"]["status"] = bad_status
    result = source_operation_receipt(
        REF, "jira_issue_resolved", record_reader=lambda *_a, **_kw: data,
        now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "provider_resolution_detail_conflict"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize("bad_timestamp", [
    None, False, 0, [], {},
    "", "not-a-timestamp", "2026-10-19T10:00:00",
    "2026-10-19T10:05:01+00:00",
    "2026-10-19T12:05:01+02:00",
    "x" * 100,
])
def test_jira_resolution_timestamp_must_be_aware_bounded_and_consistent(bad_timestamp):
    data = response(provider="jira", kind="issue", state="done",
                    operation="jira_issue_resolved")
    data["source_evidence"]["verified_operational_outcome"]["source_timestamp"] = bad_timestamp
    calls = []
    result = source_operation_receipt(
        REF, "jira_issue_resolved", record_reader=reader(data, calls), now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "provider_resolution_detail_conflict"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
    })]
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


def test_jira_missing_resolution_detail_fields_are_unknown():
    for missing in ("status", "source_timestamp"):
        data = response(provider="jira", kind="issue", state="done",
                        operation="jira_issue_resolved")
        del data["source_evidence"]["verified_operational_outcome"][missing]
        result = source_operation_receipt(
            REF, "jira_issue_resolved", record_reader=lambda *_a, **_kw: data,
            now=WHEN,
        )
        assert result["availability"] == "unknown"
        assert result["reason"] == "provider_resolution_detail_conflict"
        assert result["operation_verified"] is False


@pytest.mark.parametrize("resolution_stamp", [
    "2026-10-19T10:00:00+00:00",
    "2026-10-19T10:05:00+00:00",
    "2026-10-19T12:00:00+02:00",
    "2026-10-19T10:00:00Z",
    "2026-10-18T09:00:00+00:00",
])
def test_jira_consistent_resolution_detail_keeps_provider_only_success(resolution_stamp):
    data = response(provider="jira", kind="issue", state="done",
                    operation="jira_issue_resolved")
    data["source_evidence"]["verified_operational_outcome"]["source_timestamp"] = resolution_stamp
    result = source_operation_receipt(
        REF, "jira_issue_resolved", record_reader=lambda *_a, **_kw: data,
        now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize("bad_scope", [
    None, False, 0, 1, "true", "", [], {},
    "stripe_processor_only",
    "jira_issue_only",
    "jira_issue_only_not_customer_acknowledgement_or_refund ",
    "jira_issue_only_not_customer_acknowledgement_or_refund\\nPRIVATE_NATIVE_ID",
])
def test_jira_verified_resolution_requires_exact_provider_defined_scope(bad_scope):
    data = response(provider="jira", kind="issue", state="done",
                    operation="jira_issue_resolved")
    data["source_evidence"]["verified_operational_outcome"]["scope"] = bad_scope
    calls = []
    result = source_operation_receipt(
        REF, "jira_issue_resolved", record_reader=reader(data, calls), now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "provider_receipt_scope_conflict"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
    })]
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)
    assert "PRIVATE_NATIVE_ID" not in str(result)


def test_jira_missing_provider_defined_resolution_scope_is_unknown():
    data = response(provider="jira", kind="issue", state="done",
                    operation="jira_issue_resolved")
    del data["source_evidence"]["verified_operational_outcome"]["scope"]
    result = source_operation_receipt(
        REF, "jira_issue_resolved", record_reader=lambda *_a, **_kw: data,
        now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "provider_receipt_scope_conflict"
    assert result["operation_verified"] is False
    assert result["customer_outcome_verified"] is False


def test_jira_exact_issue_only_scope_preserves_resolution_not_customer_success():
    data = response(provider="jira", kind="issue", state="done",
                    operation="jira_issue_resolved")
    assert data["source_evidence"]["verified_operational_outcome"]["scope"] == (
        "jira_issue_only_not_customer_acknowledgement_or_refund"
    )
    result = source_operation_receipt(
        REF, "jira_issue_resolved", record_reader=lambda *_a, **_kw: data,
        now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["verification_scope"] == "authenticated_source_provider_operational_state"
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("bad_stamp", [
    None, False, 0, 1, [], {}, WHEN,
    "2026-W43-1T10:00:00+00:00",  # permissive Python week date
    "20261019T100000+00:00",     # basic date/time
    "2026-10-19 10:00:00+00:00", # ambiguous separator
    "2026-10-19T10:00+00:00",    # missing seconds
    "2026-10-19T10:00:00+0000",  # missing offset colon
    "2026-10-19T10:00:00",       # naive timestamp
    "2026-10-19T10:00:00.1234567890Z",  # excessive precision
    pytest.param("x" * 100_000, id="oversized-untrusted-timestamp"),
])
def test_receipt_requires_bounded_explicit_source_read_timestamp(
    operation, provider, kind, state, bad_stamp,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_evidence"]["verified_at"] = bad_stamp
    calls = []
    result = source_operation_receipt(
        REF, operation, record_reader=reader(data, calls), now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "invalid_provider_receipt_timestamp"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["read_only"] is True
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
    })]
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)
    assert "x" * 100 not in str(result)


@pytest.mark.parametrize(("operation", "provider", "kind", "state"), _OPERATION_FAMILIES)
@pytest.mark.parametrize("good_stamp", [
    "2026-10-19T10:00:00Z",
    "2026-10-19T10:00:00+00:00",
    "2026-10-19T12:00:00+02:00",
    "2026-10-19T10:00:00.123456+00:00",
    "2026-10-19T10:00:00.123456789Z",
])
def test_receipt_accepts_explicit_source_time_without_business_authority(
    operation, provider, kind, state, good_stamp,
):
    data = response(provider=provider, kind=kind, state=state, operation=operation)
    data["source_evidence"]["verified_at"] = good_stamp
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize("bad_resolution_stamp", [
    WHEN, "2026-W43-1T10:00:00+00:00", "20261019T100000+00:00",
    "2026-10-19 10:00:00+00:00", "2026-10-19T10:00+00:00",
    "2026-10-19T10:00:00+0000", "2026-10-19T10:00:00.1234567890Z",
    pytest.param("x" * 100_000, id="oversized-untrusted-resolution"),
])
def test_jira_resolution_rejects_noncanonical_source_time(bad_resolution_stamp):
    data = response(provider="jira", kind="issue", state="done",
                    operation="jira_issue_resolved")
    data["source_evidence"]["verified_operational_outcome"]["source_timestamp"] = (
        bad_resolution_stamp
    )
    result = source_operation_receipt(
        REF, "jira_issue_resolved",
        record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "provider_resolution_detail_conflict"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "VERY_PRIVATE_CUSTOMER" not in str(result)


@pytest.mark.parametrize("resolution_stamp", [
    "2026-10-19T10:00:00Z",
    "2026-10-19T12:00:00+02:00",
    "2026-10-19T10:00:00.123456+00:00",
    "2026-10-19T10:00:00.123456789Z",
])
def test_jira_resolution_accepts_explicit_source_time_without_customer_claim(
    resolution_stamp,
):
    data = response(provider="jira", kind="issue", state="done",
                    operation="jira_issue_resolved")
    data["source_evidence"]["verified_operational_outcome"]["source_timestamp"] = (
        resolution_stamp
    )
    result = source_operation_receipt(
        REF, "jira_issue_resolved",
        record_reader=lambda *_a, **_kw: data, now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
