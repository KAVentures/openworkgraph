"""End-to-end route authorization boundary for local attempt receipt inspection."""
import asyncio
import hashlib
from datetime import timedelta

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from server import open_work_declaration_routes as routes


SCOPE = "owg:scope:" + "a" * 32
CASE = "owg:w:" + "b" * 32
RUN = "owg:run:" + "c" * 32
PAYLOAD = {
    "work_item_ref": CASE, "expected_revision": 1,
    "expected_edit_token": "review", "confirm_historical_review": True,
    "confirm_run_comparison": True, "max_cases": 1,
    "deployment_id": "deployment", "run_id": "run",
}


def request(token="valid"):
    return Request({"type": "http", "method": "POST",
                    "path": "/v1/open-work/declarations/human-outcomes/attempt-receipts",
                    "headers": [(b"authorization", ("OWG-Session " + token).encode())]})


def setup(monkeypatch, tmp_path):
    from server import local_auth
    from server import open_work_run_case_binding as bindings
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "valid")
    monkeypatch.setattr(routes, "_source_scope", lambda: SCOPE)
    async def body(*_args, **_kwargs):
        return dict(PAYLOAD)
    async def triage(*_args, **_kwargs):
        return ({"state": "reviewed"}, {"work_item_ref": CASE}, "signature", "secret" * 8)
    async def recheck(_scope):
        return None
    monkeypatch.setattr(routes, "_body", body)
    monkeypatch.setattr(routes, "_load_review_triage", triage)
    monkeypatch.setattr(routes, "_recheck_source", recheck)
    monkeypatch.setattr(routes, "_operation_ledger_guard", lambda *_args: None)
    monkeypatch.setattr(routes, "read_imported_run", lambda **_kwargs: {
        "run_ref": RUN, "run_snapshot_fingerprint": "d" * 64})
    monkeypatch.setattr(local_auth, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(bindings, "_retention", lambda: timedelta(days=7))
    monkeypatch.setattr(bindings, "require_exclusive_run_case_binding", lambda **_kwargs: None)
    return bindings


def test_rejects_unauthenticated_request_before_source_or_journal(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.read_local_bound_attempt_receipts(request("revoked"), Response()))
    assert error.value.status_code == 403
    assert not list(tmp_path.iterdir())


def test_requires_current_operator_binding_before_read(monkeypatch, tmp_path):
    bindings = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(bindings, "get_binding", lambda **_kwargs: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.read_local_bound_attempt_receipts(request(), Response()))
    assert error.value.status_code == 403
    assert not list(tmp_path.iterdir())


def test_bound_session_reads_only_own_untrusted_receipts(monkeypatch, tmp_path):
    from server.open_work_attempt_receipts import append_receipt
    bindings = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(bindings, "get_binding", lambda **_kwargs: {"association": "operator_confirmed"})
    path = str(tmp_path / ".open_work_attempt_receipts.sqlite3")
    actor = hashlib.sha256(b"OWG-Session valid").hexdigest()
    unknown = {"status": "unknown", "provider_operation": "unknown",
               "customer_outcome": "unknown"}
    append_receipt(path, tenant=SCOPE, actor=actor, attempt=RUN, verdict=unknown)
    append_receipt(path, tenant=SCOPE, actor="other_actor", attempt=RUN, verdict=unknown)
    append_receipt(path, tenant="owg:scope:" + "f" * 32,
                   actor=actor, attempt=RUN, verdict=unknown)
    result = asyncio.run(routes.read_local_bound_attempt_receipts(request(), Response()))
    assert len(result["entries"]) == 1
    assert result["receipt_provenance"] == "local_untrusted_journal"
    assert result["customer_outcome_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["source_read_performed"] is False


def test_binding_revoked_during_read_fails_closed(monkeypatch, tmp_path):
    bindings = setup(monkeypatch, tmp_path)
    calls = iter([{"association": "operator_confirmed"}, None])
    monkeypatch.setattr(bindings, "get_binding", lambda **_kwargs: next(calls))
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.read_local_bound_attempt_receipts(request(), Response()))
    assert error.value.status_code == 409


def test_run_only_receipts_refuse_ambiguous_case_attribution(monkeypatch, tmp_path):
    """A run linked to two cases cannot identify which case its receipt belongs to."""
    from server import open_work_run_case_binding as bindings
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(bindings, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(bindings, "_retention", lambda: timedelta(days=7))
    actor = hashlib.sha256(b"OWG-Session valid").hexdigest()
    fields = dict(scope=SCOPE, actor=actor, secret="secret" * 8,
                  run_ref=RUN, fingerprint="d" * 64, revision=1)
    bindings.bind_run(**fields, case_ref=CASE)
    bindings.require_exclusive_run_case_binding(**fields, case_ref=CASE)
    other_case = "owg:w:" + "e" * 32
    bindings.bind_run(**fields, case_ref=other_case)
    from server.open_work_case_declarations import CaseDeclarationError
    with pytest.raises(CaseDeclarationError, match="ambiguous"):
        bindings.require_exclusive_run_case_binding(**fields, case_ref=CASE)
    bindings.revoke_binding(scope=SCOPE, actor=actor, secret=fields["secret"],
                            case_ref=other_case, run_ref=RUN)
    bindings.require_exclusive_run_case_binding(**fields, case_ref=CASE)


def test_ambiguous_run_case_receipt_read_denied(monkeypatch, tmp_path):
    bindings = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(bindings, "get_binding", lambda **_kwargs: {"association": "operator_confirmed"})
    from server.open_work_case_declarations import CaseDeclarationError
    def ambiguous(**_kwargs):
        raise CaseDeclarationError("ambiguous or stale run/case receipt attribution")
    monkeypatch.setattr(bindings, "require_exclusive_run_case_binding", ambiguous)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.read_local_bound_attempt_receipts(request(), Response()))
    assert error.value.status_code in (403, 409)
