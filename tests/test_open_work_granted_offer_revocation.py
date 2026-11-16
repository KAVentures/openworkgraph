"""Regression: a revoked scoped grant cannot disclose an in-flight offer snapshot."""
import asyncio

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from server import open_work_handoff_routes as routes


def _request():
    return Request({"type": "http", "method": "GET",
                    "path": "/v1/open-work/dispatch/granted",
                    "headers": [(b"authorization", b"Bearer opaque-test-token")]})


def test_granted_offer_read_denied_after_concurrent_revoke(monkeypatch):
    ref = "owg:w:" + "a" * 32
    identity = {"grant_id": "a" * 32, "work_item_ref": ref, "agent": "test"}
    calls = []

    def authenticate(_header, _ref=None):
        calls.append(_ref)
        return identity if len(calls) == 1 else None

    async def read_status(_fn, *args, **kwargs):
        assert kwargs == {"owner": False}
        return {"enabled": True, "offers": [{"work_item_ref": ref, "status": "offered"}]}

    monkeypatch.setattr(routes.grants, "authenticate", authenticate)
    monkeypatch.setattr(routes, "_run", read_status)
    response = Response()
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.granted_offers(_request(), response))
    assert error.value.status_code == 401
    assert len(calls) == 2
    assert response.headers["cache-control"] == "no-store"


def test_granted_offer_read_keeps_exact_case_only(monkeypatch):
    ref = "owg:w:" + "a" * 32
    other = "owg:w:" + "b" * 32
    identity = {"grant_id": "a" * 32, "work_item_ref": ref, "agent": "test"}
    monkeypatch.setattr(routes.grants, "authenticate", lambda *_: identity)

    async def read_status(_fn, *args, **kwargs):
        return {"enabled": True, "offers": [
            {"work_item_ref": other, "status": "offered"},
            {"work_item_ref": ref, "status": "claimed"},
            {"work_item_ref": ref, "status": "offered"},
        ]}

    monkeypatch.setattr(routes, "_run", read_status)
    response = Response()
    result = asyncio.run(routes.granted_offers(_request(), response))
    assert result["offers"] == [{"work_item_ref": ref, "status": "offered"}]
    assert response.headers["cache-control"] == "no-store"
