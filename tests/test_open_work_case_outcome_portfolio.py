"""Portfolio-wide historical outcome review using actual FastAPI and private ledgers.

Synthetic source transport only: not live Stripe, customer-outcome adjudication
or production cross-process tenant authorization.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session
from server.secure_app import app

A, B, C = ("owg:w:" + char * 32 for char in "abc")
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"
PORTFOLIO = BASE + "/human-outcomes/portfolio-triage"


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
        lambda _name: "portfolio-local-secret-" + "x" * 40,
    )
    for ref in (A, B, C):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _cases(client, headers):
    saved = {}
    for ref, goal, expected, deps in (
        (A, "verify_operation", "operation_receipt_reviewed", []),
        (B, "assess_declared_policy", "human_review_recorded", [A]),
        (C, "review_source_state", "source_state_rechecked", []),
    ):
        result = client.post(BASE, headers=headers, json={
            "confirm_owner_declaration": True,
            "work_item_ref": ref,
            "goal_code": goal, "expected_outcome_code": expected,
            "depends_on_work_item_refs": deps,
            "expected_revision": 0, "expected_edit_token": None,
        })
        assert result.status_code == 200, result.text
        saved[ref] = result.json()
    return saved


def _cas(saved, ref):
    return {
        "work_item_ref": ref,
        "expected_revision": saved[ref]["revision"],
        "expected_edit_token": saved[ref]["edit_token"],
    }


def _assess(client, headers, saved, ref, status):
    result = client.post(BASE + "/human-outcomes/assess", headers=headers, json={
        **_cas(saved, ref),
        "confirm_human_assessment": True,
        "human_status": status, "reason_code": "owner_review",
    })
    assert result.status_code == 200, result.text


def _page(client, headers, *, offset=0, max_cases=2, digest=None, confirm=True):
    return client.post(PORTFOLIO, headers=headers, json={
        "confirm_historical_review": confirm,
        "max_cases": max_cases, "offset": offset,
        "expected_portfolio_digest": digest,
    })


def _scan(ref, **_kwargs):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref, "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe", "source_state": "open",
            "status_scope": "stripe_invoice_open",
            "inventory_complete": False,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "native_id": "in_PRIVATE",
        },
        "related_owg_evidence": [{
            "resource_ref": R,
            "observed_event_count": 1, "observed_session_count": 1,
            "observed_actor_count": 1, "timeline": [],
        }],
        "related_record_history_incomplete": False,
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


def test_portfolio_pages_are_reviewable_and_payment_is_goal_specific(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        _assess(client, headers, saved, A, "met")
        _assess(client, headers, saved, B, "met")
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_args: pytest.fail("portfolio must not contact Stripe"))
        first = _page(client, headers)
        assert first.status_code == 200, first.text
        data = first.json()
        assert data["total_reviewed_cases"] == 3
        assert data["included_case_count"] == 2
        assert data["next_offset"] == 2
        assert [x["work_item_ref"] for x in data["cases"]] == [A, B]
        assert "historical_operation_receipt_missing" in data["cases"][0]["review_attention"]
        assert "historical_operation_receipt_missing" not in data["cases"][1]["review_attention"]
        assert data["cases"][1]["declared_prerequisite_count"] == 1
        assert data["cases"][1]["review_attention"] == []
        second = _page(client, headers, offset=2, digest=data["portfolio_digest"])
        assert second.status_code == 200, second.text
        assert second.json()["next_offset"] is None
        assert second.json()["cases"][0]["work_item_ref"] == C
        assert second.json()["cases"][0]["review_attention"] == ["human_assessment_missing"]
        assert second.json()["customer_outcome_verified"] is False
        assert second.json()["current_provider_operation_verified"] is False
        assert second.json()["source_read_performed"] is False
        assert second.headers["cache-control"] == "no-store"
        assert "acct_PRIVATE" not in first.text
        assert "in_PRIVATE" not in first.text


def test_actual_source_checked_sqlite_receipt_is_historical_not_customer_success(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        _assess(client, headers, saved, A, "met")
        monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
        link = client.post(BASE + "/operation-link", headers=headers, json={
            **_cas(saved, A), "confirm_source_scan": True,
            "max_pages": 2, "resource_ref": R,
        })
        assert link.status_code == 200, link.text
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        checked = client.post(BASE + "/operation-ledger/check", headers=headers, json={
            **_cas(saved, A), "confirm_source_verification": True,
            "record_link": link.json()["record_link"],
        })
        assert checked.status_code == 200, checked.text
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_args: pytest.fail("historical read must not call source"))
        page = _page(client, headers, max_cases=3)
        assert page.status_code == 200, page.text
        a = page.json()["cases"][0]
        assert a["historical_last_provider_state"] == "paid"
        assert a["provider_check_count"] == 1
        assert a["review_attention"] == []
        assert a["expected_outcome_verified"] is False
        assert page.json()["customer_outcome_verified"] is False
        assert page.json()["historical_only_requires_fresh_source_recheck"] is True
        assert "in_PRIVATE" not in page.text


def test_portfolio_token_refuses_stale_pages_and_reapproval(local):
    with TestClient(app) as client:
        headers = _auth()
        _cases(client, headers)
        first = _page(client, headers)
        assert first.status_code == 200, first.text
        digest = first.json()["portfolio_digest"]
        assert _page(client, headers, offset=2).status_code in (409, 422)
        assert _page(client, headers, offset=2, digest="0" * 64).status_code in (409, 422)
        # Revoking owner declarations cannot leave the previous portfolio
        # pagination token authoritative, even if reviews remain available.
        declarations.clear_case_declarations()
        stale = _page(client, headers, offset=2, digest=digest)
        assert stale.status_code in (403, 409, 422)
        assert _page(client, headers, max_cases=2).json()["total_reviewed_cases"] == 0


def test_auth_confirmation_scope_and_mid_read_revocation(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _cases(client, headers)
        assert _page(client, headers, confirm=False).status_code == 403
        assert _page(client, headers, max_cases=11).status_code == 422
        assert _page(client, headers, max_cases=True).status_code == 422
        assert _page(client, headers, offset=-1).status_code == 422
        assert _page(client, {}).status_code in (401, 403)
        original = routes.read_human_outcomes
        calls = []
        def revoke(*args, **kwargs):
            calls.append(True)
            declarations.clear_case_declarations()
            return original(*args, **kwargs)
        monkeypatch.setattr(routes, "read_human_outcomes", revoke)
        denied = _page(client, headers)
        assert denied.status_code in (403, 409, 422)
        assert calls
