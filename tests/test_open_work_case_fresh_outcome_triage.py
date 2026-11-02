"""Authenticated fresh Stripe receipt + retained human/provider outcome triage.

Real FastAPI/session/private SQLite integration with synthetic Stripe transport;
not live provider acceptance, independent customer adjudication or agent uplift.
"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.open_work_case_fresh_outcome_triage import compose_fresh_outcome_triage
from server.open_work_case_declarations import CaseDeclarationError
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
        lambda _name: "fresh-triage-local-secret-" + "x" * 40,
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


def _cas(saved):
    return {
        "work_item_ref": B, "expected_revision": saved[B]["revision"],
        "expected_edit_token": saved[B]["edit_token"],
    }


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


def _link(client, headers, saved, monkeypatch):
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    response = client.post(BASE + "/operation-link", headers=headers, json={
        **_cas(saved), "confirm_source_scan": True,
        "max_pages": 2, "resource_ref": R,
    })
    assert response.status_code == 200, response.text
    return response.json()["record_link"]


def _triage(client, headers, saved, link, *, count=2, confirm=True):
    return client.post(BASE + "/human-outcomes/fresh-evidence-triage",
        headers=headers, json={
            **_cas(saved), "confirm_historical_review": confirm,
            "confirm_source_verification": True, "record_link": link,
            "max_cases": count,
        })


def test_fresh_paid_is_root_only_and_does_not_verify_customer_goal(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        link = _link(client, headers, saved, monkeypatch)
        assessed = client.post(BASE + "/human-outcomes/assess",
            headers=headers, json={
                **_cas(saved), "confirm_human_assessment": True,
                "human_status": "met", "reason_code": "owner_review",
            })
        assert assessed.status_code == 200, assessed.text
        seen = []
        def provider(ref, operation):
            seen.append((ref, operation))
            return _paid(ref, operation)
        monkeypatch.setattr(routes, "source_operation_receipt", provider)
        response = _triage(client, headers, saved, link)
        assert response.status_code == 200, response.text
        data = response.json()
        assert seen == [(R, "stripe_invoice_paid")]
        assert data["fresh_root_provider"]["provider_operation_state"] == "paid"
        assert data["fresh_root_provider"]["provider_operation_verified"] is True
        assert data["fresh_root_provider"]["human_assessed_status"] == "met"
        assert data["fresh_root_provider"]["historical_last_provider_state"] == "unknown"
        assert "fresh_paid_differs_from_retained_provider_history" in data["fresh_root_provider"]["review_attention"]
        assert data["historical_review"]["case_reviews"][0]["work_item_ref"] == A
        assert data["historical_review"]["case_reviews"][-1]["work_item_ref"] == B
        assert data["historical_review"]["all_declared_prerequisites_included"] is True
        assert data["all_prerequisites_freshly_checked"] is False
        assert data["current_provider_operation_verified"] is True
        assert data["customer_outcome_verified"] is False
        assert data["expected_outcome_verified"] is False
        assert data["agent_caused_outcome_verified"] is False
        assert data["source_read_attempted"] is True
        assert data["server_side_fresh_result_persisted"] is False
        assert response.headers["cache-control"] == "no-store"
        assert "PRIVATE" not in response.text and "in_PRIVATE" not in response.text
        partial = _triage(client, headers, saved, link, count=1)
        assert partial.status_code == 200, partial.text
        assert partial.json()["unscanned_prerequisite_count"] == 1
        assert len(seen) == 2


def test_historical_paid_with_fresh_source_outage_stays_unknown(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        link = _link(client, headers, saved, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        check = client.post(BASE + "/operation-ledger/check",
            headers=headers, json={
                **_cas(saved), "confirm_source_verification": True,
                "record_link": link,
            })
        assert check.status_code == 200, check.text
        monkeypatch.setattr(routes, "source_operation_receipt", lambda *_args: None)
        response = _triage(client, headers, saved, link)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["historical_review"]["case_reviews"][-1]["historical_last_provider_state"] == "paid"
        assert data["fresh_root_provider"]["provider_operation_state"] == "unknown"
        assert data["current_provider_operation_verified"] is False
        assert "historical_paid_not_freshly_reconfirmed" in data["fresh_root_provider"]["review_attention"]
        assert data["customer_outcome_verified"] is False


def test_invalid_link_and_confirmation_refuse_before_source_io(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        link = _link(client, headers, saved, monkeypatch)
        seen = []
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_args: seen.append(True))
        forged = deepcopy(link)
        forged["seal"] = "0" * 64
        assert _triage(client, headers, saved, forged).status_code in (403, 409, 422)
        assert _triage(client, headers, saved, link, confirm=False).status_code == 403
        assert _triage(client, headers, saved, link, count=5).status_code == 422
        assert _triage(client, headers, saved, link, count=True).status_code == 422
        assert client.post(BASE + "/human-outcomes/fresh-evidence-triage",
            json={**_cas(saved), "confirm_historical_review": True,
                  "confirm_source_verification": True, "record_link": link,
                  "max_cases": 2}).status_code in (401, 403)
        assert not seen


def test_mid_source_read_revocation_or_history_change_fails_closed(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        link = _link(client, headers, saved, monkeypatch)
        def mutate(*_args):
            declarations.clear_case_declarations()
            return _paid(R, "stripe_invoice_paid")
        monkeypatch.setattr(routes, "source_operation_receipt", mutate)
        assert _triage(client, headers, saved, link).status_code in (403, 409, 422)
        saved = _cases(client, headers)
        link = _link(client, headers, saved, monkeypatch)
        def alter(*_args):
            # The human assessment ledger changes while the provider is read.
            # A combined non-atomic snapshot must be rejected, not mixed.
            from server.open_work_case_outcome_reviews import clear_human_outcome_reviews
            clear_human_outcome_reviews()
            return _paid(R, "stripe_invoice_paid")
        assessed = client.post(BASE + "/human-outcomes/assess",
            headers=headers, json={
                **_cas(saved), "confirm_human_assessment": True,
                "human_status": "met", "reason_code": "owner_review",
            })
        assert assessed.status_code == 200, assessed.text
        monkeypatch.setattr(routes, "source_operation_receipt", alter)
        assert _triage(client, headers, saved, link).status_code in (403, 409, 422)


def test_projection_rejects_fabricated_provider_success():
    history = {
        "format": "openworkgraph.local-reviewed-outcome-evidence-triage.v1",
        "work_item_ref": B, "included_case_count": 1,
        "case_reviews": [{
            "work_item_ref": B, "latest_human_assessed_status": "met",
            "historical_last_provider_state": "unknown",
        }],
        "source_read_performed": False, "expected_outcome_verified": False,
        "customer_outcome_verified": False, "unscanned_prerequisite_count": 0,
    }
    verification = {
        "format": "openworkgraph.local-case-provider-operation-verification.v1",
        "work_item_ref": B, "provider_operation_state": "paid",
        "provider_operation_verified": False,
        "expected_outcome_verified": False, "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
    }
    with pytest.raises(CaseDeclarationError):
        compose_fresh_outcome_triage(history, verification)
