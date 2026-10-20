"""Independent provider operation receipts: never agent-self-reported success."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from server.open_work_outcomes import source_operation_receipt

REF = "owg:r:" + "a" * 32
WHEN = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)


def response(provider="stripe", kind="invoice", state="paid",
             operation="stripe_invoice_paid", verified=True):
    return {
        "record_observed": True,
        "record_type": {"provider": provider, "kind": kind},
        "record_identity_complete_within_retained_window": True,
        "source_event_limit_reached": False,
        "source_evidence": {
            "provider": provider, "availability": "source_reported",
            "source_state_reported": True, "source_state": state,
            "verified_at": WHEN.isoformat(),
            "verified_operational_outcome": {
                "kind": operation, "scope": "stripe_processor_only",
                "verified": verified,
            },
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
    data["source_evidence"]["verified_operational_outcome"].pop("scope")
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
