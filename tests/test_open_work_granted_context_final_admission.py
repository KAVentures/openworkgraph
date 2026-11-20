"""Agent context must fail closed across grant and review changes."""
import asyncio

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request
from server import open_work_handoff_routes as routes
from server import open_work_declaration_routes as declarations
from server import open_work_case_declarations as store

REF = "owg:w:" + "a" * 32
SCOPE = "owg:scope:" + "c" * 32
IDENTITY = {"grant_id": "d" * 32, "work_item_ref": REF, "agent": "agent-a",
            "source_scope": SCOPE, "context_approved": True}

def request():
    return Request({"type": "http", "method": "GET",
                    "path": "/v1/open-work/dispatch/granted/case-context",
                    "headers": [(b"authorization", b"Bearer private-test-token")]})

def setup(monkeypatch, snapshot):
    monkeypatch.setattr(routes, "_grant", lambda req: IDENTITY)
    monkeypatch.setattr(declarations, "_source_scope", lambda: SCOPE)
    monkeypatch.setattr(store, "list_case_declarations", lambda scope: snapshot)
    monkeypatch.setattr(routes.grants, "authenticate", lambda *_: IDENTITY)

def test_only_allowlisted_case_fields_reach_agent(monkeypatch):
    setup(monkeypatch, {"items": [{
        "work_item_ref": REF, "goal_code": "verify_operation",
        "depends_on_work_item_refs": [], "edit_token": "owner-secret",
        "scope_ref": SCOPE, "future_private_field": "private",
    }]})
    result = asyncio.run(routes.granted_reviewed_case_context(request(), Response()))
    assert result["case"] == {"work_item_ref": REF, "goal_code": "verify_operation",
                              "depends_on_work_item_refs": []}
    assert result["customer_outcome_verified"] is False
    assert result["agent_execution_authorized"] is False

def test_grant_replacement_during_read_is_denied(monkeypatch):
    setup(monkeypatch, {"items": [{"work_item_ref": REF, "depends_on_work_item_refs": []}]})
    calls = 0
    def replaced(*_):
        nonlocal calls
        calls += 1
        return IDENTITY if calls == 1 else {**IDENTITY, "grant_id": "e" * 32}
    monkeypatch.setattr(routes.grants, "authenticate", replaced)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.granted_reviewed_case_context(request(), Response()))
    assert exc.value.status_code == 409

def test_final_projection_rejects_review_withdrawal(monkeypatch):
    snapshot = {"items": [{"work_item_ref": REF, "depends_on_work_item_refs": []}]}
    setup(monkeypatch, snapshot)
    calls = 0
    def changed(scope):
        nonlocal calls
        calls += 1
        return snapshot if calls < 3 else {"items": []}
    monkeypatch.setattr(store, "list_case_declarations", changed)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.granted_reviewed_case_context(request(), Response()))
    assert exc.value.status_code == 409
    assert calls == 3

def test_source_revocation_at_final_projection_denied(monkeypatch):
    setup(monkeypatch, {"items": [{"work_item_ref": REF, "depends_on_work_item_refs": []}]})
    calls = 0
    def scope():
        nonlocal calls
        calls += 1
        return SCOPE if calls < 4 else None
    monkeypatch.setattr(declarations, "_source_scope", scope)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.granted_reviewed_case_context(request(), Response()))
    assert exc.value.status_code == 409
