"""A source disconnect during issuance cannot disclose a newly minted grant."""
import asyncio
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server import open_work_handoff_routes as routes
from server import open_work_declaration_routes


def _request():
    payload = json.dumps({
        "work_item_ref": "owg:w:" + "a" * 32,
        "agent": "test-agent", "hours": 1,
        "confirm_grant": True, "confirm_case_context": True,
    }).encode()
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({
        "type": "http", "method": "POST",
        "path": "/v1/open-work/dispatch/grants",
        "headers": [
            (b"authorization", b"owg-session test"),
            (b"content-type", b"application/json"),
        ],
    }, receive=receive)


def test_source_replacement_during_grant_issue_revokes_without_disclosing_token(monkeypatch):
    scope = "owg:scope:" + "a" * 32
    seen = []
    scopes = iter([scope, "owg:scope:" + "b" * 32])
    monkeypatch.setattr(routes, "_require_dashboard", lambda _request: None)
    monkeypatch.setattr(open_work_declaration_routes, "_source_scope", lambda: next(scopes))
    monkeypatch.setattr(routes.grants, "issue", lambda *a, **kw: {
        "grant_id": "c" * 32, "token": "secret-must-not-be-returned",
    })
    monkeypatch.setattr(routes.grants, "revoke", lambda grant_id: seen.append(grant_id))

    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.owner_issue_grant(_request()))
    assert error.value.status_code == 409
    assert "secret-must-not-be-returned" not in str(error.value)
    assert seen == ["c" * 32]


def test_unchanged_source_returns_issued_grant(monkeypatch):
    scope = "owg:scope:" + "a" * 32
    monkeypatch.setattr(routes, "_require_dashboard", lambda _request: None)
    monkeypatch.setattr(open_work_declaration_routes, "_source_scope", lambda: scope)
    monkeypatch.setattr(routes.grants, "issue", lambda *a, **kw: {
        "grant_id": "c" * 32, "token": "test-token",
    })
    result = asyncio.run(routes.owner_issue_grant(_request()))
    assert result["token"] == "test-token"
