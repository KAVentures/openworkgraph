"""Integration-level route orchestration for explicit bound-run provider rechecks."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from server import open_work_declaration_routes as routes
from server import open_work_run_case_binding as ledger

SCOPE = "owg:scope:" + "a" * 32
CASE = "owg:w:" + "b" * 32
RUN = "owg:run:" + "c" * 32
FP = "d" * 64


class Request:
    headers = {"authorization": "owg-session test", "content-type": "application/json"}


def setup(monkeypatch, directory):
    from pathlib import Path
    monkeypatch.setattr(ledger, "auth_dir", lambda: Path(directory))
    monkeypatch.setattr(ledger, "read_policy",
                        lambda: {"human_retention": {"mode": "days", "days": 3}})
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)
    monkeypatch.setattr(routes, "_require_scope", AsyncMock(return_value=SCOPE))
    monkeypatch.setattr(routes, "_recheck_source", AsyncMock())
    payload = dict(work_item_ref=CASE, expected_revision=1, expected_edit_token="edit",
                   confirm_historical_review=True, confirm_run_comparison=True,
                   confirm_source_verification=True, max_cases=1,
                   deployment_id="dep_" + "a" * 16, run_id="run", record_link={})
    monkeypatch.setattr(routes, "_body", AsyncMock(return_value=payload))
    monkeypatch.setattr(routes, "_load_review_triage", AsyncMock(
        return_value=({"case_reviews": []}, {"work_item_ref": CASE}, "signature",
                      "secret-at-least-16")))
    monkeypatch.setattr(routes, "_operation_ledger_guard", lambda *args: None)
    monkeypatch.setattr(routes, "validate_case_record_link", lambda *a, **kw: "owg:r:" + "f" * 32)
    monkeypatch.setattr(routes, "read_imported_run",
                        lambda **kw: {"run_ref": RUN, "run_snapshot_fingerprint": FP})
    monkeypatch.setattr(routes, "project_case_operation_verification",
                        lambda *args: {"provider_operation_verified": True})
    monkeypatch.setattr(routes, "compose_fresh_outcome_triage",
                        lambda *args: {"historical_review": "separate"})
    provider = AsyncMock()  # never used: production provider is synchronous
    from unittest.mock import Mock
    source = Mock(return_value={"provider_reported_state": "paid"})
    monkeypatch.setattr(routes, "source_operation_receipt", source)
    return payload, source


def test_verified_provider_state_never_claims_customer_or_agent_causality(monkeypatch, tmp_path):
    async def exercise():
        _, source = setup(monkeypatch, tmp_path)
        import hashlib
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                        case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
        response = SimpleNamespace(headers={})
        result = await routes.verify_local_bound_run_source(Request(), response)
        assert source.call_count == 1
        assert result["provider_operation_verified"] is True
        assert result["customer_outcome_verified"] is False
        assert result["agent_caused_operation_verified"] is False
        assert result["run_case_identity_independently_verified"] is False
        assert result["source_write_performed"] is False
        assert response.headers["Cache-Control"] == "no-store"
    asyncio.run(exercise())


def test_missing_or_revoked_binding_blocks_provider_read(monkeypatch, tmp_path):
    async def exercise():
        _, source = setup(monkeypatch, tmp_path)
        with pytest.raises(HTTPException):
            await routes.verify_local_bound_run_source(Request(), SimpleNamespace(headers={}))
        source.assert_not_called()
    asyncio.run(exercise())


def test_source_outage_is_not_success(monkeypatch, tmp_path):
    async def exercise():
        _, source = setup(monkeypatch, tmp_path)
        import hashlib
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                        case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
        source.side_effect = OSError("provider offline")
        monkeypatch.setattr(routes, "project_case_operation_verification",
                            lambda admitted, ref, receipt: {
                                "provider_operation_verified": False,
                                "availability": "unknown" if receipt is None else "source_reported"})
        result = await routes.verify_local_bound_run_source(
            Request(), SimpleNamespace(headers={}))
        assert result["provider_operation_verified"] is False
        assert result["fresh_source_triage"]["historical_review"] == "separate"
    asyncio.run(exercise())


def test_source_change_during_provider_io_denies_result(monkeypatch, tmp_path):
    async def exercise():
        _, source = setup(monkeypatch, tmp_path)
        import hashlib
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                        case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
        checks = 0
        async def recheck(_scope):
            nonlocal checks
            checks += 1
            if checks == 2:
                raise HTTPException(409, "local source connection changed")
        monkeypatch.setattr(routes, "_recheck_source", recheck)
        with pytest.raises(HTTPException) as exc:
            await routes.verify_local_bound_run_source(Request(), SimpleNamespace(headers={}))
        assert exc.value.status_code == 409
        assert source.call_count == 1
    asyncio.run(exercise())
