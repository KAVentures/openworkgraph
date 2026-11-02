"""FastAPI/private-SQLite dependency history integration with synthetic Stripe.

The provider fixture is not a live Stripe or customer-outcome acceptance test.
"""
from datetime import datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server import open_work_case_operation_ledger as ledger
from server.local_auth import create_dashboard_session
from server.secure_app import app

A, B = ("owg:w:" + c * 32 for c in "ab")
RA, RB = ("owg:r:" + c * 32 for c in "ef")
BASE = "/v1/open-work/declarations"
HISTORY = BASE + "/operation-ledger/dependency-history"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", lambda: policy)
    monkeypatch.setattr(reviews, "read_policy", lambda: policy)
    account = {"id": "acct_SECRET_test"}
    monkeypatch.setattr(routes, "_read_metadata",
                        lambda _path: {"account": account["id"]} if account["id"] else None)
    monkeypatch.setattr(routes, "_read_or_create_secret",
                        lambda _name: "dependency-history-local-key-" + "x" * 40)
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _save(client, headers):
    result = {}
    for ref, deps in ((A, []), (B, [A])):
        r = client.post(BASE, headers=headers, json={
            "confirm_owner_declaration": True,
            "work_item_ref": ref, "goal_code": "verify_operation",
            "expected_outcome_code": "operation_receipt_reviewed",
            "depends_on_work_item_refs": deps,
            "expected_revision": 0, "expected_edit_token": None,
        })
        assert r.status_code == 200, r.text
        result[ref] = r.json()
    return result


def _cas(saved, ref=B):
    return {
        "work_item_ref": ref,
        "expected_revision": saved[ref]["revision"],
        "expected_edit_token": saved[ref]["edit_token"],
    }


def _scan(ref, **_kwargs):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref, "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe", "source_state": "open",
            "status_scope": "stripe_invoice_open", "inventory_complete": False,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "native_id": "in_SECRET",
        },
        "related_owg_evidence": [{
            "resource_ref": RA if ref == A else RB,
            "observed_event_count": 2, "observed_session_count": 1,
            "observed_actor_count": 1,
            "timeline": [{"email": "SECRET@example.invalid"}],
        }],
        "related_record_history_incomplete": False,
        "candidate": {"customer": "SECRET", "amount": 99999},
    }


def _paid(ref, operation):
    assert operation == "stripe_invoice_paid"
    assert ref in (RA, RB)
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
        "native_id": "in_SECRET",
    }


def _seed(client, headers, saved, monkeypatch, ref):
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    record = RA if ref == A else RB
    link = client.post(BASE + "/operation-link", headers=headers, json={
        **_cas(saved, ref), "confirm_source_scan": True,
        "max_pages": 2, "resource_ref": record,
    })
    assert link.status_code == 200, link.text
    monkeypatch.setattr(routes, "source_operation_receipt", _paid)
    check = client.post(BASE + "/operation-ledger/check", headers=headers, json={
        **_cas(saved, ref), "confirm_source_verification": True,
        "record_link": link.json()["record_link"],
    })
    assert check.status_code == 200, check.text
    return check.json()


def _history(client, headers, saved, max_cases=2, **changes):
    return client.post(HISTORY, headers=headers, json={
        **_cas(saved), "confirm_historical_review": True,
        "max_cases": max_cases, **changes,
    })


def test_historical_dependency_matrix_and_explicit_dispute(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _save(client, headers)
    _seed(client, headers, saved, monkeypatch, A)
    _seed(client, headers, saved, monkeypatch, B)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_args: pytest.fail("history made a provider read"))
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda *_args, **_kwargs: pytest.fail("history scanned inventory"))
    r = _history(client, headers, saved)
    assert r.status_code == 200, r.text
    data = r.json()
    assert [row["work_item_ref"] for row in data["case_histories"]] == [A, B]
    assert [row["historical_last_provider_state"] for row in data["case_histories"]] == ["paid", "paid"]
    assert data["historical_provider_evidence_coverage_complete"] is True
    assert data["selected_dependency_edges"] == [{
        "prerequisite_ref": A, "dependent_ref": B,
        "edge_type": "owner_declared_dependency", "source_verified": False,
    }]
    assert data["current_provider_operation_verified"] is False
    assert data["customer_outcome_verified"] is False
    assert data["source_read_performed"] is False
    assert data["separate_local_ledger_reads_not_atomic_snapshot"] is True
    raw = json.dumps(data)
    for private in ("acct_SECRET", "in_SECRET", "SECRET@example.invalid", "99999", RA, RB):
        assert private not in raw

    dispute = client.post(BASE + "/operation-ledger/dispute", headers=headers, json={
        **_cas(saved), "confirm_dispute": True,
        "target_sequence": 1, "reason_code": "operator_dispute",
    })
    assert dispute.status_code == 200, dispute.text
    changed = _history(client, headers, saved).json()
    assert [row["historical_last_provider_state"] for row in changed["case_histories"]] == ["paid", "unknown"]
    assert changed["case_histories"][1]["history_events"][0]["disputed"] is True
    assert changed["historical_provider_evidence_coverage_complete"] is True
    assert changed["customer_outcome_verified"] is False


def test_bounded_partial_history_and_no_evidence(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _save(client, headers)
    empty = _history(client, headers, saved)
    assert empty.status_code == 200, empty.text
    assert empty.json()["historical_provider_evidence_coverage_complete"] is False
    assert all(row["history_events"] == [] for row in empty.json()["case_histories"])
    _seed(client, headers, saved, monkeypatch, B)
    partial = _history(client, headers, saved)
    assert partial.status_code == 200, partial.text
    assert partial.json()["case_histories"][0]["retained_history_present"] is False
    assert partial.json()["historical_provider_evidence_coverage_complete"] is False
    bounded = _history(client, headers, saved, max_cases=1)
    assert bounded.status_code == 200, bounded.text
    assert bounded.json()["included_case_count"] == 1
    assert bounded.json()["unscanned_prerequisite_count"] == 1
    assert bounded.json()["selected_dependency_edges"] == []
    assert bounded.json()["historical_provider_evidence_coverage_complete"] is False


def test_revocation_between_case_reads_stops_further_access(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _save(client, headers)
    _seed(client, headers, saved, monkeypatch, A)
    _seed(client, headers, saved, monkeypatch, B)
    original = routes.read_operation_evidence
    reads = []
    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        reads.append(args[0]["work_item_ref"])
        if len(reads) == 1:
            reviews.clear_review_intentions()
        return result
    monkeypatch.setattr(routes, "read_operation_evidence", revoked)
    r = _history(client, headers, saved)
    assert r.status_code in (403, 409), r.text
    assert reads == [A]


def test_tampered_ledger_refused_and_no_provider_io(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _save(client, headers)
    _seed(client, headers, saved, monkeypatch, A)
    with ledger._database(create=False) as db:
        db.execute("UPDATE evidence SET provider_state='unknown' WHERE work_item_ref=?", (A,))
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_args: pytest.fail("provider read after tamper"))
    r = _history(client, headers, saved)
    assert r.status_code in (409, 422), r.text


def test_auth_confirmation_exact_cas_and_limit(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _save(client, headers)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_args: pytest.fail("provider read"))
    assert _history(client, {}, saved).status_code == 401
    assert _history(client, headers, saved, confirm_historical_review=False).status_code == 403
    assert _history(client, headers, saved, max_cases=0).status_code == 422
    assert _history(client, headers, saved, max_cases=True).status_code == 422
    assert _history(client, headers, saved, max_cases=5).status_code == 422
    assert _history(client, headers, saved, expected_edit_token="0" * 32).status_code == 409
    assert _history(client, headers, saved, unexpected_field="extra").status_code == 422
