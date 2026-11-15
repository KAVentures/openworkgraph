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


def test_ambiguous_run_case_binding_denies_provider_verification(monkeypatch, tmp_path):
    """A run bound to two cases must not gain case-specific provider authority."""
    async def exercise():
        _, source = setup(monkeypatch, tmp_path)
        import hashlib
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        for case in (CASE, "owg:w:" + "e" * 32):
            ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                            case_ref=case, run_ref=RUN, fingerprint=FP, revision=1)
        with pytest.raises(HTTPException):
            await routes.verify_local_bound_run_source(Request(), SimpleNamespace(headers={}))
        source.assert_not_called()
    asyncio.run(exercise())


def test_binding_becomes_ambiguous_during_source_read(monkeypatch, tmp_path):
    """A second case binding appearing during provider I/O denies the response."""
    async def exercise():
        _, source = setup(monkeypatch, tmp_path)
        import hashlib
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                        case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
        def provider(*args):
            ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                            case_ref="owg:w:" + "e" * 32, run_ref=RUN,
                            fingerprint=FP, revision=1)
            return {"provider_reported_state": "paid"}
        source.side_effect = provider
        with pytest.raises(HTTPException):
            await routes.verify_local_bound_run_source(Request(), SimpleNamespace(headers={}))
        assert source.call_count == 1
    asyncio.run(exercise())


def test_session_revoked_during_provider_io_denies_verified_result(monkeypatch, tmp_path):
    """Fresh provider evidence is not returned to a now-revoked operator."""
    async def exercise():
        _, source = setup(monkeypatch, tmp_path)
        import hashlib
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                        case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
        checks = 0

        def session_guard(_request):
            nonlocal checks
            checks += 1
            if checks > 1:
                raise HTTPException(403, "dashboard session revoked")

        monkeypatch.setattr(routes, "_dashboard_only", session_guard)
        with pytest.raises(HTTPException) as error:
            await routes.verify_local_bound_run_source(
                Request(), SimpleNamespace(headers={}))
        assert error.value.status_code == 403
        assert source.call_count == 1
        assert checks >= 2
    asyncio.run(exercise())


def test_revoked_session_before_optional_persistence_never_appends(monkeypatch, tmp_path):
    """A revoked session must not write a provider claim after source I/O."""
    async def exercise():
        payload, source = setup(monkeypatch, tmp_path)
        payload["persist_provider_check"] = True
        import hashlib
        from unittest.mock import Mock
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                        case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
        monkeypatch.setattr(routes, "read_operation_evidence",
                            lambda *a, **kw: {"events": []})
        append = Mock(return_value={"events": []})
        monkeypatch.setattr(routes, "append_operation_evidence", append)
        checks = 0

        def session_guard(_request):
            nonlocal checks
            checks += 1
            if source.call_count:
                raise HTTPException(403, "dashboard session revoked")

        monkeypatch.setattr(routes, "_dashboard_only", session_guard)
        with pytest.raises(HTTPException) as error:
            await routes.verify_local_bound_run_source(
                Request(), SimpleNamespace(headers={}))
        assert error.value.status_code == 403
        assert source.call_count == 1
        append.assert_not_called()
    asyncio.run(exercise())


def test_revocation_inside_optional_persistence_guard_denies_commit(monkeypatch, tmp_path):
    """The storage guard must recheck session even after pre-append admission."""
    async def exercise():
        payload, source = setup(monkeypatch, tmp_path)
        payload["persist_provider_check"] = True
        import hashlib
        from unittest.mock import Mock
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                        case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
        monkeypatch.setattr(routes, "read_operation_evidence",
                            lambda *a, **kw: {"events": []})
        attempted = Mock()

        def append(*args, **kwargs):
            attempted()
            kwargs["guard"]()
            return {"events": []}

        monkeypatch.setattr(routes, "append_operation_evidence", append)
        checks = 0

        def session_guard(_request):
            nonlocal checks
            checks += 1
            # Initial admission and pre-append admission pass; the guarded
            # storage operation observes the revocation.
            if checks >= 3:
                raise HTTPException(403, "dashboard session revoked")

        monkeypatch.setattr(routes, "_dashboard_only", session_guard)
        with pytest.raises(HTTPException) as error:
            await routes.verify_local_bound_run_source(
                Request(), SimpleNamespace(headers={}))
        assert error.value.status_code == 403
        assert source.call_count == 1
        attempted.assert_called_once()
        assert checks >= 3
    asyncio.run(exercise())


def test_receipt_journal_write_denied_when_session_revoked_inside_transaction(monkeypatch, tmp_path):
    """An in-flight operator review cannot persist after session revocation."""
    async def exercise():
        setup(monkeypatch, tmp_path)
        import hashlib
        from server import open_work_run_receipts
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                        case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
        monkeypatch.setattr(routes, "read_operation_evidence", lambda *a, **kw: [])
        monkeypatch.setattr(routes, "read_human_outcomes", lambda *a, **kw: [])
        payload = dict(work_item_ref=CASE, expected_revision=1, expected_edit_token="edit",
                       confirm_historical_review=True, confirm_run_comparison=True,
                       max_cases=1, deployment_id="dep_" + "a" * 16, run_id="run",
                       record_link={}, action="reviewed", confirm_receipt_write=True)
        monkeypatch.setattr(routes, "_body", AsyncMock(return_value=payload))
        revoked = False
        def session_check(_request):
            if revoked:
                raise HTTPException(401, "session revoked")
        monkeypatch.setattr(routes, "_dashboard_only", session_check)
        writes = []
        def append_after_revocation(**kwargs):
            nonlocal revoked
            revoked = True
            kwargs["guard"]()
            writes.append(kwargs)
            return {}
        monkeypatch.setattr(open_work_run_receipts, "append", append_after_revocation)
        with pytest.raises(HTTPException) as exc:
            await routes.bound_run_receipt_journal(Request(), SimpleNamespace(headers={}))
        assert exc.value.status_code == 401
        assert writes == []
    asyncio.run(exercise())


def test_receipt_journal_read_denied_if_session_revoked_before_response(monkeypatch, tmp_path):
    """A successful journal read must not disclose evidence after session revocation."""
    async def exercise():
        setup(monkeypatch, tmp_path)
        import hashlib
        from server import open_work_run_receipts
        actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
        ledger.bind_run(scope=SCOPE, actor=actor, secret="secret-at-least-16",
                        case_ref=CASE, run_ref=RUN, fingerprint=FP, revision=1)
        monkeypatch.setattr(routes, "read_operation_evidence", lambda *a, **kw: [])
        monkeypatch.setattr(routes, "read_human_outcomes", lambda *a, **kw: [])
        payload = dict(work_item_ref=CASE, expected_revision=1, expected_edit_token="edit",
                       confirm_historical_review=True, confirm_run_comparison=True,
                       max_cases=1, deployment_id="dep_" + "a" * 16, run_id="run",
                       record_link={})
        monkeypatch.setattr(routes, "_body", AsyncMock(return_value=payload))
        monkeypatch.setattr(open_work_run_receipts, "review",
                            lambda **kwargs: {"entries": [], "active_review_count": 0})
        revoked = False
        def session_check(_request):
            if revoked:
                raise HTTPException(401, "session revoked")
        monkeypatch.setattr(routes, "_dashboard_only", session_check)
        async def source_check(_scope):
            nonlocal revoked
            revoked = True
        monkeypatch.setattr(routes, "_recheck_source", source_check)
        with pytest.raises(HTTPException) as exc:
            await routes.bound_run_receipt_journal(Request(), SimpleNamespace(headers={}))
        assert exc.value.status_code == 401
    asyncio.run(exercise())
