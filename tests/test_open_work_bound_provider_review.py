"""Bound provider review: source I/O is gated by the actor's retained run admission."""
import asyncio
import json

import pytest
from fastapi import HTTPException, Request, Response

from server import open_work_declaration_routes as routes


def request():
    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/",
                    "headers": [(b"authorization", b"Bearer operator")]}, receive)


def payload():
    return {
        "work_item_ref": "case-1", "expected_revision": 1,
        "expected_edit_token": "token", "confirm_historical_review": True,
        "confirm_run_comparison": True, "confirm_source_verification": True,
        "max_cases": 1, "deployment_id": "deployment", "run_id": "run",
        "record_link": "signed-link",
    }


def install(monkeypatch, data, review, provider, scope_check=None):
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)

    async def admitted_scope():
        return "tenant-1"

    async def body(request, allowed, optional=None):
        assert (set(data) == allowed if optional is None else allowed <= set(data) <= allowed | optional)
        return data

    async def recheck(scope):
        assert scope == "tenant-1"
        if scope_check:
            scope_check()

    monkeypatch.setattr(routes, "_require_scope", admitted_scope)
    monkeypatch.setattr(routes, "_body", body)
    monkeypatch.setattr(routes, "_recheck_source", recheck)
    monkeypatch.setattr(routes, "read_local_combined_case_review", review)
    monkeypatch.setattr(routes, "verify_local_case_provider_operation", provider)


def test_provider_never_read_when_run_binding_admission_fails(monkeypatch):
    calls = []

    async def review(request, response):
        calls.append("review")
        raise HTTPException(403, "binding revoked")

    async def provider(request, response):
        calls.append("provider")
        return {}

    install(monkeypatch, payload(), review, provider)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert exc.value.status_code == 403
    assert calls == ["review"]


def test_provider_read_rechecks_review_and_keeps_customer_outcome_unknown(monkeypatch):
    calls = []
    historical = {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}

    async def review(request, response):
        calls.append("review")
        return historical

    async def provider(request, response):
        calls.append("provider")
        return {"provider_operation": "verified"}

    install(monkeypatch, payload(), review, provider)
    result = asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert calls == ["review", "provider", "review", "review"]
    assert result["run_ref"] == "run-1"
    assert result["customer_outcome"] == "unknown"
    assert result["customer_outcome_verified"] is False
    # A fresh invoice-state comparison is not an exact provider event
    # independently correlated to the retained agent attempt.
    assert result["provider_state_rechecked_on_this_read"] is True
    assert result["provider_event_independently_rechecked_on_this_read"] is False
    assert result["agent_execution_authorized"] is False


def test_provider_read_rejects_revoked_binding_after_io(monkeypatch):
    calls = []

    async def review(request, response):
        calls.append("review")
        if len(calls) > 1:
            raise HTTPException(403, "binding revoked")
        return {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}

    async def provider(request, response):
        calls.append("provider")
        return {"provider_operation": "verified"}

    install(monkeypatch, payload(), review, provider)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert exc.value.status_code == 403
    assert calls == ["review", "provider", "review"]


def test_no_provider_read_without_explicit_approval(monkeypatch):
    data = payload()
    data["confirm_source_verification"] = False
    calls = []

    async def review(request, response):
        calls.append("review")

    async def provider(request, response):
        calls.append("provider")

    install(monkeypatch, data, review, provider)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert exc.value.status_code == 403
    assert calls == []


def test_conflicting_review_after_provider_read_is_not_disclosed(monkeypatch):
    calls = []

    async def review(request, response):
        calls.append("review")
        return {"case_ref": "case-1", "receipts": {"run_ref": "run-1"},
                "snapshot": len(calls)}

    async def provider(request, response):
        calls.append("provider")
        return {"provider_operation": "verified"}

    install(monkeypatch, payload(), review, provider)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == ["review", "provider", "review"]


def test_source_revocation_after_provider_read_denies_disclosure(monkeypatch):
    calls = []
    revoked = [False]

    async def review(request, response):
        calls.append("review")
        return {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}

    async def provider(request, response):
        calls.append("provider")
        revoked[0] = True
        return {"provider_operation": "verified"}

    def source_guard():
        if revoked[0]:
            raise HTTPException(403, "source grant revoked")

    install(monkeypatch, payload(), review, provider, scope_check=source_guard)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert exc.value.status_code == 403
    assert calls == ["review", "provider", "review"]


def test_persistence_requires_separate_explicit_write_approval(monkeypatch):
    data = payload()
    data["persist_provider_check"] = True
    calls = []

    async def review(request, response):
        calls.append("review")
        return {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}

    async def provider(request, response):
        calls.append("provider")
        return {"provider_operation": "verified"}

    install(monkeypatch, data, review, provider)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert exc.value.status_code == 403
    assert calls == []


def test_persisted_case_evidence_is_not_independent_attempt_receipt(monkeypatch):
    data = payload()
    data.update(persist_provider_check=True, confirm_provider_evidence_write=True)
    calls = []
    historical = {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}

    async def review(request, response):
        calls.append("review")
        return historical

    async def provider(request, response):
        raise AssertionError("unbound provider path must not run on persistence")

    async def bound(request, response):
        calls.append("bound")
        return {"provider_check_persisted": True, "provider_operation": "verified"}

    install(monkeypatch, data, review, provider)
    monkeypatch.setattr(routes, "verify_local_bound_run_source", bound)
    result = asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert calls == ["review", "bound", "review", "review"]
    assert result["provider_check_persisted"] is True
    assert result["persisted_evidence_scope"] == "case_provider_ledger"
    assert result["persisted_evidence_is_independent_attempt_receipt"] is False
    assert result["customer_outcome"] == "unknown"
    assert result["agent_execution_authorized"] is False


def test_failed_guarded_persistence_cannot_report_success(monkeypatch):
    data = payload()
    data.update(persist_provider_check=True, confirm_provider_evidence_write=True)
    calls = []

    async def review(request, response):
        calls.append("review")
        return {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}

    async def provider(request, response):
        raise AssertionError("unbound provider read")

    async def bound(request, response):
        calls.append("bound")
        return {"provider_check_persisted": False}

    install(monkeypatch, data, review, provider)
    monkeypatch.setattr(routes, "verify_local_bound_run_source", bound)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == ["review", "bound"]


def test_direct_bound_run_source_persistence_requires_separate_approval(monkeypatch):
    """The older public route must not bypass the combined route's write consent."""
    data = payload()
    data["persist_provider_check"] = True
    calls = []

    def dashboard(request):
        calls.append("auth")

    async def scope():
        calls.append("scope")
        return "tenant-1"

    async def body(request, required, optional=None):
        calls.append("body")
        assert "confirm_provider_evidence_write" in optional
        return data

    monkeypatch.setattr(routes, "_dashboard_only", dashboard)
    monkeypatch.setattr(routes, "_require_scope", scope)
    monkeypatch.setattr(routes, "_body", body)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.verify_local_bound_run_source(request(), Response()))
    assert exc.value.status_code == 403
    assert calls == ["auth", "scope", "body"]


def test_bound_provider_persistence_forwards_write_approval(monkeypatch):
    data = payload()
    data.update(persist_provider_check=True, confirm_provider_evidence_write=True)
    historical = {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}
    calls = []

    async def review(request, response):
        return historical

    async def provider(request, response):
        raise AssertionError("unbound verification must not run")

    async def bound(request, response):
        raw = await request.body()
        fields = json.loads(raw)
        assert fields["persist_provider_check"] is True
        assert fields["confirm_provider_evidence_write"] is True
        calls.append("bound")
        return {"provider_check_persisted": True}

    install(monkeypatch, data, review, provider)
    monkeypatch.setattr(routes, "verify_local_bound_run_source", bound)
    result = asyncio.run(routes.read_local_bound_provider_review(request(), Response()))
    assert result["provider_check_persisted"] is True
    assert calls == ["bound"]
