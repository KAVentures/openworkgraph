"""Exercise the actual authenticated route orchestration with source and ledger rechecks."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import hashlib
import asyncio

import pytest
from fastapi import HTTPException

from server import open_work_declaration_routes as routes

SCOPE = "owg:scope:" + "a" * 32
CASE = "owg:w:" + "b" * 32
RUN = "owg:run:" + "c" * 32
FP = "d" * 64


class Request:
    headers = {"authorization": "owg-session example", "content-type": "application/json"}


def test_binding_create_read_revoke_through_authenticated_route(monkeypatch):
    asyncio.run(_exercise_binding_create_read_revoke(monkeypatch))


async def _exercise_binding_create_read_revoke(monkeypatch):
    from server import open_work_run_case_binding as ledger
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as directory:
        from pathlib import Path
        monkeypatch.setattr(ledger, "auth_dir", lambda: Path(directory))
        monkeypatch.setattr(ledger, "read_policy",
                            lambda: {"human_retention": {"mode": "days", "days": 3}})
        monkeypatch.setattr(routes, "_dashboard_only", lambda req: None)
        monkeypatch.setattr(routes, "_require_scope", AsyncMock(return_value=SCOPE))
        monkeypatch.setattr(routes, "_recheck_source", AsyncMock())
        payload = dict(work_item_ref=CASE, expected_revision=1, expected_edit_token="token",
            confirm_historical_review=True, confirm_run_comparison=True, max_cases=1,
            deployment_id="dep_" + "a" * 16, run_id="run", action="bind")
        monkeypatch.setattr(routes, "_body", AsyncMock(side_effect=lambda *args: payload))
        monkeypatch.setattr(routes, "_load_review_triage", AsyncMock(
            return_value=({"case_reviews": []}, {"work_item_ref": CASE}, "signature", "secret-at-least-16")))
        monkeypatch.setattr(routes, "_operation_ledger_guard", lambda *args: None)
        monkeypatch.setattr(routes, "read_imported_run", lambda **kwargs: {
            "run_ref": RUN, "run_snapshot_fingerprint": FP})
        response = SimpleNamespace(headers={})
        first = await routes.manage_local_imported_run_binding(Request(), response)
        assert first["binding"]["association"] == "operator_confirmed"
        assert first["customer_outcome_verified"] is False
        assert response.headers["Cache-Control"] == "no-store"
        payload["action"] = "read"
        assert (await routes.manage_local_imported_run_binding(Request(), response))["binding"] == first["binding"]
        payload["action"] = "revoke"
        assert (await routes.manage_local_imported_run_binding(Request(), response))["binding"]["revoked"]
        payload["action"] = "read"
        assert (await routes.manage_local_imported_run_binding(Request(), response))["binding"] is None


def test_binding_requires_dashboard_session(monkeypatch):
    asyncio.run(_exercise_binding_requires_dashboard_session(monkeypatch))


async def _exercise_binding_requires_dashboard_session(monkeypatch):
    monkeypatch.setattr(routes, "_dashboard_only", lambda req: (_ for _ in ()).throw(HTTPException(403)))
    with pytest.raises(HTTPException) as exc:
        await routes.manage_local_imported_run_binding(Request(), SimpleNamespace(headers={}))
    assert exc.value.status_code == 403
