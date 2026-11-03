"""FastAPI/private-SQLite human-outcome feedback integration (synthetic local cases).

A human status is an assertion, not independently verified business success.
"""
from datetime import datetime, timezone
import os
from contextlib import closing
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server import open_work_case_outcome_reviews as outcomes
from server.local_auth import create_dashboard_session
from server.secure_app import app

A, B = ("owg:w:" + c * 32 for c in "ab")
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
        lambda _name: "human-outcome-local-secret-" + "x" * 40,
    )
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account, policy, tmp_path


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


def _assess(client, headers, saved, status="met", reason="owner_review"):
    return client.post(BASE + "/human-outcomes/assess", headers=headers, json={
        **_cas(saved), "confirm_human_assessment": True,
        "human_status": status, "reason_code": reason,
    })


def _history(client, headers, saved):
    return client.post(BASE + "/human-outcomes/history", headers=headers,
                       json=_cas(saved))


def _correct(client, headers, saved, seq=1, status="unknown"):
    return client.post(BASE + "/human-outcomes/correct", headers=headers, json={
        **_cas(saved), "confirm_human_correction": True,
        "target_sequence": seq, "human_status": status,
    })


def test_explicit_human_feedback_persists_and_correction_preserves_history(local, monkeypatch):
    def forbid_provider(*args, **kwargs):
        raise AssertionError("human assessment must not read a provider")
    monkeypatch.setattr(routes, "source_operation_receipt", forbid_provider)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", forbid_provider)
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert _history(client, headers, saved).json()["event_count"] == 0
        first = _assess(client, headers, saved, "not_met", "customer_feedback_reviewed")
        assert first.status_code == 200, first.text
        assert first.json()["latest_human_assessed_status"] == "not_met"
        assert outcomes._path().exists()
        later = _correct(client, headers, saved, 1, "met")
        assert later.status_code == 200, later.text
        history = _history(client, headers, saved)
        assert history.status_code == 200, history.text
        data = history.json()
        assert data["event_count"] == 2
        assert data["events"][0]["human_assessed_status"] == "not_met"
        assert data["events"][1]["kind"] == "correction"
        assert data["events"][1]["corrects_sequence"] == 1
        assert data["latest_human_assessed_status"] == "met"
        for key in ("expected_outcome_verified", "customer_outcome_verified",
                    "provider_operation_verified", "business_obligation_verified",
                    "agent_caused_outcome_verified", "agent_execution_authorized",
                    "source_read_performed", "source_write_performed"):
            assert data[key] is False
        assert history.headers["cache-control"] == "no-store"
        blob = outcomes._path().read_bytes()
        for private in (b"acct_PRIVATE", b"customer@example.com",
                        b"invoice_", b"sk_test_", b"PRIVATE CUSTOMER"):
            assert private not in blob
        if os.name == "posix":
            assert outcomes._path().stat().st_mode & 0o777 == 0o600


def test_validation_confirmation_and_optimistic_correction(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert client.post(BASE + "/human-outcomes/history",
                           json=_cas(saved)).status_code == 401
        assert client.post(BASE + "/human-outcomes/assess", headers=headers,
                           json={**_cas(saved), "confirm_human_assessment": False,
                                 "human_status": "met", "reason_code": "owner_review"}).status_code == 403
        assert _assess(client, headers, saved, "verified", "owner_review").status_code == 422
        assert _assess(client, headers, saved, "met", "untrusted_free_text").status_code == 422
        assert _correct(client, headers, saved).status_code == 422
        assert _assess(client, headers, saved).status_code == 200
        assert _correct(client, headers, saved, 2).status_code == 409
        assert _correct(client, headers, saved, 1).status_code == 200
        assert _correct(client, headers, saved, 1).status_code == 409
        assert _correct(client, headers, saved, 2, "not_met").status_code == 200
        assert _history(client, headers, saved).json()["event_count"] == 3


def test_review_deletion_and_source_disconnect_scrub_feedback(local):
    account, _policy, _path = local
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert _assess(client, headers, saved).status_code == 200
        assert client.post(BASE + "/human-outcomes/assess", headers=headers, json={
            **_cas(saved, A), "confirm_human_assessment": True,
            "human_status": "unknown", "reason_code": "owner_review",
        }).status_code == 200
        assert reviews.remove_review_intention(B)
        assert _history(client, headers, saved).status_code in (403, 409, 422)
        # Revoking B must not erase independent A's assessment.
        with outcomes._database(create=False) as db:
            assert db is not None
            assert db.execute("SELECT work_item_ref FROM assessments").fetchall() == [(A,)]
        account["id"] = None
        assert _history(client, headers, saved).status_code == 403
        reviews.clear_review_intentions()
        assert not outcomes._path().exists()


def test_owner_declaration_deletion_scrubs_all_dependent_feedback(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert _assess(client, headers, saved).status_code == 200
        assert declarations.delete_case_declaration(
            A, routes._source_scope(),
            expected_revision=saved[A]["revision"],
            expected_edit_token=saved[A]["edit_token"],
        )["invalidated_declaration_count"] == 2
        with outcomes._database(create=False) as db:
            assert db is not None
            assert db.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] == 0
        assert _history(client, headers, saved).status_code in (403, 409, 422)


def test_integrity_failure_and_graph_version_change_fail_closed(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert _assess(client, headers, saved).status_code == 200
        with closing(sqlite3.connect(outcomes._path())) as db, db:  # close it: Windows cannot delete an open file
            db.execute("UPDATE assessments SET human_status='not_met' WHERE work_item_ref=?", (B,))
        assert _history(client, headers, saved).status_code == 422
        outcomes.clear_human_outcome_reviews()
        assert _assess(client, headers, saved).status_code == 200
        assert client.post(BASE, headers=headers, json={
            "confirm_owner_declaration": True, "work_item_ref": A,
            "goal_code": "review_source_state",
            "expected_outcome_code": "source_state_rechecked",
            "depends_on_work_item_refs": [],
            "expected_revision": saved[A]["revision"],
            "expected_edit_token": saved[A]["edit_token"],
        }).status_code == 200
        assert _history(client, headers, saved).status_code in (403, 409, 422)


def test_retention_revocation_and_no_default_store(local):
    _account, policy, _path = local
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert not outcomes._path().exists()
        assert _history(client, headers, saved).status_code == 200
        assert not outcomes._path().exists()
        assert _assess(client, headers, saved).status_code == 200
        policy["human_retention"] = {"mode": "session"}
        assert _history(client, headers, saved).status_code in (403, 409, 422)
        reviews.clear_review_intentions()
        assert not outcomes._path().exists()


def _dependency_history(client, headers, saved, max_cases=2, confirm=True):
    return client.post(BASE + "/human-outcomes/dependency-history", headers=headers, json={
        **_cas(saved), "confirm_historical_review": confirm,
        "max_cases": max_cases,
    })


def test_human_outcome_dependency_history_is_bounded_and_source_free(local, monkeypatch):
    def no_provider(*args, **kwargs):
        raise AssertionError("human outcome dependency history must not read providers")
    monkeypatch.setattr(routes, "source_operation_receipt", no_provider)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", no_provider)
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert _assess(client, headers, saved, "not_met").status_code == 200
        assert client.post(BASE + "/human-outcomes/assess", headers=headers, json={
            **_cas(saved, A), "confirm_human_assessment": True,
            "human_status": "met", "reason_code": "owner_review",
        }).status_code == 200
        assert _correct(client, headers, saved, 1, "met").status_code == 200
        response = _dependency_history(client, headers, saved)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["format"] == "openworkgraph.local-reviewed-dependency-human-outcomes.v1"
        assert data["included_case_count"] == 2
        assert data["unscanned_prerequisite_count"] == 0
        assert data["human_feedback_coverage_complete"] is True
        assert data["every_selected_case_has_human_feedback"] is True
        assert {row["work_item_ref"] for row in data["case_histories"]} == {A, B}
        assert all(row["latest_human_assessed_status"] == "met"
                   for row in data["case_histories"])
        b = next(row for row in data["case_histories"] if row["work_item_ref"] == B)
        assert b["human_assessment_count"] == 2
        assert b["history_events"][-1]["kind"] == "correction"
        assert b["history_events"][-1]["corrects_sequence"] == 1
        assert data["selected_dependency_edges"] == [{
            "prerequisite_ref": A, "dependent_ref": B,
            "edge_type": "owner_declared_dependency", "source_verified": False,
        }]
        assert data["human_assessments_are_not_independent_verification"] is True
        assert data["separate_local_ledger_reads_not_atomic_snapshot"] is True
        for key in ("expected_outcome_verified", "customer_outcome_verified",
                    "current_provider_operation_verified", "business_obligation_verified",
                    "agent_caused_outcome_verified", "agent_execution_authorized",
                    "source_read_performed", "source_write_performed"):
            assert data[key] is False
        assert response.headers["cache-control"] == "no-store"


def test_dependency_human_history_missing_and_omitted_are_not_success(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert _assess(client, headers, saved).status_code == 200
        full = _dependency_history(client, headers, saved).json()
        assert full["included_case_count"] == 2
        assert full["human_feedback_coverage_complete"] is False
        a = next(row for row in full["case_histories"] if row["work_item_ref"] == A)
        assert a["human_assessment_count"] == 0
        assert a["latest_human_assessed_status"] == "unknown"
        partial = _dependency_history(client, headers, saved, max_cases=1)
        assert partial.status_code == 200, partial.text
        data = partial.json()
        assert data["included_case_count"] == 1
        assert data["unscanned_prerequisite_count"] == 1
        assert data["all_declared_prerequisites_included"] is False
        assert data["human_feedback_coverage_complete"] is False
        assert data["selected_dependency_edges"] == []
        assert data["case_histories"][0]["work_item_ref"] == B


def test_dependency_human_history_requires_review_and_fails_closed(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert client.post(BASE + "/human-outcomes/dependency-history",
                           json={**_cas(saved), "confirm_historical_review": True,
                                 "max_cases": 2}).status_code == 401
        assert _dependency_history(client, headers, saved, confirm=False).status_code == 403
        for count in (0, 5, True, "2"):
            assert _dependency_history(client, headers, saved, max_cases=count).status_code == 422
        assert _assess(client, headers, saved).status_code == 200
        with closing(sqlite3.connect(outcomes._path())) as db, db:  # close it: Windows cannot delete an open file
            db.execute("UPDATE assessments SET human_status='not_met' WHERE work_item_ref=?", (B,))
        assert _dependency_history(client, headers, saved).status_code == 422
        outcomes.clear_human_outcome_reviews()
        assert _assess(client, headers, saved).status_code == 200
        assert reviews.remove_review_intention(A)
        assert _dependency_history(client, headers, saved).status_code in (403, 409, 422)
