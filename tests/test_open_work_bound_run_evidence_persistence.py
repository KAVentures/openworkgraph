"""Opt-in provider evidence persistence on the authenticated bound-run route.

The case operation ledger is independently exercised in its own SQLite tests;
these route tests cover its explicit admission, CAS, and failure boundaries.
"""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from server import open_work_declaration_routes as routes
from server import open_work_run_case_binding as binding
from test_open_work_run_source_verification_routes import Request, setup, SCOPE, CASE, RUN, FP


def _bind():
    actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
    binding.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                     case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)


def test_explicit_persistence_reads_prior_history_before_source_and_cas_appends(monkeypatch, tmp_path):
    async def exercise():
        payload, source = setup(monkeypatch, tmp_path)
        payload["persist_provider_check"] = True
        _bind()
        events = [{"sequence": 1, "kind": "check"}]
        order = []
        def read(*args, **kwargs):
            order.append("read")
            return {"events": events}
        def provider(*args):
            order.append("provider")
            return {"provider_reported_state": "paid"}
        def append(*args, **kwargs):
            order.append("append")
            assert kwargs["expected_history"] is events
            assert kwargs["verification"]["provider_operation_verified"] is True
            kwargs["guard"]()
            return {"events": events + [{"sequence": 2, "kind": "check"}]}
        monkeypatch.setattr(routes, "read_operation_evidence", read)
        monkeypatch.setattr(routes, "source_operation_receipt", provider)
        monkeypatch.setattr(routes, "append_operation_evidence", append)
        response = SimpleNamespace(headers={})
        result = await routes.verify_local_bound_run_source(Request(), response)
        assert order == ["read", "provider", "append"]
        assert result["provider_check_persisted"] is True
        assert result["case_provider_evidence_history"]["events"][-1]["sequence"] == 2
        assert result["provider_evidence_run_identity_independently_verified"] is False
        assert result["customer_outcome_verified"] is False
        assert response.headers["Cache-Control"] == "no-store"
    asyncio.run(exercise())


def test_default_read_only_does_not_touch_private_ledger(monkeypatch, tmp_path):
    async def exercise():
        setup(monkeypatch, tmp_path)
        _bind()
        read = Mock(side_effect=AssertionError("no ledger read"))
        append = Mock(side_effect=AssertionError("no ledger write"))
        monkeypatch.setattr(routes, "read_operation_evidence", read)
        monkeypatch.setattr(routes, "append_operation_evidence", append)
        result = await routes.verify_local_bound_run_source(Request(), SimpleNamespace(headers={}))
        assert result["provider_check_persisted"] is False
        assert result["case_provider_evidence_history"] is None
        read.assert_not_called()
        append.assert_not_called()
    asyncio.run(exercise())


def test_concurrent_ledger_change_refuses_append_result(monkeypatch, tmp_path):
    async def exercise():
        payload, _ = setup(monkeypatch, tmp_path)
        payload["persist_provider_check"] = True
        _bind()
        monkeypatch.setattr(routes, "read_operation_evidence",
                            lambda *a, **kw: {"events": []})
        def changed(*args, **kwargs):
            assert kwargs["expected_history"] == []
            raise routes.CaseDeclarationError("stale evidence history")
        monkeypatch.setattr(routes, "append_operation_evidence", changed)
        with pytest.raises(HTTPException) as exc:
            await routes.verify_local_bound_run_source(Request(), SimpleNamespace(headers={}))
        assert exc.value.status_code == 409
    asyncio.run(exercise())


def test_revoked_binding_before_append_denies_persistence(monkeypatch, tmp_path):
    async def exercise():
        payload, source = setup(monkeypatch, tmp_path)
        payload["persist_provider_check"] = True
        _bind()
        monkeypatch.setattr(routes, "read_operation_evidence",
                            lambda *a, **kw: {"events": []})
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        def revoke_on_provider(*args):
            binding.revoke_binding(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                                   case_ref=CASE, run_ref=RUN)
            return None
        monkeypatch.setattr(routes, "source_operation_receipt", revoke_on_provider)
        append = Mock()
        monkeypatch.setattr(routes, "append_operation_evidence", append)
        with pytest.raises(HTTPException):
            await routes.verify_local_bound_run_source(Request(), SimpleNamespace(headers={}))
        append.assert_not_called()
    asyncio.run(exercise())
