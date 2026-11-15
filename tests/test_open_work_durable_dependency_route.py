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


def test_durable_route_purges_stale_mirror_when_review_read_fails(monkeypatch, tmp_path):
    """A revoked/failed declaration read must not retain previously mirrored edges."""
    from server import local_auth
    from server import open_work_dependency_store as store

    scope = "owg:scope:" + "a" * 32
    a, b = "owg:w:" + "a" * 32, "owg:w:" + "b" * 32
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    monkeypatch.setattr(routes, "_source_scope", lambda: scope)
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda name: "local-test-secret")
    monkeypatch.setattr(local_auth, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "auth_dir", lambda: tmp_path, raising=False)

    import hashlib
    import hmac
    actor = "dashboard:" + hmac.new(
        b"local-test-secret", b"open-work:durable-session-actor:v1:valid",
        hashlib.sha256,
    ).hexdigest()[:32]
    path = str(tmp_path / ".open_work_reviewed_dependencies.sqlite3")
    store.declare_dependency(path, scope=scope, actor=actor, case_id=a, prerequisite=b)
    assert store.dependency_context(path, scope=scope, actor=actor, case_id=a)["direct"] == [b]

    def denied(*args, **kwargs):
        from server.open_work_case_declarations import CaseDeclarationError
        raise CaseDeclarationError("reviewed declaration scope changed")

    monkeypatch.setattr(store, "reconcile_current_reviewed_dependencies", denied)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_local_durable_reviewed_dependencies(
            _request("valid", "work_item_ref=" + a), Response()))
    assert exc.value.status_code == 409
    assert store.dependency_context(path, scope=scope, actor=actor, case_id=a)["direct"] == []


def test_durable_route_purges_when_scope_revoked_after_reconciliation(monkeypatch, tmp_path):
    """A mid-request scope revocation must not leave a readable mirror."""
    from server import local_auth
    from server import open_work_dependency_store as store
    import hashlib
    import hmac

    scope = "owg:scope:" + "a" * 32
    a, b = "owg:w:" + "a" * 32, "owg:w:" + "b" * 32
    actor = "dashboard:" + hmac.new(
        b"local-test-secret", b"open-work:durable-session-actor:v1:valid",
        hashlib.sha256,
    ).hexdigest()[:32]
    path = str(tmp_path / ".open_work_reviewed_dependencies.sqlite3")
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    monkeypatch.setattr(routes, "_source_scope", lambda: scope)
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda name: "local-test-secret")
    monkeypatch.setattr(local_auth, "auth_dir", lambda: tmp_path)

    def populate(path, **kwargs):
        store.declare_dependency(path, scope=scope, actor=actor, case_id=a, prerequisite=b)
        return {"reconciled": True}

    async def revoke(_scope):
        raise HTTPException(403, "source grant revoked")

    monkeypatch.setattr(store, "reconcile_current_reviewed_dependencies", populate)
    monkeypatch.setattr(routes, "_recheck_source", revoke)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_local_durable_reviewed_dependencies(
            _request("valid", "work_item_ref=" + a), Response()))
    assert exc.value.status_code == 403
    assert store.dependency_context(path, scope=scope, actor=actor, case_id=a)["direct"] == []


def test_durable_route_refuses_success_if_revocation_cleanup_fails(monkeypatch, tmp_path):
    """A failed purge must never return the just-reconciled dependency data."""
    from server import local_auth
    from server import open_work_dependency_store as store
    scope = "owg:scope:" + "a" * 32
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    monkeypatch.setattr(routes, "_source_scope", lambda: scope)
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda name: "local-test-secret")
    monkeypatch.setattr(local_auth, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "reconcile_current_reviewed_dependencies",
                        lambda path, **kwargs: {"reconciled": True})

    async def revoke(_scope):
        raise HTTPException(403, "source grant revoked")

    def fail_purge(*args, **kwargs):
        raise OSError("database locked")

    monkeypatch.setattr(routes, "_recheck_source", revoke)
    monkeypatch.setattr(store, "purge_actor", fail_purge)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_local_durable_reviewed_dependencies(
            _request("valid"), Response()))
    assert exc.value.status_code == 503
    assert "cleanup unavailable" in exc.value.detail


def test_durable_route_rejects_ambiguous_query_before_source_access(monkeypatch):
    """A caller cannot select ambiguous refs or introduce a scope override."""
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    monkeypatch.setattr(routes, "_source_scope", lambda: pytest.fail("invalid query read source scope"))
    valid = "owg:w:" + "a" * 32
    for query in (
        "work_item_ref=" + valid + "&work_item_ref=" + "owg:w:" + "b" * 32,
        "work_item_ref=" + valid + "&scope=owg:scope:attacker",
        "actor=dashboard:attacker",
        "work_item_ref=",
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(routes.get_local_durable_reviewed_dependencies(
                _request("valid", query), Response()))
        assert exc.value.status_code == 422


def test_durable_route_revoked_dashboard_session_purges_mirror(monkeypatch, tmp_path):
    """Session expiry after reconciliation cannot return a durable graph."""
    from server import local_auth
    from server import open_work_dependency_store as store
    import hashlib
    import hmac

    scope = "owg:scope:" + "a" * 32
    a, b = "owg:w:" + "a" * 32, "owg:w:" + "b" * 32
    actor = "dashboard:" + hmac.new(
        b"local-test-secret", b"open-work:durable-session-actor:v1:valid",
        hashlib.sha256,
    ).hexdigest()[:32]
    path = str(tmp_path / ".open_work_reviewed_dependencies.sqlite3")
    calls = 0

    def session_valid(token):
        nonlocal calls
        calls += 1
        return token == "valid" and calls == 1

    def populate(path, **kwargs):
        store.declare_dependency(path, scope=scope, actor=actor, case_id=a, prerequisite=b)
        return {"reconciled": True}

    monkeypatch.setattr(routes, "dashboard_session_valid", session_valid)
    monkeypatch.setattr(routes, "_source_scope", lambda: scope)
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda name: "local-test-secret")
    monkeypatch.setattr(local_auth, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "reconcile_current_reviewed_dependencies", populate)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_local_durable_reviewed_dependencies(
            _request("valid", "work_item_ref=" + a), Response()))
    assert exc.value.status_code == 403
    assert calls == 2
    assert store.dependency_context(path, scope=scope, actor=actor, case_id=a)["direct"] == []


def test_durable_route_reports_cleanup_failure_after_invalid_review(monkeypatch, tmp_path):
    """A failed review plus failed purge is a 503, never a stale-data success."""
    from server import local_auth
    from server import open_work_dependency_store as store
    from server.open_work_case_declarations import CaseDeclarationError

    scope = "owg:scope:" + "a" * 32
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    monkeypatch.setattr(routes, "_source_scope", lambda: scope)
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda name: "local-test-secret")
    monkeypatch.setattr(local_auth, "auth_dir", lambda: tmp_path)

    def fail_review(*args, **kwargs):
        raise CaseDeclarationError("review no longer authorized")

    def fail_purge(*args, **kwargs):
        raise OSError("sqlite file locked")

    monkeypatch.setattr(store, "reconcile_current_reviewed_dependencies", fail_review)
    monkeypatch.setattr(store, "purge_actor", fail_purge)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.get_local_durable_reviewed_dependencies(
            _request("valid"), Response()))
    assert exc.value.status_code == 503
    assert exc.value.detail == "durable dependency review cleanup unavailable"


def test_durable_route_isolates_independent_dashboard_sessions(monkeypatch, tmp_path):
    """One valid dashboard session cannot read another session's durable mirror."""
    import hashlib
    import hmac
    from server import local_auth
    from server import open_work_dependency_store as store

    scope = "owg:scope:" + "a" * 32
    a, b = "owg:w:" + "a" * 32, "owg:w:" + "b" * 32
    path = str(tmp_path / ".open_work_reviewed_dependencies.sqlite3")
    monkeypatch.setattr(routes, "dashboard_session_valid",
                        lambda token: token in ("first", "second"))
    monkeypatch.setattr(routes, "_source_scope", lambda: scope)
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda name: "local-test-secret")
    monkeypatch.setattr(local_auth, "auth_dir", lambda: tmp_path)

    def actor(token):
        return "dashboard:" + hmac.new(
            b"local-test-secret",
            ("open-work:durable-session-actor:v1:" + token).encode(),
            hashlib.sha256,
        ).hexdigest()[:32]

    def reconcile(path, **kwargs):
        if kwargs["actor"] == actor("first"):
            store.declare_dependency(
                path, scope=scope, actor=kwargs["actor"],
                case_id=a, prerequisite=b)
        return {"reconciled": True}

    monkeypatch.setattr(store, "reconcile_current_reviewed_dependencies", reconcile)
    first = asyncio.run(routes.get_local_durable_reviewed_dependencies(
        _request("first", "work_item_ref=" + a), Response()))
    second = asyncio.run(routes.get_local_durable_reviewed_dependencies(
        _request("second", "work_item_ref=" + a), Response()))
    assert first["context"]["direct"] == [b]
    assert second["context"]["direct"] == []
    assert actor("first") != actor("second")


def test_source_change_revokes_all_session_receipts(monkeypatch, tmp_path):
    """Source replacement clears every actor's old-scope receipts, not just caller."""
    from server import local_auth
    from server import open_work_dependency_store as deps
    from server import open_work_run_receipts as run_receipts
    from server import open_work_attempt_receipts as receipts
    from server import open_work_run_case_binding as bindings

    old_scope = "owg:scope:" + "a" * 32
    new_scope = "owg:scope:" + "b" * 32
    path = str(tmp_path / ".open_work_attempt_receipts.sqlite3")
    verdict = {"status": "unknown", "provider_operation": "unknown",
               "customer_outcome": "unknown"}
    for tenant, actor in ((old_scope, "session_a"), (old_scope, "session_b"),
                          (new_scope, "session_c")):
        receipts.append_receipt(path, tenant=tenant, actor=actor,
                                attempt="attempt_1", verdict=verdict)
    monkeypatch.setattr(local_auth, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(routes, "_source_scope", lambda: new_scope)
    for name in ("clear_operation_evidence", "clear_human_outcome_reviews",
                 "clear_case_declarations"):
        monkeypatch.setattr(routes, name, lambda: None)
    monkeypatch.setattr(bindings, "clear_binding_scope", lambda scope: None)
    monkeypatch.setattr(run_receipts, "clear_scope", lambda scope: None)
    monkeypatch.setattr(deps, "purge_scope", lambda path, scope: None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes._recheck_source(old_scope))
    assert exc.value.status_code == 409
    assert receipts.list_receipts(path, tenant=old_scope, actor="session_a",
                                  attempt="attempt_1") == []
    assert receipts.list_receipts(path, tenant=old_scope, actor="session_b",
                                  attempt="attempt_1") == []
    assert len(receipts.list_receipts(path, tenant=new_scope, actor="session_c",
                                     attempt="attempt_1")) == 1
