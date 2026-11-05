"""Authenticated case-wide evidence review for an operator-bound imported run."""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from server import open_work_declaration_routes as routes
from server import open_work_run_case_binding as binding
from test_open_work_run_source_verification_routes import Request, setup, SCOPE, CASE, RUN, FP


def _prepare(monkeypatch, tmp_path):
    payload, source = setup(monkeypatch, tmp_path)
    payload.pop("confirm_source_verification")
    actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
    binding.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                     case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
    return payload, source, actor


def test_read_only_join_returns_case_wide_history_not_run_outcome(monkeypatch, tmp_path):
    async def exercise():
        _, source, _ = _prepare(monkeypatch, tmp_path)
        ledger = Mock(return_value={"work_item_ref": CASE, "events": [{"sequence": 1, "kind": "check"}], "provider_check_count": 1, "disputed_check_count": 0, "last_observed_provider_state": "paid"})
        human = Mock(return_value={"work_item_ref": CASE, "events": [], "event_count": 0, "latest_human_assessed_status": "unknown"})
        monkeypatch.setattr(routes, "read_human_outcomes", human)
        monkeypatch.setattr(routes, "read_operation_evidence", ledger)
        response = SimpleNamespace(headers={})
        result = await routes.review_local_bound_run_evidence_history(Request(), response)
        assert result["run_ref"] == RUN
        assert result["case_provider_evidence_history"]["events"][0]["sequence"] == 1
        assert result["evidence_scope"] == "case_wide_not_run_attributed"
        assert result["customer_outcome_verified"] is False
        assert result["agent_caused_operation_verified"] is False
        assert result["source_read_performed"] is False
        assert result["source_write_performed"] is False
        assert response.headers["Cache-Control"] == "no-store"
        source.assert_not_called()
        assert ledger.call_count == 1
        assert human.call_count == 1
        assert result["case_historical_reconciliation"]["customer_outcome_verified"] is False
        ledger.call_args.kwargs["guard"]()
    asyncio.run(exercise())


def test_missing_binding_refuses_evidence_read(monkeypatch, tmp_path):
    async def exercise():
        _, source, actor = _prepare(monkeypatch, tmp_path)
        binding.revoke_binding(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                               case_ref=CASE, run_ref=RUN)
        read = Mock(side_effect=AssertionError("ledger must not be read"))
        monkeypatch.setattr(routes, "read_operation_evidence", read)
        with pytest.raises(HTTPException):
            await routes.review_local_bound_run_evidence_history(
                Request(), SimpleNamespace(headers={}))
        read.assert_not_called()
        source.assert_not_called()
    asyncio.run(exercise())


def test_revocation_during_history_read_refuses_response(monkeypatch, tmp_path):
    async def exercise():
        _, source, actor = _prepare(monkeypatch, tmp_path)
        def read(*args, **kwargs):
            binding.revoke_binding(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                                   case_ref=CASE, run_ref=RUN)
            return {"work_item_ref": CASE, "events": [], "provider_check_count": 0, "disputed_check_count": 0, "last_observed_provider_state": "unknown"}
        monkeypatch.setattr(routes, "read_operation_evidence", read)
        monkeypatch.setattr(routes, "read_human_outcomes", Mock(return_value={"work_item_ref": CASE, "events": [], "event_count": 0, "latest_human_assessed_status": "unknown"}))
        with pytest.raises(HTTPException) as exc:
            await routes.review_local_bound_run_evidence_history(
                Request(), SimpleNamespace(headers={}))
        assert exc.value.status_code in (403, 409)
        source.assert_not_called()
    asyncio.run(exercise())


def test_explicit_review_and_exact_case_required(monkeypatch, tmp_path):
    async def exercise():
        payload, source, _ = _prepare(monkeypatch, tmp_path)
        payload["confirm_run_comparison"] = False
        read = Mock()
        monkeypatch.setattr(routes, "read_operation_evidence", read)
        with pytest.raises(HTTPException) as exc:
            await routes.review_local_bound_run_evidence_history(
                Request(), SimpleNamespace(headers={}))
        assert exc.value.status_code == 403
        read.assert_not_called()
        source.assert_not_called()
    asyncio.run(exercise())
