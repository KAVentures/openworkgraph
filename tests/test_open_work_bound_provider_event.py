"""Exact event reads require current authenticated run admission and fail closed."""
import asyncio
import pytest
from fastapi import HTTPException, Request, Response
from server import open_work_declaration_routes as routes

DATA = {
    "work_item_ref": "case-1", "expected_revision": 1,
    "expected_edit_token": "token", "confirm_historical_review": True,
    "confirm_run_comparison": True, "confirm_source_verification": True,
    "max_cases": 1, "deployment_id": "dep", "run_id": "run",
    "provider_object_id": "in_123456789", "provider_operation": "invoice.finalized",
    "provider_correlation_id": "run-123", "provider_event_id": "evt_12345678",
}

def request():
    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/",
                    "headers": [(b"authorization", b"owg-session test")]}, receive)

def install(monkeypatch, *, review=None, source=None, body=None):
    monkeypatch.setattr(routes, "_dashboard_only", lambda req: None)
    async def scope():
        return "tenant-1"
    async def parse(req, required, optional=None):
        return dict(DATA if body is None else body)
    async def recheck(s):
        assert s == "tenant-1"
        if source:
            source()
    async def admitted(req, resp):
        if review:
            return await review(req, resp)
        return {"case_ref": "case-1", "receipts": {"run_ref": "run-1"}}
    monkeypatch.setattr(routes, "_require_scope", scope)
    monkeypatch.setattr(routes, "_body", parse)
    monkeypatch.setattr(routes, "_recheck_source", recheck)
    monkeypatch.setattr(routes, "read_local_combined_case_review", admitted)

def test_no_provider_read_without_retained_run(monkeypatch):
    async def denied(req, resp):
        raise HTTPException(403, "revoked binding")
    install(monkeypatch, review=denied)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403

def test_no_provider_read_without_explicit_confirmation(monkeypatch):
    install(monkeypatch, body={**DATA, "confirm_source_verification": False})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403

def test_provider_read_revocation_never_discloses(monkeypatch):
    calls = []
    def source():
        calls.append("scope")
        if len(calls) >= 2:
            raise HTTPException(409, "revoked")
    install(monkeypatch, source=source)
    # Admission succeeds, but source consent disappears before the reader.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409

def test_source_account_unavailable_fails_closed(monkeypatch):
    install(monkeypatch)
    from server import local_source_credentials
    monkeypatch.setattr(local_source_credentials, "_read_metadata", lambda path: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 403


def test_provider_account_replacement_during_read_denies_verdict(monkeypatch):
    """A source switch during provider I/O must not disclose old-account evidence."""
    install(monkeypatch)
    from server import local_source_credentials
    from server import open_work_provider_attempt_verification as verifier
    accounts = iter(["acct_12345678", "acct_87654321"])
    monkeypatch.setattr(local_source_credentials, "_read_metadata",
                        lambda path: {"account": next(accounts)})
    monkeypatch.setattr(verifier, "verify_provider_attempt",
                        lambda **kwargs: {"status": "verified_provider_event"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_bound_provider_event(request(), Response()))
    assert exc.value.status_code == 409
