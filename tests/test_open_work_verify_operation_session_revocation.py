"""Security regression: an operator session revoked during provider I/O cannot receive evidence."""
import asyncio

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from server import open_work_declaration_routes as routes


def test_verify_operation_denies_session_revoked_during_provider_read(monkeypatch):
    session = {"valid": True}
    reads = []
    projections = []
    request = Request({
        "type": "http", "method": "POST",
        "path": "/v1/open-work/declarations/verify-operation",
        "headers": [(b"authorization", b"owg-session valid")],
    })
    payload = {
        "work_item_ref": "owg:w:case", "expected_revision": 1,
        "expected_edit_token": "token", "confirm_source_verification": True,
        "record_link": "reviewed-link",
    }

    monkeypatch.setattr(routes, "dashboard_session_valid",
                        lambda token: token == "valid" and session["valid"])
    async def scope():
        return "owg:scope:test"
    async def body(*args, **kwargs):
        return payload
    async def source_check(*args):
        return None
    monkeypatch.setattr(routes, "_require_scope", scope)
    monkeypatch.setattr(routes, "_body", body)
    monkeypatch.setattr(routes, "_recheck_source", source_check)
    monkeypatch.setattr(routes, "list_case_declarations", lambda scope: {"items": []})
    monkeypatch.setattr(routes, "admitted_case_dossier_snapshot",
                        lambda *args, **kwargs: ({"work_item_ref": "owg:w:case"}, "signature"))
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda name: "secret")
    monkeypatch.setattr(routes, "validate_case_record_link",
                        lambda *args, **kwargs: "stripe:invoice:test")

    def provider_read(*args):
        reads.append(True)
        session["valid"] = False
        return {"status": "succeeded"}
    monkeypatch.setattr(routes, "source_operation_receipt", provider_read)
    monkeypatch.setattr(routes, "project_case_operation_verification",
                        lambda *args: projections.append(True))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.verify_local_case_provider_operation(request, Response()))
    assert exc.value.status_code == 403
    assert reads == [True]
    assert projections == []


def test_verify_operation_denies_revoked_session_before_provider_read(monkeypatch):
    """A session invalidated while the scope check yields cannot access the provider."""
    session = {"valid": True}
    request = Request({
        "type": "http", "method": "POST",
        "path": "/v1/open-work/declarations/verify-operation",
        "headers": [(b"authorization", b"owg-session valid")],
    })
    monkeypatch.setattr(routes, "dashboard_session_valid",
                        lambda token: token == "valid" and session["valid"])
    async def scope():
        session["valid"] = False
        return "owg:scope:test"
    async def body(*args, **kwargs):
        return {
            "work_item_ref": "owg:w:case", "expected_revision": 1,
            "expected_edit_token": "token", "confirm_source_verification": True,
            "record_link": "reviewed-link",
        }
    async def source_check(*args):
        return None
    monkeypatch.setattr(routes, "_require_scope", scope)
    monkeypatch.setattr(routes, "_body", body)
    monkeypatch.setattr(routes, "_recheck_source", source_check)
    monkeypatch.setattr(routes, "list_case_declarations", lambda scope: {"items": []})
    monkeypatch.setattr(routes, "admitted_case_dossier_snapshot",
                        lambda *args, **kwargs: ({"work_item_ref": "owg:w:case"}, "signature"))
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda name: "secret")
    monkeypatch.setattr(routes, "validate_case_record_link",
                        lambda *args, **kwargs: "stripe:invoice:test")
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *args: pytest.fail("revoked session reached provider"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.verify_local_case_provider_operation(request, Response()))
    assert exc.value.status_code == 403
