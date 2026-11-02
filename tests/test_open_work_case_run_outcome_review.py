"""FastAPI/private-store acceptance for selected imported run versus case outcome evidence.

Synthetic reports and Stripe account metadata only. No run-to-case identity,
independent customer completion, or causal attribution is claimed.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import automation_monitoring as monitoring
from server import open_work_case_declarations as declarations
from server import open_work_case_run_outcome_review as review
from server import open_work_case_operation_ledger as operation_ledger
from server import open_work_declaration_routes as routes
from server import open_work_reviews as reviews
from server.local_auth import create_dashboard_session
from server.secure_app import app

CASE = "owg:w:" + "a" * 32
BASE = "/v1/open-work/declarations"
ENDPOINT = BASE + "/human-outcomes/compare-imported-run"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.setattr(monitoring, "DATA_DIR", tmp_path)
    policy = {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", lambda: policy)
    monkeypatch.setattr(reviews, "read_policy", lambda: policy)
    account = {"id": "acct_PRIVATE"}
    monkeypatch.setattr(routes, "_read_metadata", lambda _p: {"account": account["id"]} if account["id"] else None)
    monkeypatch.setattr(routes, "_read_or_create_secret", lambda _n: "test-run-outcome-" + "x" * 48)
    reviews.save_review_intention(
        CASE, "assess_follow_up", provider_candidate_currently_open=True,
        now=datetime.now(timezone.utc),
    )
    return account, policy


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _case(client, headers):
    response = client.post(BASE, headers=headers, json={
        "confirm_owner_declaration": True, "work_item_ref": CASE,
        "goal_code": "verify_operation",
        "expected_outcome_code": "stripe_invoice_paid_reported",
        "depends_on_work_item_refs": [],
        "expected_revision": 0, "expected_edit_token": None,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _run(status="succeeded", verified=True, finished="2026-11-02T09:05:00Z"):
    return {
        "format": monitoring.FORMAT,
        "deployment": {"name": "Private synthetic", "runtime": "ai-sdk"},
        "runs": [{
            "run_id": "private-customer-run-id",
            "started_at": "2026-11-02T09:00:00Z",
            "finished_at": finished, "status": status,
            "outcome": {"verified": verified, "source": "sensitive-private-label"},
            "steps": [],
        }],
    }


def _payload(saved, deployment_id):
    return {
        "work_item_ref": CASE,
        "expected_revision": saved["revision"],
        "expected_edit_token": saved["edit_token"],
        "confirm_historical_review": True,
        "confirm_run_comparison": True,
        "max_cases": 1,
        "deployment_id": deployment_id,
        "run_id": "private-customer-run-id",
    }


def test_real_imported_run_and_reviewed_case_are_compared_without_provider_access(local, monkeypatch):
    def no_source(*_args, **_kwargs):
        pytest.fail("historical run comparison must not contact Stripe")
    monkeypatch.setattr(routes, "source_operation_receipt", no_source)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", no_source)
    imported = monitoring.import_report(_run())
    dep = imported["deployment"]["deployment_id"]
    with TestClient(app) as client:
        headers = _auth()
        saved = _case(client, headers)
        result = client.post(ENDPOINT, headers=headers, json=_payload(saved, dep))
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["format"] == "openworkgraph.local-reviewed-run-outcome-comparison.v1"
        assert data["selected_run"]["reported_run_status"] == "succeeded"
        assert data["selected_run"]["reported_outcome_verified"] is True
        assert data["historical_last_provider_state"] == "unknown"
        assert data["provider_check_vs_reported_run_finish"] == "no_historical_provider_check"
        assert data["narrow_historical_paid_observed_after_reported_finish"] is False
        assert data["operator_selected_run_case_association_only"] is True
        assert data["run_case_identity_independently_verified"] is False
        for flag in ("customer_outcome_verified", "agent_caused_outcome_verified",
                     "agent_execution_authorized", "source_read_performed", "source_write_performed"):
            assert data[flag] is False
        assert result.headers["cache-control"] == "no-store"
        for private in ("acct_PRIVATE", "private-customer-run-id", "sensitive-private-label"):
            assert private not in result.text
        assert not operation_ledger._path().exists()


def test_invalid_run_or_missing_report_does_not_create_monitoring_database(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _case(client, headers)
        dep = "dep_" + "1" * 16
        payload = _payload(saved, dep)
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code in (409, 422)
        assert not monitoring.database_path().exists()
        monitoring.import_report(_run())
        for bad in ("../bad", "dep_" + "x" * 16, "dep_" + "2" * 16):
            payload["deployment_id"] = bad
            assert client.post(ENDPOINT, headers=headers, json=payload).status_code in (409, 422)
        payload["deployment_id"] = dep
        payload["run_id"] = "\x00invalid"
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code in (409, 422)


def test_session_confirmation_revocation_and_source_switch_fail_closed(local):
    account, policy = local
    dep = monitoring.import_report(_run())["deployment"]["deployment_id"]
    with TestClient(app) as client:
        headers = _auth()
        saved = _case(client, headers)
        payload = _payload(saved, dep)
        assert client.post(ENDPOINT, json=payload).status_code in (401, 403)
        payload["confirm_run_comparison"] = False
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code == 403
        payload["confirm_run_comparison"] = True
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code == 200
        assert reviews.remove_review_intention(CASE)
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code in (403, 409, 422)
        account["id"] = "acct_OTHER"
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code in (403, 409, 422)
        policy["human_retention"] = {"mode": "session"}
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code in (403, 409, 422)


def test_replaced_run_between_reads_refuses_stale_comparison(local, monkeypatch):
    dep = monitoring.import_report(_run())["deployment"]["deployment_id"]
    with TestClient(app) as client:
        headers = _auth()
        saved = _case(client, headers)
        original = routes.read_imported_run
        calls = {"n": 0}

        def replaced(**kwargs):
            result = original(**kwargs)
            calls["n"] += 1
            if calls["n"] == 2:
                result = {**result, "reported_run_status": "failed"}
            return result

        monkeypatch.setattr(routes, "read_imported_run", replaced)
        response = client.post(ENDPOINT, headers=headers, json=_payload(saved, dep))
        assert response.status_code in (409, 422)
        assert calls["n"] == 2


def test_pure_comparison_never_equates_post_run_paid_receipt_with_causality():
    ref = CASE
    triage = {
        "format": "openworkgraph.local-reviewed-outcome-evidence-triage.v1",
        "work_item_ref": ref, "included_case_count": 1,
        "customer_outcome_verified": False, "source_read_performed": False,
        "unscanned_prerequisite_count": 0, "all_declared_prerequisites_included": True,
        "case_reviews": [{
            "work_item_ref": ref,
            "owner_declared_expected_outcome_code": "stripe_invoice_paid_reported",
            "historical_last_provider_state": "paid",
            "latest_human_assessed_status": "met",
            "provider_check_count": 1, "disputed_check_count": 0,
            "last_historical_provider_check_at": "2026-11-02T09:06:00+00:00",
        }],
    }
    plan = {
        "format": "openworkgraph.local-reviewed-outcome-action-plan.v1",
        "work_item_ref": ref,
        "cases": [{"work_item_ref": ref, "prerequisites_with_unresolved_review": []}],
    }
    run = {"reported_run_status": "succeeded", "reported_finished_at": "2026-11-02T09:05:00+00:00"}
    result = review.compose_run_outcome_review(run, triage, plan)
    assert result["narrow_historical_paid_observed_after_reported_finish"] is True
    assert result["agent_caused_outcome_verified"] is False
    assert result["customer_outcome_verified"] is False
    triage["case_reviews"][0]["last_historical_provider_check_at"] = "2026-11-02T09:04:00+00:00"
    before = review.compose_run_outcome_review(run, triage, plan)
    assert before["provider_check_vs_reported_run_finish"] == "before_reported_finish"
    assert before["narrow_historical_paid_observed_after_reported_finish"] is False
