"""End-to-end local REST/SQLite acceptance for portable outcome review packets.

Synthetic provider data verifies integration and refusal behavior, not a live
Stripe receipt, independently adjudicated customer outcome, or commercial lift.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server import open_work_case_review_packet as packets
from server.local_auth import create_dashboard_session
from server.secure_app import app

A, B = ("owg:w:" + c * 32 for c in "ab")
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", lambda: policy)
    monkeypatch.setattr(reviews, "read_policy", lambda: policy)
    account = {"id": "acct_PRIVATE"}
    monkeypatch.setattr(
        routes, "_read_metadata",
        lambda _path: {"account": account["id"]} if account["id"] else None,
    )
    monkeypatch.setattr(
        routes, "_read_or_create_secret",
        lambda _name: "packet-local-secret-" + "x" * 40,
    )
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _cases(client, headers):
    saved = {}
    for ref, deps in ((A, []), (B, [A])):
        response = client.post(BASE, headers=headers, json={
            "confirm_owner_declaration": True, "work_item_ref": ref,
            "goal_code": "verify_operation",
            "expected_outcome_code": "operation_receipt_reviewed",
            "depends_on_work_item_refs": deps,
            "expected_revision": 0, "expected_edit_token": None,
        })
        assert response.status_code == 200, response.text
        saved[ref] = response.json()
    return saved


def _cas(saved, ref=B):
    return {
        "work_item_ref": ref, "expected_revision": saved[ref]["revision"],
        "expected_edit_token": saved[ref]["edit_token"],
    }


def _issue(client, headers, saved, count=2):
    return client.post(BASE + "/human-outcomes/review-packet", headers=headers,
                       json={**_cas(saved), "confirm_historical_review": True,
                             "max_cases": count})


def _verify(client, headers, saved, packet):
    return client.post(BASE + "/human-outcomes/verify-review-packet",
                       headers=headers, json={**_cas(saved), "packet": packet})


def _scan(ref, **_kwargs):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref, "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe", "source_state": "open",
            "status_scope": "stripe_invoice_open", "inventory_complete": False,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "native_id": "in_PRIVATE",
        },
        "related_owg_evidence": [{
            "resource_ref": R, "observed_event_count": 2,
            "observed_session_count": 1, "observed_actor_count": 1,
            "timeline": [{"customer": "PRIVATE@example.invalid"}],
        }],
        "related_record_history_incomplete": False,
        "candidate": {"customer": "PRIVATE", "amount": 123456},
    }


def _paid(ref, operation):
    assert (ref, operation) == (R, "stripe_invoice_paid")
    return {
        "format": "openworkgraph.source-operation-receipt.v1",
        "resource_ref": R, "expected_operation": operation,
        "availability": "source_reported", "operation_verified": True,
        "provider": "stripe", "provider_record_kind": "invoice",
        "provider_reported_state": "paid",
        "verification_scope": "authenticated_source_provider_operational_state",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "business_objective_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False, "read_only": True,
        "native_id": "in_PRIVATE",
    }


def _record_paid(client, headers, saved, monkeypatch):
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    link = client.post(BASE + "/operation-link", headers=headers, json={
        **_cas(saved), "confirm_source_scan": True,
        "max_pages": 2, "resource_ref": R,
    })
    assert link.status_code == 200, link.text
    monkeypatch.setattr(routes, "source_operation_receipt", _paid)
    check = client.post(BASE + "/operation-ledger/check", headers=headers, json={
        **_cas(saved), "confirm_source_verification": True,
        "record_link": link.json()["record_link"],
    })
    assert check.status_code == 200, check.text


def test_packet_issue_verify_and_corrected_histories(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assessed = client.post(BASE + "/human-outcomes/assess", headers=headers,
            json={**_cas(saved), "confirm_human_assessment": True,
                  "human_status": "met", "reason_code": "owner_review"})
        assert assessed.status_code == 200, assessed.text
        _record_paid(client, headers, saved, monkeypatch)

        def forbid(*_args, **_kwargs):
            raise AssertionError("review packet must never contact provider")
        monkeypatch.setattr(routes, "stripe_open_work_case_context", forbid)
        monkeypatch.setattr(routes, "source_operation_receipt", forbid)
        issued = _issue(client, headers, saved)
        assert issued.status_code == 200, issued.text
        data = issued.json()
        packet = data["packet"]
        assert len(packet["selected_case_refs"]) == 2
        assert packet["unscanned_prerequisite_count"] == 0
        assert packet["revision"] == saved[B]["revision"]
        assert data["customer_outcome_verified"] is False
        assert data["source_read_performed"] is False
        assert "PRIVATE" not in issued.text and "in_PRIVATE" not in issued.text
        assert issued.headers["cache-control"] == "no-store"

        first = _verify(client, headers, saved, packet)
        assert first.status_code == 200, first.text
        assert first.json()["comparison"] == "unchanged"
        assert first.json()["changed_case_refs"] == []

        correction = client.post(BASE + "/human-outcomes/correct", headers=headers,
            json={**_cas(saved), "confirm_human_correction": True,
                  "target_sequence": 1, "human_status": "not_met"})
        assert correction.status_code == 200, correction.text
        second = _verify(client, headers, saved, packet)
        assert second.status_code == 200, second.text
        assert second.json()["comparison"] == "changed"
        assert second.json()["changed_case_refs"] == [B]
        assert second.json()["review_summary"]["case_reviews"][-1]["latest_human_assessed_status"] == "not_met"
        assert second.json()["customer_outcome_verified"] is False

        fresh = _issue(client, headers, saved).json()["packet"]
        dispute = client.post(BASE + "/operation-ledger/dispute", headers=headers,
            json={**_cas(saved), "confirm_dispute": True,
                  "target_sequence": 1, "reason_code": "operator_dispute"})
        assert dispute.status_code == 200, dispute.text
        third = _verify(client, headers, saved, fresh)
        assert third.status_code == 200, third.text
        assert third.json()["changed_case_refs"] == [B]
        assert third.json()["review_summary"]["case_reviews"][-1]["historical_last_provider_state"] == "unknown"


def test_packet_forgery_expiry_and_revocation_fail_before_ledger_reads(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        issued = _issue(client, headers, saved)
        assert issued.status_code == 200, issued.text
        packet = issued.json()["packet"]
        forged = deepcopy(packet)
        forged["triage_digest"] = "0" * 64

        def forbid(*_args, **_kwargs):
            raise AssertionError("invalid packet must refuse before ledger reads")
        monkeypatch.setattr(routes, "read_operation_evidence", forbid)
        monkeypatch.setattr(routes, "read_human_outcomes", forbid)
        assert _verify(client, headers, saved, forged).status_code in (409, 422)
        assert _verify(client, headers, saved, {**packet, "extra": True}).status_code in (409, 422)

        now = datetime.now(timezone.utc)
        with monkeypatch.context() as patch:
            patch.setattr(packets, "_clock", lambda _now: now + timedelta(hours=2))
            assert _verify(client, headers, saved, packet).status_code in (409, 422)

        tampered_case = deepcopy(packet)
        tampered_case["case_digests"][0] = "f" * 64
        assert _verify(client, headers, saved, tampered_case).status_code in (409, 422)
        declarations.clear_case_declarations()
        assert _verify(client, headers, saved, packet).status_code in (403, 409, 422)


def test_packet_partial_coverage_auth_and_source_revocation(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        issued = _issue(client, headers, saved, count=1)
        assert issued.status_code == 200, issued.text
        packet = issued.json()["packet"]
        assert packet["selected_case_refs"] == [B]
        assert packet["unscanned_prerequisite_count"] == 1
        assert _verify(client, headers, saved, packet).json()["comparison"] == "unchanged"
        assert client.post(BASE + "/human-outcomes/verify-review-packet",
                           json={**_cas(saved), "packet": packet}).status_code in (401, 403)
        assert client.post(BASE + "/human-outcomes/review-packet", headers=headers,
            json={**_cas(saved), "confirm_historical_review": False,
                  "max_cases": 1}).status_code == 403
        local["id"] = None
        assert _verify(client, headers, saved, packet).status_code in (403, 409)
