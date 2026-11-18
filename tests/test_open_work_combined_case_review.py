"""Combined operator case-review route: bounded replay and fail-closed joins."""
import asyncio
import json

import pytest
from fastapi import HTTPException, Request, Response

from server import open_work_declaration_routes as routes


def _request(payload):
    body = json.dumps(payload).encode()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({
        "type": "http", "method": "POST", "path": "/v1/open-work/declarations/human-outcomes/case-review",
        "headers": [(b"authorization", b"OWG-Session test"), (b"content-type", b"application/json")],
        "query_string": b"", "server": ("test", 80), "scheme": "http",
    }, receive=receive)


def test_combined_case_review_rechecks_both_snapshots_and_returns_unknown(monkeypatch):
    ref = "owg:w:" + "a" * 32
    counts = {"receipt": 0, "dependency": 0}
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)
    async def scope():
        return "owg:scope:test"
    monkeypatch.setattr(routes, "_require_scope", scope)
    async def source_check(scope):
        assert scope == "owg:scope:test"
    monkeypatch.setattr(routes, "_recheck_source", source_check)
    async def receipts(request, response):
        counts["receipt"] += 1
        assert json.loads(await request.body()) == {"approved": True}
        return {"case_ref": ref, "entries": [], "customer_outcome_verified": False}
    async def deps(request, response):
        counts["dependency"] += 1
        assert request.query_params.get("work_item_ref") == ref
        return {"context": {"direct": [], "prerequisites": [], "truncated": False}}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", receipts)
    monkeypatch.setattr(routes, "get_local_durable_reviewed_dependencies", deps)
    response = Response()
    result = asyncio.run(routes.read_local_combined_case_review(
        _request({"approved": True}), response))
    # The final source await requires a third, fail-closed admission pass.
    assert counts == {"receipt": 3, "dependency": 3}
    assert result["customer_outcome"] == "unknown"
    assert result["agent_execution_authorized"] is False
    assert result["cross_store_atomic_snapshot"] is False
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("changed", ["receipt", "dependency"])
def test_combined_case_review_rejects_concurrent_changes(monkeypatch, changed):
    ref = "owg:w:" + "b" * 32
    counts = {"receipt": 0, "dependency": 0}
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)
    async def scope():
        return "owg:scope:test"
    monkeypatch.setattr(routes, "_require_scope", scope)
    async def source_check(scope):
        pass
    monkeypatch.setattr(routes, "_recheck_source", source_check)
    async def receipts(request, response):
        counts["receipt"] += 1
        return {"case_ref": ref, "revision": counts["receipt"] if changed == "receipt" else 1}
    async def deps(request, response):
        counts["dependency"] += 1
        return {"revision": counts["dependency"] if changed == "dependency" else 1}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", receipts)
    monkeypatch.setattr(routes, "get_local_durable_reviewed_dependencies", deps)
    response = Response()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_combined_case_review(_request({}), response))
    assert exc.value.status_code == 409
    assert "cache-control" not in response.headers


def test_combined_case_review_rejects_missing_session_before_reads(monkeypatch):
    monkeypatch.setattr(routes, "_dashboard_only",
                        lambda request: (_ for _ in ()).throw(HTTPException(403)))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_combined_case_review(_request({}), Response()))
    assert exc.value.status_code == 403


def test_combined_case_review_rejects_oversized_body(monkeypatch):
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_combined_case_review(
            _request({"padding": "x" * 5000}), Response()))
    assert exc.value.status_code == 413


def test_combined_case_review_rejects_source_revocation_during_final_dependency_read(monkeypatch):
    """The last dependency await cannot authorize a disconnected source."""
    ref = "owg:w:" + "c" * 32
    source_checks = 0
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)
    async def scope():
        return "owg:scope:original"
    monkeypatch.setattr(routes, "_require_scope", scope)
    async def source_check(expected):
        nonlocal source_checks
        assert expected == "owg:scope:original"
        source_checks += 1
        if source_checks == 2:
            raise HTTPException(409, "local source connection changed")
    monkeypatch.setattr(routes, "_recheck_source", source_check)
    async def receipts(request, response):
        return {"case_ref": ref, "entries": [], "customer_outcome_verified": False}
    async def dependencies(request, response):
        assert request.query_params.get("work_item_ref") == ref
        return {"context": {"direct": [], "prerequisites": [], "truncated": False}}
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", receipts)
    monkeypatch.setattr(routes, "get_local_durable_reviewed_dependencies", dependencies)
    response = Response()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_combined_case_review(_request({}), response))
    assert exc.value.status_code == 409
    assert source_checks == 2
    assert "cache-control" not in response.headers


def test_combined_case_review_rejects_stream_over_limit_without_draining(monkeypatch):
    """Stop receiving immediately after the cap is exceeded, not after EOF."""
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)
    chunks = [b"x" * 2000, b"y" * 2097]
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if calls > len(chunks):
            raise AssertionError("oversized request must not drain the stream")
        return {"type": "http.request", "body": chunks[calls - 1],
                "more_body": True}

    request = Request({
        "type": "http", "method": "POST",
        "path": "/v1/open-work/declarations/human-outcomes/case-review",
        "headers": [], "query_string": b"",
        "server": ("test", 80), "scheme": "http",
    }, receive=receive)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.read_local_combined_case_review(request, Response()))
    assert exc.value.status_code == 413
    assert calls == 2


def test_combined_case_review_accepts_chunked_bounded_request(monkeypatch):
    """A valid chunked request must replay identical bytes to receipt admission."""
    ref = "owg:w:" + "d" * 32
    chunks = [b'{"approved":', b'true}']
    calls = 0

    async def receive():
        nonlocal calls
        index = calls
        calls += 1
        return {"type": "http.request", "body": chunks[index],
                "more_body": index < len(chunks) - 1}

    request = Request({
        "type": "http", "method": "POST",
        "path": "/v1/open-work/declarations/human-outcomes/case-review",
        "headers": [], "query_string": b"",
        "server": ("test", 80), "scheme": "http",
    }, receive=receive)
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)

    async def scope():
        return "owg:scope:test"

    async def source_check(scope):
        assert scope == "owg:scope:test"

    async def receipts(request, response):
        assert await request.body() == b"".join(chunks)
        return {"case_ref": ref, "entries": [], "customer_outcome_verified": False}

    async def deps(request, response):
        assert request.query_params["work_item_ref"] == ref
        return {"context": {"direct": [], "prerequisites": [], "truncated": False}}

    monkeypatch.setattr(routes, "_require_scope", scope)
    monkeypatch.setattr(routes, "_recheck_source", source_check)
    monkeypatch.setattr(routes, "read_local_bound_attempt_receipts", receipts)
    monkeypatch.setattr(routes, "get_local_durable_reviewed_dependencies", deps)
    result = asyncio.run(routes.read_local_combined_case_review(request, Response()))
    assert result["case_ref"] == ref
    assert result["customer_outcome"] == "unknown"
    assert calls == 2
