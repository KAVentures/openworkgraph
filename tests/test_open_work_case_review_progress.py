"""Authenticated local REST + real private SQLite review progress acceptance.

Provider reads are synthetic. These tests never certify real Stripe/customer success.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server import open_work_case_review_packet as review_packet
from server.local_auth import create_dashboard_session
from server.secure_app import app

A, B = ("owg:w:" + char * 32 for char in "ab")
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"
ISSUE = BASE + "/human-outcomes/progress-checkpoint"
COMPARE = BASE + "/human-outcomes/progress-compare"


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
        lambda _name: "review-progress-test-secret-" + "x" * 40,
    )
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _setup(client, headers):
    saved = {}
    for ref, deps, outcome in (
        (A, [], "stripe_invoice_paid_reported"),
        (B, [A], "operation_receipt_reviewed"),
    ):
        response = client.post(BASE, headers=headers, json={
            "confirm_owner_declaration": True, "work_item_ref": ref,
            "goal_code": "verify_operation", "expected_outcome_code": outcome,
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


def _issue(client, headers, saved, max_cases=2):
    return client.post(ISSUE, headers=headers, json={
        **_cas(saved), "confirm_historical_review": True, "max_cases": max_cases,
    })


def _compare(client, headers, saved, checkpoint):
    return client.post(COMPARE, headers=headers, json={
        **_cas(saved), "checkpoint": checkpoint,
    })


def _paid_scan(ref, **_kwargs):
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
    }


def _paid_receipt(ref, operation):
    assert (ref, operation) == (R, "stripe_invoice_paid")
    return {
        "format": "openworkgraph.source-operation-receipt.v1",
        "resource_ref": R, "expected_operation": operation,
        "availability": "source_reported", "operation_verified": True,
        "provider": "stripe", "provider_record_kind": "invoice",
        "provider_reported_state": "paid",
        "verification_scope": "authenticated_source_provider_operational_state",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "business_objective_verified": False, "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False, "read_only": True,
        "native_id": "in_PRIVATE",
    }


def _verify_paid(client, headers, saved, monkeypatch):
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _paid_scan)
    link = client.post(BASE + "/operation-link", headers=headers, json={
        **_cas(saved, A), "confirm_source_scan": True,
        "max_pages": 2, "resource_ref": R,
    })
    assert link.status_code == 200, link.text
    monkeypatch.setattr(routes, "source_operation_receipt", _paid_receipt)
    checked = client.post(BASE + "/source-goals/verify-payment", headers=headers, json={
        **_cas(saved, A), "confirm_source_verification": True,
        "record_link": link.json()["record_link"],
    })
    assert checked.status_code == 200, checked.text
    return checked


def test_progress_baseline_unchanged_and_no_source_reads(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        monkeypatch.setattr(
            routes, "source_operation_receipt",
            lambda *_: pytest.fail("historical progress must not read provider"),
        )
        monkeypatch.setattr(
            routes, "stripe_open_work_case_context",
            lambda *_a, **_k: pytest.fail("historical progress must not scan provider"),
        )
        baseline = _issue(client, headers, saved)
        assert baseline.status_code == 200, baseline.text
        checkpoint = baseline.json()["checkpoint"]
        assert checkpoint["format"] == "openworkgraph.local-outcome-review-progress-checkpoint.v1"
        assert len(checkpoint["case_states"]) == 2
        result = _compare(client, headers, saved, checkpoint)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["comparison"] == "unchanged"
        assert [row["work_item_ref"] for row in data["cases"]] == [A, B]
        assert all(row["new_human_assessments"] == 0 for row in data["cases"])
        assert all(row["new_provider_checks"] == 0 for row in data["cases"])
        assert data["customer_outcome_verified"] is False
        assert data["source_read_performed"] is False
        assert data["agent_execution_authorized"] is False
        assert result.headers["cache-control"] == "no-store"
        for sensitive in (R, "in_PRIVATE", "PRIVATE@example.invalid", "acct_PRIVATE", "record_binding"):
            assert sensitive not in result.text
            assert sensitive not in baseline.text


def test_progress_reports_actual_human_and_provider_history_changes(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        checkpoint = _issue(client, headers, saved).json()["checkpoint"]
        assessment = client.post(BASE + "/human-outcomes/assess", headers=headers, json={
            **_cas(saved, A), "confirm_human_assessment": True,
            "human_status": "met", "reason_code": "owner_review",
        })
        assert assessment.status_code == 200, assessment.text
        _verify_paid(client, headers, saved, monkeypatch)
        monkeypatch.setattr(
            routes, "source_operation_receipt",
            lambda *_: pytest.fail("comparison must never reread provider"),
        )
        result = _compare(client, headers, saved, checkpoint)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["comparison"] == "changed"
        a, b = data["cases"]
        assert a["new_human_assessments"] == 1
        assert a["new_provider_checks"] == 1
        assert a["new_provider_disputes"] == 0
        assert a["human_status_before"] == "unknown"
        assert a["human_status_after"] == "met"
        assert a["historical_provider_state_after"] == "paid"
        assert "record_human_assessment" in a["resolved_review_action_codes"]
        assert "obtain_signed_invoice_link" in a["resolved_review_action_codes"]
        assert "fresh_declared_payment_check" in data["review_action_plan"]["cases"][0]["operator_action_codes"]
        assert b["prerequisite_review_pending_after"] is False
        assert data["current_provider_operation_verified"] is False
        assert data["customer_outcome_verified"] is False


def test_progress_reports_dispute_and_never_promotes_paid_to_customer_success(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        _verify_paid(client, headers, saved, monkeypatch)
        checkpoint = _issue(client, headers, saved).json()["checkpoint"]
        history = client.post(BASE + "/operation-ledger/history", headers=headers, json=_cas(saved, A))
        assert history.status_code == 200, history.text
        target = history.json()["events"][-1]["sequence"]
        dispute = client.post(BASE + "/operation-ledger/dispute", headers=headers, json={
            **_cas(saved, A), "confirm_dispute": True,
            "target_sequence": target, "reason_code": "operator_dispute",
        })
        assert dispute.status_code == 200, dispute.text
        result = _compare(client, headers, saved, checkpoint)
        assert result.status_code == 200, result.text
        row = result.json()["cases"][0]
        assert row["new_provider_disputes"] == 1
        assert row["historical_provider_state_before"] == "paid"
        assert row["historical_provider_state_after"] == "unknown"
        assert row["customer_outcome_verified"] is False


def test_forged_checkpoint_refused_before_ledger_reads(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        checkpoint = _issue(client, headers, saved).json()["checkpoint"]
        checkpoint["case_states"][0]["operator_action_codes"] = []
        monkeypatch.setattr(
            routes, "read_operation_evidence",
            lambda *_a, **_k: pytest.fail("forged checkpoint must fail before ledger IO"),
        )
        monkeypatch.setattr(
            routes, "read_human_outcomes",
            lambda *_a, **_k: pytest.fail("forged checkpoint must fail before ledger IO"),
        )
        result = _compare(client, headers, saved, checkpoint)
        assert result.status_code in (409, 422), result.text


def test_expired_and_source_revoked_checkpoint_refused(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        checkpoint = _issue(client, headers, saved).json()["checkpoint"]
        monkeypatch.setattr(
            review_packet, "_clock",
            lambda _now: datetime.now(timezone.utc) + timedelta(hours=2),
        )
        expired = _compare(client, headers, saved, checkpoint)
        assert expired.status_code in (409, 422), expired.text


def test_account_switch_revokes_checkpoint(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        checkpoint = _issue(client, headers, saved).json()["checkpoint"]
        local["id"] = "acct_DIFFERENT"
        assert _compare(client, headers, saved, checkpoint).status_code in (403, 409, 422)
        local["id"] = None
        assert _compare(client, headers, saved, checkpoint).status_code == 403


def test_partial_dependency_and_authentication(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _setup(client, headers)
        payload = {**_cas(saved), "confirm_historical_review": True, "max_cases": 1}
        assert client.post(ISSUE, json=payload).status_code == 401  # no credentials
        baseline = _issue(client, headers, saved, max_cases=1)
        assert baseline.status_code == 200, baseline.text
        assert len(baseline.json()["checkpoint"]["case_states"]) == 1
        compared = _compare(client, headers, saved, baseline.json()["checkpoint"])
        assert compared.status_code == 200, compared.text
        assert compared.json()["unscanned_prerequisite_count"] == 1
        assert compared.json()["review_action_plan"]["cases"][0]["prerequisite_review_pending"] is True
        assert client.post(COMPARE, json={
            **_cas(saved), "checkpoint": baseline.json()["checkpoint"],
        }).status_code == 401  # no credentials
