"""Real FastAPI/private-SQLite path with a synthetic source receipt.

No synthetic receipt certifies a live Stripe account, customer settlement,
business uplift or an agent's causal contribution.
"""
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server import open_work_case_operation_ledger as ledger
from server.open_work_case_dossier import admitted_case_dossier_snapshot
from server.open_work_case_status_checkpoint import _bindings, _canonical, _mac
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



RENEW = BASE + "/source-goals/renew-record-link"


def _renew(client, headers, saved, link, *, confirm=True):
    return client.post(RENEW, headers=headers, json={
        **_cas(saved), "confirm_historical_review": confirm, "record_link": link,
    })


def _signed_expired_link(saved, link, *, days_ago=0):
    """Reproduce an expired but originally legitimate signed source link."""
    scope = routes._source_scope()
    secret = routes._read_or_create_secret(".open_work_reference_key")
    snapshot = declarations.list_case_declarations(scope)
    admitted, signature = admitted_case_dossier_snapshot(snapshot, **_cas(saved))
    _graph, _source, key = _bindings(signature, scope, secret)
    now = datetime.now(timezone.utc)
    unsigned = {k: v for k, v in link.items() if k != "seal"}
    unsigned["issued_at"] = (now - timedelta(hours=23, minutes=30, days=days_ago)).isoformat()
    unsigned["expires_at"] = (now - timedelta(minutes=1, days=days_ago)).isoformat()
    assert unsigned["expires_at"] < admitted["owner_declaration"]["expires_at"]
    return {
        **unsigned,
        "seal": _mac(key, b"case-record-link", _canonical(unsigned)),
    }


def test_expired_link_renewed_from_real_sqlite_history_and_rechecked(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        assert _verify(client, headers, saved, link).status_code == 200
        expired = _signed_expired_link(saved, link)
        monkeypatch.setattr(
            routes, "source_operation_receipt",
            lambda *_: pytest.fail("renewal or rejected old link must not contact Stripe"),
        )
        assert _verify(client, headers, saved, expired).status_code in (403, 409, 422)
        with sqlite3.connect(ledger._path()) as db:
            count_before = db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        renewed = _renew(client, headers, saved, expired)
        assert renewed.status_code == 200, renewed.text
        body = renewed.json()
        assert body["prior_link_expired"] is True
        assert body["historical_provider_check_count"] == 1
        assert body["historical_last_provider_state"] == "paid"
        assert body["provider_goal_met_now"] is False
        assert body["source_read_performed"] is False
        assert body["new_evidence_appended"] is False
        assert body["agent_execution_authorized"] is False
        assert renewed.headers["cache-control"] == "no-store"
        with sqlite3.connect(ledger._path()) as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == count_before
        assert all(secret not in renewed.text for secret in (
            "in_PRIVATE", "acct_PRIVATE", "PRIVATE@example.invalid",
        ))
        monkeypatch.setattr(
            routes, "stripe_open_work_case_context",
            lambda *_a, **_kw: pytest.fail("recheck must not require open inventory"),
        )
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        checked = _verify(client, headers, saved, body["record_link"])
        assert checked.status_code == 200, checked.text
        assert checked.json()["provider_check_count"] == 2


def test_no_check_forged_link_and_unconfirmed_renewal_refused(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        monkeypatch.setattr(
            routes, "source_operation_receipt",
            lambda *_: pytest.fail("renewal must not contact provider"),
        )
        assert _renew(client, headers, saved, link).status_code in (403, 409, 422)
        assert _renew(client, headers, saved, dict(link, seal="0" * 64)).status_code in (403, 409, 422)
        assert _renew(client, headers, saved, link, confirm=False).status_code == 403
        assert _renew(client, {}, saved, link).status_code in (401, 403)
        assert not ledger._path().exists()


def test_dispute_and_old_out_of_retention_link_block_renewal(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        assert _verify(client, headers, saved, link).status_code == 200
        ancient = _signed_expired_link(saved, link, days_ago=8)
        assert _renew(client, headers, saved, ancient).status_code in (403, 409, 422)
        disputed = client.post(BASE + "/operation-ledger/dispute", headers=headers, json={
            **_cas(saved), "confirm_dispute": True,
            "target_sequence": 1, "reason_code": "operator_dispute",
        })
        assert disputed.status_code == 200, disputed.text
        monkeypatch.setattr(
            routes, "source_operation_receipt",
            lambda *_: pytest.fail("disputed renewal must not contact provider"),
        )
        assert _renew(client, headers, saved, link).status_code in (403, 409, 422)


def test_source_switch_review_revocation_and_stale_cas_deny_renewal(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        saved = _declare(client, headers).json()
        link = _link(client, headers, saved, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        assert _verify(client, headers, saved, link).status_code == 200
        account, _policy = local
        account["id"] = "acct_OTHER"
        assert _renew(client, headers, saved, link).status_code in (403, 409, 422)
        account["id"] = "acct_PRIVATE"
        stale = dict(saved, edit_token="0" * 32)
        assert _renew(client, headers, stale, link).status_code in (403, 409, 422)
        reviews.remove_review_intention(A)
        assert _renew(client, headers, saved, link).status_code in (403, 409, 422)
