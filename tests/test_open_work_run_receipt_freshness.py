"""Executable authenticated route tests for operator run-receipt freshness.

No external provider IO; real SQLite/HMAC receipt storage is used.
"""
import asyncio
import hashlib

import pytest
from fastapi import HTTPException
from starlette.responses import Response

from server import open_work_declaration_routes as routes
from server import open_work_run_receipts as receipts
from server import open_work_run_case_binding as bindings

SCOPE = "owg:scope:" + "a" * 32
CASE = "owg:w:" + "b" * 32
RUN = "owg:run:" + "c" * 32
SECRET = "freshness-test-secret"
FINGERPRINT = "d" * 64
SIGNATURE = "signature"


class Request:
    headers = {"authorization": "owg-session local-test-session"}


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(bindings, "read_policy", lambda: {
        "human_retention": {"mode": "days", "days": 3}})
    monkeypatch.setattr(routes, "_dashboard_only", lambda request: None)

    async def scope():
        return SCOPE

    async def source(_):
        return None

    payload = {
        "work_item_ref": CASE, "expected_revision": 1,
        "expected_edit_token": "edit", "confirm_historical_review": True,
        "confirm_run_comparison": True, "max_cases": 1,
        "deployment_id": "deployment", "run_id": "run",
        "record_link": "signed-link",
    }

    async def body(_request, _fields, optional=None):
        return dict(payload)

    async def triage(_scope, _payload):
        return ({"snapshot": 1}, {"work_item_ref": CASE}, SIGNATURE, SECRET)

    evidence = {"provider": [{"state": "open"}], "human": []}
    monkeypatch.setattr(routes, "_require_scope", scope)
    monkeypatch.setattr(routes, "_recheck_source", source)
    monkeypatch.setattr(routes, "_body", body)
    monkeypatch.setattr(routes, "_load_review_triage", triage)
    monkeypatch.setattr(routes, "validate_case_record_link",
                        lambda *args, **kwargs: "resource")
    monkeypatch.setattr(routes, "read_imported_run",
                        lambda **kwargs: {"run_ref": RUN,
                                          "run_snapshot_fingerprint": FINGERPRINT})
    monkeypatch.setattr(bindings, "get_binding",
                        lambda **kwargs: {"binding": "confirmed"})
    monkeypatch.setattr(routes, "_operation_ledger_guard",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "read_operation_evidence",
                        lambda *args, **kwargs: list(evidence["provider"]))
    monkeypatch.setattr(routes, "read_human_outcomes",
                        lambda *args, **kwargs: list(evidence["human"]))
    return payload, evidence


def invoke():
    return asyncio.run(routes.bound_run_receipt_journal(Request(), Response()))


def test_authenticated_route_marks_changed_case_evidence_stale(route_env):
    payload, evidence = route_env
    payload.update(action="reviewed", confirm_receipt_write=True)
    written = invoke()
    assert written["active_review_count"] == 1
    assert written["customer_outcome_verified"] is False
    payload.pop("action")
    payload.pop("confirm_receipt_write")
    current = invoke()
    assert current["current_review_count"] == 1
    assert current["entries"][0]["freshness"] == "current"
    evidence["provider"].append({"state": "paid"})
    stale = invoke()
    assert stale["current_review_count"] == 0
    assert stale["entries"][0]["freshness"] == "stale"
    assert stale["case_evidence_scope"] == "case_wide_not_run_causal"
    assert stale["customer_outcome_verified"] is False


def test_route_correction_retraction_and_explicit_write_confirmation(route_env):
    payload, evidence = route_env
    payload.update(action="reviewed")
    with pytest.raises(HTTPException) as denied:
        invoke()
    assert denied.value.status_code == 403
    payload["confirm_receipt_write"] = True
    first = invoke()
    first_id = first["entries"][0]["id"]
    payload.update(action="disputed", supersedes=first_id)
    disputed = invoke()
    assert disputed["active_review_count"] == 1
    payload.update(action="retracted", supersedes=disputed["entries"][-1]["id"])
    retracted = invoke()
    assert retracted["active_review_count"] == 0
    payload.pop("action")
    payload.pop("supersedes")
    payload.pop("confirm_receipt_write")
    evidence["human"].append({"assessment": "corrected"})
    history = invoke()
    assert len(history["entries"]) == 3
    assert all(e["freshness"] == "stale" for e in history["entries"])
    assert history["current_review_count"] == 0


def test_route_rejects_evidence_change_between_reads(route_env, monkeypatch):
    payload, evidence = route_env
    payload.update(action="reviewed", confirm_receipt_write=True)
    calls = 0

    def changing_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls >= 2:
            return [{"state": "changed"}]
        return [{"state": "open"}]

    monkeypatch.setattr(routes, "read_operation_evidence", changing_read)
    with pytest.raises(HTTPException) as error:
        invoke()
    assert error.value.status_code == 409
    actor = hashlib.sha256(Request.headers["authorization"].encode()).hexdigest()
    assert receipts.review(scope=SCOPE, actor=actor, secret=SECRET,
                           case_ref=CASE, run_ref=RUN,
                           fingerprint=FINGERPRINT, revision=1)["entries"] == []
