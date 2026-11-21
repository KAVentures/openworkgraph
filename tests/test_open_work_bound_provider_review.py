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
