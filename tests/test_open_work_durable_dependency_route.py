"""HTTP boundary regression for durable reviewed dependency route."""
import asyncio

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from server import open_work_declaration_routes as routes


def _request(token=None, query=""):
    headers = [] if token is None else [(b"authorization", ("owg-session " + token).encode())]
    return Request({"type": "http", "method": "GET", "path": "/v1/open-work/declarations/durable-dependencies",
                    "query_string": query.encode(), "headers": headers})


def test_durable_route_rejects_unauthenticated_before_any_scope_read(monkeypatch):
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: False)
    monkeypatch.setattr(routes, "_source_scope", lambda: pytest.fail("unauthorized scope read"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_local_durable_reviewed_dependencies(_request(), Response()))
    assert exc.value.status_code == 403


def test_durable_route_rejects_unconnected_source(monkeypatch):
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    monkeypatch.setattr(routes, "_source_scope", lambda: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_local_durable_reviewed_dependencies(_request("valid"), Response()))
    assert exc.value.status_code == 403


def test_durable_route_reconciles_reviewed_edges_and_never_grants_execution(monkeypatch, tmp_path):
    from server import local_auth
    from server import open_work_dependency_store as store
    scope = "owg:scope:" + "a" * 32
    a, b = "owg:w:" + "a" * 32, "owg:w:" + "b" * 32
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    monkeypatch.setattr(routes, "_source_scope", lambda: scope)
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda name: "local-test-secret")
    monkeypatch.setattr(local_auth, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(routes, "auth_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(store, "auth_dir", lambda: tmp_path, raising=False)
    # Provider is already consent/retention-filtered by the production ledger.
    snapshot = {"format": "openworkgraph.local-case-declarations.v1", "items": [
        {"work_item_ref": a, "depends_on_work_item_refs": [b],
         "goal_code": "verify_operation", "expected_outcome_code": "operation_receipt_reviewed",
         "revision": 1, "updated_at": "2026-11-07T00:00:00Z",
         "expires_at": "2026-11-08T00:00:00Z",
         "provenance": "explicit_owner_declaration_not_source_verified",
         "expected_outcome_verified": False, "business_obligation_verified": False,
         "customer_outcome_verified": False, "agent_execution_authorized": False,
         "source_state": "unknown_until_explicit_recheck"},
        {"work_item_ref": b, "depends_on_work_item_refs": [],
         "goal_code": "verify_operation", "expected_outcome_code": "operation_receipt_reviewed",
         "revision": 1, "updated_at": "2026-11-07T00:00:00Z",
         "expires_at": "2026-11-08T00:00:00Z",
         "provenance": "explicit_owner_declaration_not_source_verified",
         "expected_outcome_verified": False, "business_obligation_verified": False,
         "customer_outcome_verified": False, "agent_execution_authorized": False,
         "source_state": "unknown_until_explicit_recheck"},
    ]}
    monkeypatch.setattr(store, "reconcile_current_reviewed_dependencies",
                        lambda path, **kwargs: store.replace_reviewed_dependencies(
                            path, scope=kwargs["scope"], actor=kwargs["actor"], snapshot=snapshot))
    response = Response()
    result = asyncio.run(routes.get_local_durable_reviewed_dependencies(
        _request("valid", "work_item_ref=" + a), response))
    assert result["context"]["direct"] == [b]
    assert result["agent_execution_authorized"] is False
    assert result["customer_outcome"] == "unknown"
    assert response.headers["cache-control"] == "no-store"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_local_durable_reviewed_dependencies(
            _request("valid", "work_item_ref=untrusted"), Response()))
    assert exc.value.status_code == 422
