"""FastAPI/private SQLite review triage of distinct human/provider outcome evidence.

Synthetic source adapters validate contracts, NOT real Stripe/customer outcomes.
"""
from datetime import datetime, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server import open_work_case_outcome_reviews as human
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
        lambda _name: "triage-local-secret-" + "x" * 40,
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
        "work_item_ref": ref,
        "expected_revision": saved[ref]["revision"],
        "expected_edit_token": saved[ref]["edit_token"],
    }


def _triage(client, headers, saved, count=2):
    return client.post(BASE + "/human-outcomes/evidence-triage", headers=headers,
                       json={**_cas(saved), "confirm_historical_review": True,
                             "max_cases": count})


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


def _assess(client, headers, saved, status="met"):
    result = client.post(BASE + "/human-outcomes/assess", headers=headers, json={
        **_cas(saved), "confirm_human_assessment": True,
        "human_status": status, "reason_code": "owner_review",
    })
    assert result.status_code == 200, result.text


def test_triage_joins_real_local_ledgers_without_provider_io(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        _assess(client, headers, saved)
        _record_paid(client, headers, saved, monkeypatch)
        def forbid(*args, **kwargs):
            raise AssertionError("historical triage must not contact a provider")
        monkeypatch.setattr(routes, "source_operation_receipt", forbid)
        monkeypatch.setattr(routes, "stripe_open_work_case_context", forbid)
        response = _triage(client, headers, saved)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["format"] == "openworkgraph.local-reviewed-outcome-evidence-triage.v1"
        assert data["included_case_count"] == 2
        assert data["unscanned_prerequisite_count"] == 0
        assert data["selected_dependency_edges"] == [{
            "prerequisite_ref": A, "dependent_ref": B,
            "edge_type": "owner_declared_dependency", "source_verified": False,
        }]
        first, second = data["case_reviews"]
        assert first["work_item_ref"] == A
        assert set(first["review_attention"]) == {
            "human_assessment_missing", "provider_check_missing",
        }
        assert second["work_item_ref"] == B
        assert second["latest_human_assessed_status"] == "met"
        assert second["historical_last_provider_state"] == "paid"
        assert second["review_attention"] == []
        assert data["cases_with_review_attention"] == 1
        assert data["customer_outcome_verified"] is False
        assert data["expected_outcome_verified"] is False
        assert data["source_read_performed"] is False
        assert response.headers["cache-control"] == "no-store"
        assert "PRIVATE" not in response.text and "in_PRIVATE" not in response.text
        partial = _triage(client, headers, saved, 1)
        assert partial.status_code == 200, partial.text
        assert partial.json()["unscanned_prerequisite_count"] == 1
        assert partial.json()["all_declared_prerequisites_included"] is False


def test_dispute_and_human_correction_update_review_attention(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        _record_paid(client, headers, saved, monkeypatch)
        _assess(client, headers, saved, "met")
        disputed = client.post(BASE + "/operation-ledger/dispute", headers=headers, json={
            **_cas(saved), "confirm_dispute": True,
            "target_sequence": 1, "reason_code": "operator_dispute",
        })
        assert disputed.status_code == 200, disputed.text
        result = _triage(client, headers, saved).json()["case_reviews"][-1]
        assert result["historical_last_provider_state"] == "unknown"
        assert "provider_history_unknown_or_disputed" in result["review_attention"]
        assert "human_met_without_historical_paid_receipt" in result["review_attention"]
        corrected = client.post(BASE + "/human-outcomes/correct", headers=headers, json={
            **_cas(saved), "confirm_human_correction": True,
            "target_sequence": 1, "human_status": "not_met",
        })
        assert corrected.status_code == 200, corrected.text
        result = _triage(client, headers, saved).json()["case_reviews"][-1]
        assert result["latest_human_assessed_status"] == "not_met"
        assert result["human_assessment_count"] == 2
        assert "human_met_without_historical_paid_receipt" not in result["review_attention"]


def test_authorization_cas_and_confirmation_fail_closed(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        body = {**_cas(saved), "confirm_historical_review": True, "max_cases": 2}
        assert client.post(BASE + "/human-outcomes/evidence-triage", json=body).status_code in (401, 403)
        assert client.post(BASE + "/human-outcomes/evidence-triage", headers=headers,
                           json={**body, "confirm_historical_review": False}).status_code == 403
        assert client.post(BASE + "/human-outcomes/evidence-triage", headers=headers,
                           json={**body, "max_cases": 5}).status_code == 422
        assert client.post(BASE + "/human-outcomes/evidence-triage", headers=headers,
                           json={**body, "expected_revision": 999}).status_code == 409
        assert _triage(client, headers, saved).status_code == 200


def test_tampering_and_revocation_during_join_fail_closed(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        _assess(client, headers, saved)
        with sqlite3.connect(human._path()) as db:
            db.execute("UPDATE assessments SET human_status='not_met' WHERE work_item_ref=?", (B,))
        assert _triage(client, headers, saved).status_code in (409, 422)
        human.clear_human_outcome_reviews()
        _assess(client, headers, saved)
        original = routes.read_human_outcomes
        def revoke(*args, **kwargs):
            value = original(*args, **kwargs)
            declarations.clear_case_declarations()
            return value
        monkeypatch.setattr(routes, "read_human_outcomes", revoke)
        assert _triage(client, headers, saved).status_code in (403, 409, 422)
