"""Executable retained-receipt disclosure regression for in-flight binding revocation.

This tests the actual authenticated route coroutine rather than source-code ordering.
No provider request, credential, or persistent journal is needed.
"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server import open_work_declaration_routes as routes
from server import open_work_run_case_binding as bindings


def test_receipt_route_denies_binding_revoked_during_final_source_check(monkeypatch, tmp_path):
    payload = {
        "work_item_ref": "owg:case:test", "expected_revision": 1,
        "expected_edit_token": "test-token", "confirm_historical_review": True,
        "confirm_run_comparison": True, "max_cases": 1,
        "deployment_id": "test-deployment", "run_id": "test-run",
    }
    run = {"run_ref": "test-run-ref", "run_snapshot_fingerprint": "test-fingerprint"}
    state = {"binding": True, "source_checks": 0}
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)

    async def scope():
        return "owg:scope:test"

    async def body(request, required):
        assert set(payload) == required
        return payload

    async def triage(scope_value, supplied):
        assert scope_value == "owg:scope:test"
        return {"state": "unknown"}, {"work_item_ref": payload["work_item_ref"]}, "sig", "secret"

    async def source_check(scope_value):
        state["source_checks"] += 1
        if state["source_checks"] == 3:
            # Simulate a concurrent, explicitly revoked run/case binding
            # during the last asynchronous source-authorization boundary.
            state["binding"] = False

    def get_binding(**kwargs):
        return state["binding"]

    def exclusive(**kwargs):
        if not state["binding"]:
            raise ValueError("binding revoked")

    monkeypatch.setattr(routes, "_require_scope", scope)
    monkeypatch.setattr(routes, "_body", body)
    monkeypatch.setattr(routes, "_load_review_triage", triage)
    monkeypatch.setattr(routes, "_recheck_source", source_check)
    monkeypatch.setattr(routes, "read_imported_run", lambda **kwargs: run)
    monkeypatch.setattr(routes, "_operation_ledger_guard", lambda *args: None)
    monkeypatch.setattr(bindings, "get_binding", get_binding)
    monkeypatch.setattr(bindings, "require_exclusive_run_case_binding", exclusive)
    monkeypatch.setattr(bindings, "_retention", lambda: timedelta(days=1))
    monkeypatch.setattr("server.local_auth.auth_dir", lambda: tmp_path)

    request = SimpleNamespace(headers={"authorization": "OWG-Session test"})
    response = SimpleNamespace(headers={})
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.read_local_bound_attempt_receipts(request, response))
    assert error.value.status_code == 403
    assert state["source_checks"] == 3
    assert "Cache-Control" not in response.headers


def test_receipt_route_keeps_unknown_outcome_when_binding_stays_current(monkeypatch, tmp_path):
    payload = {
        "work_item_ref": "owg:case:test", "expected_revision": 1,
        "expected_edit_token": "test-token", "confirm_historical_review": True,
        "confirm_run_comparison": True, "max_cases": 1,
        "deployment_id": "test-deployment", "run_id": "test-run",
    }
    run = {"run_ref": "test-run-ref", "run_snapshot_fingerprint": "test-fingerprint"}
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)

    async def scope():
        return "owg:scope:test"

    async def body(request, required):
        return payload

    async def triage(scope_value, supplied):
        return {"state": "unknown"}, {"work_item_ref": payload["work_item_ref"]}, "sig", "secret"

    async def source_check(scope_value):
        return None

    monkeypatch.setattr(routes, "_require_scope", scope)
    monkeypatch.setattr(routes, "_body", body)
    monkeypatch.setattr(routes, "_load_review_triage", triage)
    monkeypatch.setattr(routes, "_recheck_source", source_check)
    monkeypatch.setattr(routes, "read_imported_run", lambda **kwargs: run)
    monkeypatch.setattr(routes, "_operation_ledger_guard", lambda *args: None)
    monkeypatch.setattr(bindings, "get_binding", lambda **kwargs: True)
    monkeypatch.setattr(bindings, "require_exclusive_run_case_binding", lambda **kwargs: None)
    monkeypatch.setattr(bindings, "_retention", lambda: timedelta(days=1))
    monkeypatch.setattr("server.local_auth.auth_dir", lambda: tmp_path)

    response = SimpleNamespace(headers={})
    result = asyncio.run(routes.read_local_bound_attempt_receipts(
        SimpleNamespace(headers={"authorization": "OWG-Session test"}), response,
    ))
    assert result["entries"] == []
    assert result["customer_outcome_verified"] is False
    assert result["provider_event_independently_rechecked_on_this_read"] is False
    assert result["agent_execution_authorized"] is False
    assert response.headers["Cache-Control"] == "no-store"
