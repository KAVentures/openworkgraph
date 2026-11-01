"""Fail closed on contradictory provider-defined operation-scope disclaimers.

These are synthetic, read-only contract tests, not provider/tenant validation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.open_work_outcomes import source_operation_receipt
from server.workflow_evidence import WorkflowEvidenceError

REF = "owg:r:" + "a" * 32
WHEN = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

OPERATIONS = (
    ("github_pr_merged", "github", "pull_request", "merged",
     "not_a_claim_of_business_success"),
    ("jira_issue_resolved", "jira", "issue", "done",
     "not_a_claim_of_business_success"),
    ("stripe_invoice_paid", "stripe", "invoice", "paid",
     "not_a_claim_of_customer_bank_settlement"),
    ("stripe_refund_processor_succeeded", "stripe", "refund", "succeeded",
     "not_a_claim_of_customer_bank_settlement"),
)

METHODS = {
    "github": "explicit_opt_in_authenticated_local_github_cli_read",
    "jira": "explicit_opt_in_site_verified_oauth_jira_issue_status_read",
    "stripe": "opted_in_restricted_key_stripe_account_and_record_get",
}


def _graph(operation, provider, kind, state):
    source = {
        "provider": provider,
        "availability": "source_reported",
        "source_state_reported": True,
        "source_state": state,
        "verified_at": WHEN.isoformat(),
        "verification_method": METHODS[provider],
        "private_customer": "PRIVATE_NEVER_RETURN",
    }
    if provider == "stripe":
        source["verified_operational_outcome"] = {
            "kind": operation,
            "verified": True,
            "scope": "stripe_processor_only",
        }
    if provider == "jira":
        source["verified_operational_outcome"] = {
            "kind": operation,
            "verified": True,
            "scope": "jira_issue_only_not_customer_acknowledgement_or_refund",
            "status": "resolved",
            "source_timestamp": WHEN.isoformat(),
        }
    return {
        "resource_ref": REF,
        "source_rows_scoped_to_exact_record": True,
        "source_window": {"since": None, "until": None},
        "source_log_join_supported": True,
        "record_observed": True,
        "source_event_limit_reached": False,
        "record_identity_complete_within_retained_window": True,
        "record_type": {"provider": provider, "kind": kind},
        "source_evidence": source,
    }


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize("contradiction", [False, None, 0, 1, "true", [], {}])
def test_explicit_provider_scope_disclaimer_conflict_returns_unknown(
    operation, provider, kind, state, scope_flag, contradiction,
):
    graph = _graph(operation, provider, kind, state)
    graph["source_evidence"][scope_flag] = contradiction
    calls = []

    def read(ref, **kwargs):
        calls.append((ref, kwargs))
        return graph

    receipt = source_operation_receipt(
        REF, operation, record_reader=read, now=WHEN,
    )
    assert receipt["availability"] == "unknown"
    assert receipt["reason"] == "provider_receipt_scope_conflict"
    assert receipt["operation_verified"] is False
    assert receipt["business_objective_verified"] is False
    assert receipt["customer_outcome_verified"] is False
    assert receipt["agent_caused_operation_verified"] is False
    assert receipt["agent_execution_authorized"] is False
    assert receipt["read_only"] is True
    assert receipt["resource_ref"] == REF
    assert "PRIVATE_NEVER_RETURN" not in str(receipt)
    assert len(calls) == 1
    assert calls[0][0] == REF
    assert calls[0][1]["include_source_status"] is True
    assert calls[0][1]["required_source_provider_kind"] == (provider, kind)


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize("scope_present", [False, True])
def test_provider_scope_disclaimer_absent_or_explicit_true_stays_provider_only(
    operation, provider, kind, state, scope_flag, scope_present,
):
    graph = _graph(operation, provider, kind, state)
    if scope_present:
        graph["source_evidence"][scope_flag] = True

    receipt = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: graph, now=WHEN,
    )
    assert receipt["availability"] == "source_reported"
    assert receipt["operation_verified"] is True
    assert receipt["verification_scope"] == (
        "authenticated_source_provider_operational_state"
    )
    assert receipt["business_objective_verified"] is False
    assert receipt["customer_outcome_verified"] is False
    assert receipt["agent_caused_operation_verified"] is False
    assert receipt["agent_execution_authorized"] is False
    assert receipt["read_only"] is True
    assert "PRIVATE_NEVER_RETURN" not in str(receipt)


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize(
    "invalid_age",
    [True, False, 1.5, 3600.5, None, "900", [], {}, float("nan"),
     float("inf"), 0, -1, 3601],
    ids=["true", "false", "fraction", "fraction-over-max", "none",
         "string", "list", "dict", "nan", "infinity", "zero",
         "negative", "above-max"],
)
def test_invalid_source_age_policy_rejected_before_provider_read(
    operation, provider, kind, state, scope_flag, invalid_age,
):
    # Invalid age policies must not cause a source refresh, even if an
    # injected reader would otherwise return a matching provider receipt.
    calls = []

    def unexpected_read(*args, **kwargs):
        calls.append((args, kwargs))
        return _graph(operation, provider, kind, state)

    with pytest.raises(ValueError, match="invalid maximum source age"):
        source_operation_receipt(
            REF, operation, max_age_seconds=invalid_age,
            record_reader=unexpected_read, now=WHEN,
        )
    assert calls == []


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize(
    "limit,age,verified",
    [(1, 1, True), (1, 2, False),
     (900, 900, True), (900, 901, False),
     (3600, 3600, True), (3600, 3601, False)],
)
def test_exact_integer_age_boundary_preserves_provider_only_result(
    operation, provider, kind, state, scope_flag, limit, age, verified,
):
    graph = _graph(operation, provider, kind, state)
    source = graph["source_evidence"]
    stamp = (WHEN - timedelta(seconds=age)).isoformat()
    source["verified_at"] = stamp
    if provider == "jira":
        # Keep independent Jira resolution evidence consistent with the
        # authenticated source read; age alone is under test here.
        source["verified_operational_outcome"]["source_timestamp"] = stamp
    calls = []

    def read(ref, **kwargs):
        calls.append((ref, kwargs))
        return graph

    receipt = source_operation_receipt(
        REF, operation, max_age_seconds=limit, record_reader=read, now=WHEN,
    )
    assert len(calls) == 1
    assert receipt["operation_verified"] is verified
    assert receipt["availability"] == ("source_reported" if verified else "unknown")
    if not verified:
        assert receipt["reason"] == "source_receipt_stale_or_clock_conflict"
    assert receipt["business_objective_verified"] is False
    assert receipt["customer_outcome_verified"] is False
    assert receipt["agent_caused_operation_verified"] is False
    assert receipt["agent_execution_authorized"] is False
    assert receipt["read_only"] is True
    assert "PRIVATE_NEVER_RETURN" not in str(receipt)


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize(
    "window",
    [
        {"since": "not-an-iso-timestamp"},
        {"until": "not-an-iso-timestamp"},
        {"since": "2026-11-01T09:00:00+00:00",
         "until": "2026-11-01T08:00:00+00:00"},
        {"since": "2026-11-01T08:00:00+00:00",
         "until": "2026-11-01T08:00:00+00:00"},
    ],
    ids=["bad-since", "bad-until", "reversed", "equal"],
)
def test_invalid_history_window_rejected_before_any_source_reader(
    operation, provider, kind, state, scope_flag, window,
):
    from server.workflow_evidence import WorkflowEvidenceError

    calls = []

    def unexpected_read(*args, **kwargs):
        calls.append((args, kwargs))
        return _graph(operation, provider, kind, state)

    with pytest.raises(WorkflowEvidenceError):
        source_operation_receipt(
            REF, operation, record_reader=unexpected_read, now=WHEN, **window,
        )
    assert calls == []


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize(
    "invalid_clock",
    [
        datetime(2026, 11, 1, 8, 0),
        "2026-11-01T08:00:00Z",
        0,
        False,
    ],
    ids=["naive-datetime", "timestamp-string", "numeric", "boolean"],
)
def test_invalid_evaluation_clock_rejected_before_any_source_reader(
    operation, provider, kind, state, scope_flag, invalid_clock,
):
    calls = []

    def unexpected_read(*args, **kwargs):
        calls.append((args, kwargs))
        return _graph(operation, provider, kind, state)

    with pytest.raises(ValueError, match="timezone-aware datetime"):
        source_operation_receipt(
            REF, operation, record_reader=unexpected_read, now=invalid_clock,
        )
    assert calls == []


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize(
    "window",
    [
        {"since": "2026-11-01T07:00:00+00:00",
         "until": "2026-11-01T09:00:00+00:00"},
        {"since": None, "until": None},
    ],
    ids=["bounded", "unbounded-default"],
)
def test_valid_history_window_and_offset_clock_preserve_provider_only_receipt(
    operation, provider, kind, state, scope_flag, window,
):
    graph = _graph(operation, provider, kind, state)
    graph["source_window"] = dict(window)
    calls = []

    def read(ref, **kwargs):
        calls.append((ref, kwargs))
        return graph

    offset_clock = WHEN.astimezone(timezone(timedelta(hours=2)))
    receipt = source_operation_receipt(
        REF, operation, record_reader=read, now=offset_clock, **window,
    )
    assert len(calls) == 1
    assert calls[0][0] == REF
    assert calls[0][1]["since"] == window["since"]
    assert calls[0][1]["until"] == window["until"]
    assert calls[0][1]["required_source_provider_kind"] == (provider, kind)
    assert receipt["availability"] == "source_reported"
    assert receipt["operation_verified"] is True
    assert receipt["business_objective_verified"] is False
    assert receipt["customer_outcome_verified"] is False
    assert receipt["agent_caused_operation_verified"] is False
    assert receipt["agent_execution_authorized"] is False
    assert receipt["read_only"] is True
    assert "PRIVATE_NEVER_RETURN" not in str(receipt)


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize("window_name", ["since", "until"])
@pytest.mark.parametrize(
    "invalid_explicit_scope",
    [
        False, 0, 0.0, "", [], {}, (), b"2026-11-01T08:00:00+00:00",
        datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc),
    ],
    ids=[
        "false", "integer-zero", "float-zero", "empty-string", "empty-list",
        "empty-dict", "empty-tuple", "bytes-timestamp", "datetime-object",
    ],
)
def test_explicit_falsey_or_nonstr_history_scope_never_invokes_source_reader(
    operation, provider, kind, state, scope_flag, window_name,
    invalid_explicit_scope,
):
    # Only None represents an omitted history bound. Previously, falsey
    # caller values bypassed the truthiness-based _ts check, and even a
    # datetime object could be implicitly converted into a string by _ts.
    # Neither is safe authorization for an alternate source-capable reader.
    from server.workflow_evidence import WorkflowEvidenceError

    calls = []

    def unexpected_read(*args, **kwargs):
        calls.append((args, kwargs))
        return _graph(operation, provider, kind, state)

    with pytest.raises(WorkflowEvidenceError, match="ISO-8601 timestamp"):
        source_operation_receipt(
            REF, operation, record_reader=unexpected_read, now=WHEN,
            **{window_name: invalid_explicit_scope},
        )
    assert calls == []


# The general historical-evidence parser intentionally supports legacy date
# forms. Operation verification is narrower: a timezone-ambiguous caller
# window must never reach a source-capable reader on a different host.
_AMBIGUOUS_WINDOWS = (
    "2026-11-01",                         # date only
    "2026-W44-7T08:00:00Z",              # ISO week date
    "20261101T080000Z",                 # ISO basic datetime
    "2026-11-01 08:00:00Z",             # space separator
    "2026-11-01T08:00:00",              # naive seconds
    "2026-11-01T08:00",                 # naive minutes
    "2026-11-01T08:00:00+0000",         # offset without colon
    "2026-11-01T08:00:00z",             # noncanonical lowercase Z
    "2026-11-01T08:00:00+24:00",        # impossible offset
    "2026-11-01T08:00:00+00:00\n",      # trailing data
    "2026-11-01T08:00:00+00:00+00:00",  # doubled offset
    "2026-11-01T08:00:00.1234567890Z", # unbounded precision
    "2026-11-01T08:00:00+00",          # incomplete offset
    "2026-11-01T08:00:00Z" + "x" * 100, # oversized input
)


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize("boundary", ["since", "until"])
@pytest.mark.parametrize("ambiguous", _AMBIGUOUS_WINDOWS)
def test_ambiguous_history_window_never_reaches_source_reader(
    operation, provider, kind, state, scope_flag, boundary, ambiguous,
):
    calls = []

    def unexpected_read(*args, **kwargs):
        calls.append((args, kwargs))
        return _graph(operation, provider, kind, state)

    with pytest.raises(WorkflowEvidenceError, match="timezone-aware ISO-8601"):
        source_operation_receipt(
            REF, operation, record_reader=unexpected_read, now=WHEN,
            **{boundary: ambiguous},
        )
    assert calls == []


_AWARE_WINDOWS = (
    ("2026-11-01T07:00Z", "2026-11-01T08:01Z"),
    ("2026-11-01T08:00+00:00", "2026-11-01T08:01+00:00"),
    ("2026-11-01T08:00:00+00:00", "2026-11-01T08:01:00+00:00"),
    ("2026-11-01T09:00:00+01:00", "2026-11-01T09:01:00+01:00"),
    ("2026-11-01T07:30:00.123456789Z", "2026-11-01T08:30:00Z"),
)


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize("since,until", _AWARE_WINDOWS)
def test_explicit_aware_history_windows_preserve_provider_only_receipts(
    operation, provider, kind, state, scope_flag, since, until,
):
    graph = _graph(operation, provider, kind, state)
    graph["source_window"] = {"since": since, "until": until}
    calls = []

    def read(ref, **kwargs):
        calls.append((ref, kwargs))
        return graph

    receipt = source_operation_receipt(
        REF, operation, since=since, until=until, record_reader=read, now=WHEN,
    )
    assert receipt["availability"] == "source_reported"
    assert receipt["operation_verified"] is True
    assert receipt["business_objective_verified"] is False
    assert receipt["customer_outcome_verified"] is False
    assert receipt["agent_caused_operation_verified"] is False
    assert receipt["agent_execution_authorized"] is False
    assert receipt["read_only"] is True
    assert "PRIVATE_NEVER_RETURN" not in str(receipt)
    assert calls == [(
        REF, {
            "since": since, "until": until, "include_source_status": True,
            "required_source_provider_kind": (provider, kind),
        },
    )]


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize(
    "malformed_legacy",
    [None, 0, 1, "true", "false", [], {}],
    ids=["null", "zero", "one", "string-true", "string-false",
         "array", "object"],
)
def test_explicit_malformed_legacy_business_outcome_is_not_source_proof(
    operation, provider, kind, state, scope_flag, malformed_legacy,
):
    # Stripe's historical flag means a *processor* operation, not customer
    # settlement. GitHub/Jira keep it false. No non-Boolean flag can
    # override either provider's narrower authenticated source receipt.
    graph = _graph(operation, provider, kind, state)
    graph["source_evidence"]["verified_business_outcome"] = malformed_legacy
    calls = []

    def read(ref, **kwargs):
        calls.append((ref, kwargs))
        return graph

    result = source_operation_receipt(
        REF, operation, record_reader=read, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "provider_receipt_scope_conflict"
    assert result["operation_verified"] is False
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["read_only"] is True
    assert "PRIVATE_NEVER_RETURN" not in str(result)
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
        "required_source_provider_kind": (provider, kind),
    })]


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
def test_explicit_opposite_legacy_business_outcome_is_conflicted(
    operation, provider, kind, state, scope_flag,
):
    graph = _graph(operation, provider, kind, state)
    graph["source_evidence"]["verified_business_outcome"] = provider != "stripe"
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: graph, now=WHEN,
    )
    assert result["availability"] == "unknown"
    assert result["reason"] == "provider_receipt_scope_conflict"
    assert result["operation_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert "PRIVATE_NEVER_RETURN" not in str(result)


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize("legacy_present", [False, True])
def test_matching_or_absent_legacy_business_outcome_remains_provider_only(
    operation, provider, kind, state, scope_flag, legacy_present,
):
    graph = _graph(operation, provider, kind, state)
    if legacy_present:
        graph["source_evidence"]["verified_business_outcome"] = provider == "stripe"
    result = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: graph, now=WHEN,
    )
    assert result["availability"] == "source_reported"
    assert result["operation_verified"] is True
    assert result["verification_scope"] == (
        "authenticated_source_provider_operational_state"
    )
    assert result["business_objective_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["read_only"] is True
    assert "PRIVATE_NEVER_RETURN" not in str(result)


# The provider operation receipt is never authority for business completion,
# customer acknowledgement, agent causality or permission to execute. Optional
# injected/cached assertions at either nesting level must not contradict that
# contract even when the provider's operational state is otherwise valid.
_AUTHORITY_FIELDS = (
    "business_objective_verified",
    "customer_outcome_verified",
    "agent_caused_operation_verified",
    "agent_execution_authorized",
)


def _with_optional_operation_detail(graph, operation, provider):
    # The current GitHub adapter omits a nested operation detail; an injected
    # cache may supply a valid one. The other provider fixtures already have
    # their production-shaped details.
    if provider == "github":
        graph["source_evidence"]["verified_operational_outcome"] = {
            "kind": operation, "verified": True,
        }
    return graph


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize("level", ["source", "detail"])
@pytest.mark.parametrize("field", _AUTHORITY_FIELDS)
@pytest.mark.parametrize(
    "contradiction", [True, 1, "false", None, {}],
    ids=["true", "numeric-one", "string-false", "null", "object"],
)
def test_explicit_customer_business_or_agent_authority_conflict_fails_closed(
    operation, provider, kind, state, scope_flag, level, field, contradiction,
):
    graph = _with_optional_operation_detail(
        _graph(operation, provider, kind, state), operation, provider,
    )
    source = graph["source_evidence"]
    target = source if level == "source" else source["verified_operational_outcome"]
    target[field] = contradiction
    calls = []

    def read(ref, **kwargs):
        calls.append((ref, kwargs))
        return graph

    receipt = source_operation_receipt(
        REF, operation, record_reader=read, now=WHEN,
    )
    assert receipt["availability"] == "unknown"
    assert receipt["reason"] == "provider_receipt_scope_conflict"
    assert receipt["operation_verified"] is False
    for key in _AUTHORITY_FIELDS:
        assert receipt[key] is False
    assert receipt["read_only"] is True
    assert "PRIVATE_NEVER_RETURN" not in str(receipt)
    assert calls == [(REF, {
        "since": None, "until": None, "include_source_status": True,
        "required_source_provider_kind": (provider, kind),
    })]


@pytest.mark.parametrize("operation,provider,kind,state,scope_flag", OPERATIONS)
@pytest.mark.parametrize("level", ["source", "detail"])
@pytest.mark.parametrize("explicit_false", [False, True])
def test_absent_or_literal_false_authority_assertions_preserve_provider_only(
    operation, provider, kind, state, scope_flag, level, explicit_false,
):
    graph = _with_optional_operation_detail(
        _graph(operation, provider, kind, state), operation, provider,
    )
    source = graph["source_evidence"]
    target = source if level == "source" else source["verified_operational_outcome"]
    if explicit_false:
        target.update({key: False for key in _AUTHORITY_FIELDS})
    receipt = source_operation_receipt(
        REF, operation, record_reader=lambda *_a, **_kw: graph, now=WHEN,
    )
    assert receipt["availability"] == "source_reported"
    assert receipt["operation_verified"] is True
    assert receipt["verification_scope"] == (
        "authenticated_source_provider_operational_state"
    )
    for key in _AUTHORITY_FIELDS:
        assert receipt[key] is False
    assert receipt["read_only"] is True
    assert "PRIVATE_NEVER_RETURN" not in str(receipt)
