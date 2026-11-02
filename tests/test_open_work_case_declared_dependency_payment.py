"""FastAPI + real private SQLite acceptance for atomic reviewed dependency checks.

The Stripe-shaped receipts are synthetic. No real account, customer outcome,
bank settlement or agent causality is certified by these tests.
"""
from datetime import datetime, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server import open_work_case_operation_ledger as ledger
from server.local_auth import create_dashboard_session
from server.secure_app import app

A = "owg:w:" + "a" * 32
B = "owg:w:" + "b" * 32
RA = "owg:r:" + "a" * 32
RB = "owg:r:" + "b" * 32
BASE = "/v1/open-work/declarations"
BATCH = BASE + "/source-goals/verify-dependency-payments"
SINGLE = BASE + "/source-goals/verify-payment"
HISTORY = BASE + "/source-goals/payment-history"


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
        lambda _name: "dependency-batch-test-secret-" + "x" * 40,
    )
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _cas(row):
    return {
        "work_item_ref": row["work_item_ref"],
        "expected_revision": row["revision"],
        "expected_edit_token": row["edit_token"],
    }


def _declare(client, headers, ref, dependencies=(), *, outcome="stripe_invoice_paid_reported"):
    response = client.post(BASE, headers=headers, json={
        "confirm_owner_declaration": True, "work_item_ref": ref,
        "goal_code": "verify_operation",
        "expected_outcome_code": outcome,
        "depends_on_work_item_refs": list(dependencies),
        "expected_revision": 0, "expected_edit_token": None,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _scan(ref, **_options):
    resource = RA if ref == A else RB
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
            "resource_ref": resource, "observed_event_count": 2,
            "observed_session_count": 1, "observed_actor_count": 1,
            "timeline": [{"private_customer": "PRIVATE@example.invalid"}],
        }],
        "related_record_history_incomplete": False,
    }


def _paid(ref, operation):
    assert operation == "stripe_invoice_paid"
    return {
        "format": "openworkgraph.source-operation-receipt.v1",
        "resource_ref": ref, "expected_operation": operation,
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


def _setup(client, headers, monkeypatch, *, prerequisite_outcome="stripe_invoice_paid_reported"):
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    b = _declare(client, headers, B, outcome=prerequisite_outcome)
    a = _declare(client, headers, A, (B,))
    links = []
    for row, resource in ((b, RB), (a, RA)):
        response = client.post(BASE + "/operation-link", headers=headers, json={
            **_cas(row), "confirm_source_scan": True,
            "max_pages": 2, "resource_ref": resource,
        })
        assert response.status_code == 200, response.text
        links.append(response.json()["record_link"])
    return a, b, links


def _batch(client, headers, root, links):
    return client.post(BATCH, headers=headers, json={
        **_cas(root), "confirm_source_verification": True,
        "record_links": links,
    })


def test_two_case_dependency_batch_persists_atomically_with_unknown(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, b, links = _setup(client, headers, monkeypatch)
        reads = []
        def source(ref, op):
            reads.append(ref)
            return _paid(ref, op) if ref == RB else None
        monkeypatch.setattr(routes, "source_operation_receipt", source)
        response = _batch(client, headers, a, links)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["format"] == "openworkgraph.local-case-declared-dependency-payment-check.v1"
        assert reads == [RB, RA]  # prerequisite first
        assert [row["work_item_ref"] for row in data["cases"]] == [B, A]
        assert [row["provider_goal_state_at_check"] for row in data["cases"]] == ["met", "unknown"]
        assert [row["provider_state_transition"] for row in data["cases"]] == ["first_check", "first_check"]
        assert data["fresh_provider_paid_count"] == 1
        assert data["all_declared_provider_goals_met_at_individual_checks"] is False
        assert data["provider_reads_are_sequential_not_atomic_snapshot"] is True
        assert data["local_evidence_appends_are_one_atomic_transaction"] is True
        assert data["customer_outcome_verified"] is False
        assert data["agent_execution_authorized"] is False
        with sqlite3.connect(ledger._path()) as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2
            stored = str(db.execute("SELECT * FROM evidence").fetchall())
        for secret in (RA, RB, "in_PRIVATE", "acct_PRIVATE", "PRIVATE@example.invalid"):
            assert secret not in response.text
            assert secret not in stored
        assert response.headers["cache-control"] == "no-store"
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("history must not read provider"))
        for row in (a, b):
            history = client.post(HISTORY, headers=headers, json=_cas(row))
            assert history.status_code == 200, history.text
            assert history.json()["provider_check_count"] == 1


def test_invalid_missing_forged_and_duplicate_links_refuse_before_provider_io(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, b, links = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("invalid batch cannot read Stripe"))
        for candidate in (
            links[:1],
            links + [links[0]],
            [links[0], dict(links[1], seal="0" * 64)],
            [links[0], dict(links[0])],
        ):
            result = _batch(client, headers, a, candidate)
            assert result.status_code in (403, 409, 422), result.text
        assert not ledger._path().exists()


def test_unreviewed_payment_goal_prerequisite_is_refused_before_io(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, b, links = _setup(client, headers, monkeypatch,
                             prerequisite_outcome="operation_receipt_reviewed")
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("incompatible goal cannot read provider"))
        result = _batch(client, headers, a, links)
        assert result.status_code in (403, 409, 422)
        assert not ledger._path().exists()


def test_mid_read_review_revocation_stops_next_provider_call_without_write(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, b, links = _setup(client, headers, monkeypatch)
        reads = []
        def revoke(ref, op):
            reads.append(ref)
            reviews.remove_review_intention(A)
            return _paid(ref, op)
        monkeypatch.setattr(routes, "source_operation_receipt", revoke)
        result = _batch(client, headers, a, links)
        assert result.status_code in (403, 409, 422)
        assert reads == [RB]
        assert not ledger._path().exists()


def test_concurrent_dispute_rolls_back_all_new_dependency_checks(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, b, links = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        first = client.post(SINGLE, headers=headers, json={
            **_cas(b), "confirm_source_verification": True,
            "record_link": links[0],
        })
        assert first.status_code == 200, first.text
        original = routes.append_operation_evidence_batch
        def dispute_before_atomic_append(entries, **kw):
            ledger.dispute_operation_evidence(
                entries[0]["admitted"], entries[0]["signature"],
                scope=kw["scope"], secret=kw["secret"], guard=kw["guard"],
                target_seq=1, reason="operator_dispute",
            )
            return original(entries, **kw)
        monkeypatch.setattr(routes, "append_operation_evidence_batch", dispute_before_atomic_append)
        result = _batch(client, headers, a, links)
        assert result.status_code == 409, result.text
        with sqlite3.connect(ledger._path()) as db:
            rows = db.execute(
                "SELECT work_item_ref, kind FROM evidence ORDER BY work_item_ref, seq"
            ).fetchall()
        assert rows == [(B, "check"), (B, "dispute")]


def test_second_check_transitions_both_cases_and_no_new_permissions(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, b, links = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", lambda *_: None)
        assert _batch(client, headers, a, links).status_code == 200
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        result = _batch(client, headers, a, links)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["fresh_provider_paid_count"] == 2
        assert data["all_declared_provider_goals_met_at_individual_checks"] is True
        assert [row["provider_state_transition"] for row in data["cases"]] == [
            "unknown_to_paid", "unknown_to_paid",
        ]
        assert data["business_objective_verified"] is False
        assert data["agent_caused_outcome_verified"] is False
        assert data["source_write_performed"] is False
