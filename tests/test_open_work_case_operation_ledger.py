"""Actual FastAPI/SQLite integration with synthetic Stripe provider responses.

A fake provider does not prove real Stripe acceptance or a customer outcome.
"""
from datetime import datetime, timezone
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server import open_work_case_operation_ledger as ledger
from server.local_auth import create_dashboard_session
from server.secure_app import app


@contextmanager
def _closed_sqlite(path):
    """Preserve transactional context semantics and release handles on Windows."""
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()

A, B = ("owg:w:" + c * 32 for c in "ab")
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", lambda: policy)
    monkeypatch.setattr(reviews, "read_policy", lambda: policy)
    account = {"id": "acct_local_private"}
    monkeypatch.setattr(routes, "_read_metadata",
                        lambda _path: {"account": account["id"]} if account["id"] else None)
    monkeypatch.setattr(routes, "_read_or_create_secret",
                        lambda _name: "ledger-test-local-key-" + "x" * 40)
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
            "confirm_owner_declaration": True,
            "work_item_ref": ref, "goal_code": "verify_operation",
            "expected_outcome_code": "operation_receipt_reviewed",
            "depends_on_work_item_refs": deps,
            "expected_revision": 0, "expected_edit_token": None,
        })
        assert response.status_code == 200, response.text
        saved[ref] = response.json()
    return saved


def _payload(saved):
    return {
        "work_item_ref": B, "expected_revision": saved[B]["revision"],
        "expected_edit_token": saved[B]["edit_token"],
    }


def _scan(ref, **_options):
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
        **_payload(saved), "confirm_source_scan": True,
        "max_pages": 2, "resource_ref": R,
    })
    assert response.status_code == 200, response.text
    return response.json()["record_link"]


def _check(client, headers, saved, link):
    return client.post(BASE + "/operation-ledger/check", headers=headers, json={
        **_payload(saved), "confirm_source_verification": True,
        "record_link": link,
    })


def _history(client, headers, saved):
    return client.post(BASE + "/operation-ledger/history", headers=headers,
                       json=_payload(saved))


def _dispute(client, headers, saved, seq=1, reason="source_conflict"):
    return client.post(BASE + "/operation-ledger/dispute", headers=headers, json={
        **_payload(saved), "confirm_dispute": True,
        "target_sequence": seq, "reason_code": reason,
    })


def test_persisted_paid_unknown_dispute_and_read_without_provider(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    link = _link(client, headers, saved, monkeypatch)
    reads = []
    def source(ref, op):
        reads.append((ref, op))
        return _paid(ref, op) if len(reads) == 1 else None
    monkeypatch.setattr(routes, "source_operation_receipt", source)
    first = _check(client, headers, saved, link)
    assert first.status_code == 200, first.text
    assert first.json()["last_observed_provider_state"] == "paid"
    assert first.json()["server_side_evidence_persisted"] is True
    assert ledger._path().is_file()
    second = _check(client, headers, saved, link)
    assert second.status_code == 200, second.text
    assert second.json()["last_observed_provider_state"] == "unknown"
    assert second.json()["provider_check_count"] == 2
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("history/dispute must not read provider"))
    history = _history(client, headers, saved)
    assert history.status_code == 200, history.text
    assert history.json()["provider_check_count"] == 2
    disputed = _dispute(client, headers, saved, seq=1)
    assert disputed.status_code == 200, disputed.text
    assert disputed.json()["events"][0]["disputed"] is True
    assert disputed.json()["events"][2]["kind"] == "dispute"
    assert _dispute(client, headers, saved, seq=1).status_code in (409, 422)
    assert len(reads) == 2
    for response in (first, second, history, disputed):
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["customer_outcome_verified"] is False
        assert response.json()["current_provider_operation_verified"] is False
        assert response.json()["agent_execution_authorized"] is False
        assert all(private not in response.text for private in (
            R, "in_PRIVATE", "PRIVATE", "acct_local_private",
            "edit_token", "record_binding", "graph_binding", "source_binding",
        ))
    with _closed_sqlite(ledger._path()) as db:
        contents = str(db.execute("SELECT * FROM evidence").fetchall())
    assert all(private not in contents for private in (
        R, "in_PRIVATE", "PRIVATE", "acct_local_private",
    ))


def test_revocation_disconnection_and_retention_prevent_history_access(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    link = _link(client, headers, saved, monkeypatch)
    monkeypatch.setattr(routes, "source_operation_receipt", _paid)
    assert _check(client, headers, saved, link).status_code == 200
    account, policy = local
    account["id"] = "acct_other"
    assert _history(client, headers, saved).status_code in (403, 409, 422)
    account["id"] = "acct_local_private"
    assert _history(client, headers, saved).status_code == 200
    reviews.remove_review_intention(B)
    assert _history(client, headers, saved).status_code in (403, 409, 422)
    with _closed_sqlite(ledger._path()) as db:
        assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    reviews.clear_review_intentions()
    assert not ledger._path().exists()


def test_mid_provider_read_revocation_never_commits(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    link = _link(client, headers, saved, monkeypatch)
    def revoke(ref, op):
        reviews.remove_review_intention(A)
        return _paid(ref, op)
    monkeypatch.setattr(routes, "source_operation_receipt", revoke)
    assert _check(client, headers, saved, link).status_code in (403, 409, 422)
    if ledger._path().exists():
        with _closed_sqlite(ledger._path()) as db:
            assert db.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0


def test_invalid_link_and_missing_confirmation_refuse_before_source_read(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    link = _link(client, headers, saved, monkeypatch)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("invalid request must not read provider"))
    forged = dict(link, seal="0" * 64)
    assert _check(client, headers, saved, forged).status_code in (409, 422)
    assert client.post(BASE + "/operation-ledger/check", headers=headers, json={
        **_payload(saved), "confirm_source_verification": False,
        "record_link": link,
    }).status_code == 403
    assert _history(client, {}, saved).status_code == 401  # no credentials: global auth refuses first
    assert not ledger._path().exists()


def test_history_integrity_tampering_and_dispute_validation(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    link = _link(client, headers, saved, monkeypatch)
    monkeypatch.setattr(routes, "source_operation_receipt", _paid)
    assert _check(client, headers, saved, link).status_code == 200
    assert _dispute(client, headers, saved, seq=999).status_code in (409, 422)
    assert _dispute(client, headers, saved, reason="paid_verified").status_code in (409, 422)
    with _closed_sqlite(ledger._path()) as db:
        db.execute("UPDATE evidence SET provider_state='unknown' WHERE seq=1")
    assert _history(client, headers, saved).status_code in (409, 422)
    assert _check(client, headers, saved, link).status_code in (409, 422)


def test_retention_switch_and_no_source_or_customer_write(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    link = _link(client, headers, saved, monkeypatch)
    monkeypatch.setattr(routes, "source_operation_receipt", _paid)
    assert _check(client, headers, saved, link).status_code == 200
    _account, policy = local
    policy["human_retention"] = {"mode": "ephemeral"}
    assert _history(client, headers, saved).status_code in (403, 409, 422)
    assert _check(client, headers, saved, link).status_code in (403, 409, 422)
    reviews.clear_review_intentions()
    assert not ledger._path().exists()


def test_disputing_latest_paid_check_makes_effective_history_unknown(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    link = _link(client, headers, saved, monkeypatch)
    monkeypatch.setattr(routes, "source_operation_receipt", _paid)
    assert _check(client, headers, saved, link).status_code == 200
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("dispute must not call provider"))
    disputed = _dispute(client, headers, saved, seq=1, reason="record_mismatch")
    assert disputed.status_code == 200, disputed.text
    assert disputed.json()["last_observed_provider_state"] == "unknown"
    assert disputed.json()["events"][0]["provider_state"] == "paid"
    assert disputed.json()["events"][0]["disputed"] is True
    assert _history(client, headers, saved).json()["last_observed_provider_state"] == "unknown"


def test_prerequisite_revision_invalidates_persisted_history(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    link = _link(client, headers, saved, monkeypatch)
    monkeypatch.setattr(routes, "source_operation_receipt", _paid)
    assert _check(client, headers, saved, link).status_code == 200
    updated = client.post(BASE, headers=headers, json={
        "confirm_owner_declaration": True, "work_item_ref": A,
        "goal_code": "review_source_state",
        "expected_outcome_code": "source_state_rechecked",
        "depends_on_work_item_refs": [],
        "expected_revision": saved[A]["revision"],
        "expected_edit_token": saved[A]["edit_token"],
    })
    assert updated.status_code == 200, updated.text
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("stale dependency must not read provider"))
    assert _history(client, headers, saved).status_code in (403, 409, 422)
    assert _check(client, headers, saved, link).status_code in (403, 409, 422)
