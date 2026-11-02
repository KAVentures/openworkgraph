"""Real FastAPI/private-SQLite path with a synthetic source receipt.

No synthetic receipt certifies a live Stripe account, customer settlement,
business uplift or an agent's causal contribution.
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
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"
VERIFY = BASE + "/source-goals/verify-payment"
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
        lambda _name: "declared-goal-test-secret-" + "x" * 40,
    )
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account, policy


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _declare(client, headers, ref=A, *, goal="verify_operation",
             expected="stripe_invoice_paid_reported"):
    response = client.post(BASE, headers=headers, json={
        "confirm_owner_declaration": True,
        "work_item_ref": ref, "goal_code": goal,
        "expected_outcome_code": expected,
        "depends_on_work_item_refs": [],
        "expected_revision": 0, "expected_edit_token": None,
    })
    return response


def _cas(saved):
    return {
        "work_item_ref": saved["work_item_ref"],
        "expected_revision": saved["revision"],
        "expected_edit_token": saved["edit_token"],
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


def _verify(client, headers, saved, link, confirm=True):
    return client.post(VERIFY, headers=headers, json={
        **_cas(saved), "confirm_source_verification": confirm,
        "record_link": link,
    })


def _history(client, headers, saved):
    return client.post(HISTORY, headers=headers, json=_cas(saved))


def test_paid_then_outage_is_unknown_but_durable_and_never_customer_success(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        declared = _declare(client, headers)
        assert declared.status_code == 200, declared.text
        saved = declared.json()
        link = _link(client, headers, saved, monkeypatch)
        reads = []
        def source(ref, op):
            reads.append((ref, op))
            return _paid(ref, op) if len(reads) == 1 else None
        monkeypatch.setattr(routes, "source_operation_receipt", source)
        paid = _verify(client, headers, saved, link)
        assert paid.status_code == 200, paid.text
        assert paid.json()["provider_declared_goal_met_at_check"] is True
        assert paid.json()["provider_declared_goal_state_at_check"] == "met"
        assert paid.json()["provider_check_count"] == 1
        assert paid.json()["customer_outcome_verified"] is False
        assert paid.json()["business_objective_verified"] is False
        unknown = _verify(client, headers, saved, link)
        assert unknown.status_code == 200, unknown.text
        assert unknown.json()["provider_declared_goal_state_at_check"] == "unknown"
        assert unknown.json()["provider_declared_goal_met_at_check"] is False
        assert unknown.json()["provider_check_count"] == 2
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_a: pytest.fail("historical read must not call provider"))
        history = _history(client, headers, saved)
        assert history.status_code == 200, history.text
        assert history.json()["historical_last_provider_state"] == "unknown"
        assert history.json()["provider_declared_goal_met_now"] is False
        assert history.json()["fresh_source_recheck_required"] is True
        assert len(reads) == 2
        assert ledger._path().is_file()
        for response in (paid, unknown, history):
            assert response.headers["cache-control"] == "no-store"
            assert all(secret not in response.text for secret in (
                R, "in_PRIVATE", "PRIVATE", "acct_PRIVATE", "graph_binding", "record_binding",
            ))
        with sqlite3.connect(ledger._path()) as db:
            rows = str(db.execute("SELECT * FROM evidence").fetchall())
        assert all(secret not in rows for secret in (R, "in_PRIVATE", "acct_PRIVATE"))


def test_disputed_historical_paid_never_becomes_current(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        assert _verify(client, headers, saved, link).status_code == 200
        history = _history(client, headers, saved)
        assert history.json()["historical_last_check_reported_paid"] is True
        assert history.json()["provider_declared_goal_met_now"] is False
        disputed = client.post(BASE + "/operation-ledger/dispute", headers=headers, json={
            **_cas(saved), "confirm_dispute": True,
            "target_sequence": 1, "reason_code": "operator_dispute",
        })
        assert disputed.status_code == 200, disputed.text
        after = _history(client, headers, saved)
        assert after.json()["historical_last_provider_state"] == "unknown"
        assert after.json()["disputed_provider_check_count"] == 1


def test_mismatched_goal_and_forged_link_refused_before_source_io(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        assert _declare(client, headers, goal="review_source_state").status_code == 422
        assert _declare(client, headers, ref=B, goal="assess_declared_policy").status_code == 422
        other = _declare(client, headers, ref=B, expected="human_review_recorded")
        assert other.status_code == 200, other.text
        assert _history(client, headers, other.json()).status_code in (403, 422)
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_a: pytest.fail("rejected request must not read provider"))
        assert _verify(client, headers, saved, link, confirm=False).status_code == 403
        assert _verify(client, headers, saved, dict(link, seal="0" * 64)).status_code in (409, 422)
        assert _history(client, {}, saved).status_code == 401
        assert not ledger._path().exists()


def test_mid_read_review_revocation_and_source_change_fail_closed(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        def revoke(ref, op):
            reviews.remove_review_intention(A)
            return _paid(ref, op)
        monkeypatch.setattr(routes, "source_operation_receipt", revoke)
        assert _verify(client, headers, saved, link).status_code in (403, 409, 422)
        if ledger._path().exists():
            with sqlite3.connect(ledger._path()) as db:
                assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0


def test_account_switch_and_stale_cas_deny_historical_access(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        assert _verify(client, headers, saved, link).status_code == 200
        account, _ = local
        account["id"] = "acct_DIFFERENT"
        assert _history(client, headers, saved).status_code in (403, 409, 422)
        account["id"] = "acct_PRIVATE"
        assert _history(client, headers, saved).status_code == 200
        stale = dict(saved, edit_token="0" * 32)
        assert _history(client, headers, stale).status_code in (403, 409, 422)


def test_batch_creation_refuses_incompatible_source_payment_goal(local):
    with TestClient(app) as client:
        headers = _auth()
        invalid = client.post(BASE + "/batch", headers=headers, json={
            "confirm_batch_create": True,
            "items": [{
                "work_item_ref": A, "goal_code": "review_source_state",
                "expected_outcome_code": "stripe_invoice_paid_reported",
                "depends_on_work_item_refs": [],
                "expected_revision": 0, "expected_edit_token": None,
            }],
        })
        assert invalid.status_code in (409, 422)


def test_source_account_replaced_during_provider_read_never_persists(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        account, _ = local
        def replace(ref, op):
            account["id"] = "acct_REPLACED"
            return _paid(ref, op)
        monkeypatch.setattr(routes, "source_operation_receipt", replace)
        assert _verify(client, headers, saved, link).status_code in (403, 409, 422)
        assert not ledger._path().exists()


def test_tampered_retained_evidence_fails_closed_without_provider_read(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        assert _verify(client, headers, saved, link).status_code == 200
        with sqlite3.connect(ledger._path()) as db:
            db.execute("UPDATE evidence SET provider_state='unknown' WHERE seq=1")
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_a: pytest.fail("history must not read provider"))
        history = _history(client, headers, saved)
        assert history.status_code in (403, 409, 422)
        assert history.headers.get("cache-control", "no-store") == "no-store"
