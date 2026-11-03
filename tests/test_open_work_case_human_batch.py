"""Real FastAPI/private SQLite prerequisite-first human outcome batch acceptance.

All provider/customer inputs are synthetic. Human assertions never establish
independently verified business success, settlement, or agent causality.
"""
from datetime import datetime, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_case_outcome_reviews as outcomes
from server import open_work_declaration_routes as routes
from server import open_work_reviews as reviews
from server.local_auth import create_dashboard_session
from server.secure_app import app

A, B = ("owg:w:" + char * 32 for char in "ab")
BASE = "/v1/open-work/declarations"
BATCH = BASE + "/human-outcomes/assess-dependency-batch"


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
        lambda _name: "batch-human-test-secret-" + "x" * 40,
    )
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account, policy


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


def _payload(saved, counts=(0, 0)):
    return {
        "work_item_ref": B, "expected_revision": saved[B]["revision"],
        "expected_edit_token": saved[B]["edit_token"],
        "confirm_human_assessment": True, "max_cases": 2,
        "assessments": [
            {
                "work_item_ref": ref, "expected_revision": saved[ref]["revision"],
                "expected_edit_token": saved[ref]["edit_token"],
                "human_status": status, "reason_code": "owner_review",
                "expected_event_count": count,
            }
            for ref, status, count in zip((A, B), ("met", "not_met"), counts)
        ],
    }


def _count():
    with outcomes._database(create=False) as db:
        return (db.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]
                if db is not None else 0)


def test_batch_persists_both_independent_assertions_in_dependency_order(local, monkeypatch):
    def no_provider(*_args, **_kwargs):
        pytest.fail("historical human assessment must not read Stripe")
    monkeypatch.setattr(routes, "source_operation_receipt", no_provider)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", no_provider)
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert not outcomes._path().exists()
        response = client.post(BATCH, headers=headers, json=_payload(saved))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["format"] == "openworkgraph.local-dependency-human-assessment-batch.v1"
        assert result["assessed_case_count"] == 2
        assert result["prerequisite_first"] is True
        assert result["local_sqlite_batch_atomic"] is True
        assert [row["work_item_ref"] for row in result["case_histories"]] == [A, B]
        assert [row["latest_human_assessed_status"] for row in result["case_histories"]] == ["met", "not_met"]
        assert all(row["event_count"] == 1 for row in result["case_histories"])
        assert _count() == 2
        assert response.headers["cache-control"] == "no-store"
        for key in ("provider_operation_verified", "expected_outcome_verified",
                    "customer_outcome_verified", "agent_caused_outcome_verified",
                    "agent_execution_authorized", "source_read_performed",
                    "source_write_performed"):
            assert result[key] is False
        for private in ("acct_PRIVATE", "customer@example.com", "sk_test_", "in_PRIVATE"):
            assert private not in response.text
            assert private.encode() not in outcomes._path().read_bytes()
        for ref, status in ((A, "met"), (B, "not_met")):
            history = client.post(BASE + "/human-outcomes/history", headers=headers, json={
                "work_item_ref": ref, "expected_revision": saved[ref]["revision"],
                "expected_edit_token": saved[ref]["edit_token"],
            })
            assert history.status_code == 200, history.text
            assert history.json()["latest_human_assessed_status"] == status


def test_batch_second_insert_failure_rolls_back_both(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        original = outcomes._mac
        called = {"count": 0}

        def fail_second(*args, **kwargs):
            called["count"] += 1
            if called["count"] == 2:
                raise sqlite3.OperationalError("synthetic second append failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(outcomes, "_mac", fail_second)
        response = client.post(BATCH, headers=headers, json=_payload(saved))
        assert response.status_code == 409, response.text
        monkeypatch.setattr(outcomes, "_mac", original)
        assert called["count"] == 2
        assert _count() == 0
        retry = client.post(BATCH, headers=headers, json=_payload(saved))
        assert retry.status_code == 200, retry.text
        assert _count() == 2


def test_batch_optimistic_counts_and_order_prevent_partial_replays(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        payload = _payload(saved)
        assert client.post(BATCH, headers=headers, json=payload).status_code == 200
        assert client.post(BATCH, headers=headers, json=payload).status_code in (409, 422)
        assert _count() == 2
        swapped = _payload(saved, (1, 1))
        swapped["assessments"].reverse()
        assert client.post(BATCH, headers=headers, json=swapped).status_code in (409, 422)
        assert _count() == 2
        swapped = _payload(saved, (1, 0))
        assert client.post(BATCH, headers=headers, json=swapped).status_code in (409, 422)
        assert _count() == 2
        assert client.post(BATCH, headers=headers, json=_payload(saved, (1, 1))).status_code == 200
        assert _count() == 4


def test_batch_refuses_missing_duplicate_forged_or_unreviewed_cases(local):
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        for change in ("missing", "duplicate", "forged", "wrong_cas", "bad_status"):
            payload = _payload(saved)
            if change == "missing":
                payload["assessments"].pop()
            elif change == "duplicate":
                payload["assessments"][1] = dict(payload["assessments"][0])
            elif change == "forged":
                payload["assessments"][0]["work_item_ref"] = "owg:w:" + "f" * 32
            elif change == "wrong_cas":
                payload["assessments"][0]["expected_edit_token"] = "0" * 32
            else:
                payload["assessments"][1]["human_status"] = "verified"
            assert client.post(BATCH, headers=headers, json=payload).status_code in (403, 409, 422)
        assert _count() == 0
        assert reviews.remove_review_intention(A)
        assert client.post(BATCH, headers=headers, json=_payload(saved)).status_code in (403, 409, 422)
        assert _count() == 0


def test_batch_integrity_tampering_refuses_all_and_source_switch(local):
    account, _policy = local
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        assert client.post(BATCH, headers=headers, json=_payload(saved)).status_code == 200
        with sqlite3.connect(outcomes._path()) as db:
            db.execute("UPDATE assessments SET human_status='unknown' WHERE work_item_ref=?", (A,))
        assert client.post(BATCH, headers=headers, json=_payload(saved, (1, 1))).status_code == 422
        assert _count() == 2
        account["id"] = "acct_OTHER"
        assert client.post(BATCH, headers=headers, json=_payload(saved, (1, 1))).status_code in (403, 409, 422)
        assert _count() <= 2


def test_batch_auth_consent_and_retention_no_default_writes(local):
    _account, policy = local
    with TestClient(app) as client:
        headers = _auth()
        saved = _cases(client, headers)
        payload = _payload(saved)
        assert client.post(BATCH, json=payload).status_code == 401
        payload["confirm_human_assessment"] = False
        assert client.post(BATCH, headers=headers, json=payload).status_code == 403
        assert not outcomes._path().exists()
        payload["confirm_human_assessment"] = True
        policy["human_retention"] = {"mode": "session"}
        assert client.post(BATCH, headers=headers, json=payload).status_code in (403, 409, 422)
        assert not outcomes._path().exists()
