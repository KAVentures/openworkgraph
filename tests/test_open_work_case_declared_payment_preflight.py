"""FastAPI/private-SQLite acceptance for historical payment preflight.

Synthetic provider receipts are not live Stripe/customer acceptance.
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



PREVIEW = BASE + "/source-goals/preview-dependency-payments"


def _preview(client, headers, root, links):
    return client.post(PREVIEW, headers=headers, json={
        **_cas(root), "confirm_historical_review": True, "record_links": links,
    })


def _verify_preview(client, headers, root, links, token):
    return client.post(BATCH, headers=headers, json={
        **_cas(root), "confirm_source_verification": True,
        "record_links": links, "expected_preflight": token,
    })


def test_preview_then_exact_token_check_persists_both_cases(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, _b, links = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("preview must never read provider"))
        preview = _preview(client, headers, a, links)
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["format"] == "openworkgraph.local-case-declared-payment-preflight.v1"
        assert [row["work_item_ref"] for row in body["cases"]] == [B, A]
        assert body["reviewed_case_count"] == 2
        assert all(row["historical_provider_state"] == "unknown" for row in body["cases"])
        assert all(row["remaining_private_ledger_event_slots"] == 32 for row in body["cases"])
        assert body["source_read_performed"] is False
        assert body["customer_outcome_verified"] is False
        assert body["agent_execution_authorized"] is False
        assert not ledger._path().exists()
        for sensitive in (RA, RB, "acct_PRIVATE", "in_PRIVATE", "PRIVATE@example.invalid"):
            assert sensitive not in preview.text
        assert preview.headers["cache-control"] == "no-store"
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        result = _verify_preview(client, headers, a, links, body["expected_preflight"])
        assert result.status_code == 200, result.text
        assert result.json()["fresh_provider_paid_count"] == 2
        with sqlite3.connect(ledger._path()) as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2


def test_existing_explicit_verification_without_preview_stays_compatible(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, _b, links = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        assert _batch(client, headers, a, links).status_code == 200


def test_forged_and_expired_preflight_refused_before_provider_io(local, monkeypatch):
    from datetime import timedelta
    with TestClient(app) as client:
        headers = _auth()
        a, _b, links = _setup(client, headers, monkeypatch)
        token = _preview(client, headers, a, links).json()["expected_preflight"]
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("invalid preview must not contact provider"))
        bad = [
            dict(token, seal="0" * 64),
            dict(token, snapshot_seal="0" * 64),
            dict(token, work_item_ref=B),
            dict(token, issued_at="invalid"),
            dict(token, expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()),
            dict(token, extra="unapproved"),
            None,
        ]
        for candidate in bad:
            result = _verify_preview(client, headers, a, links, candidate)
            assert result.status_code in (403, 409, 422), result.text
        assert not ledger._path().exists()


def test_new_payment_check_after_preview_stales_checkpoint_before_source_io(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, b, links = _setup(client, headers, monkeypatch)
        token = _preview(client, headers, a, links).json()["expected_preflight"]
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        checked = client.post(SINGLE, headers=headers, json={
            **_cas(b), "confirm_source_verification": True, "record_link": links[0],
        })
        assert checked.status_code == 200, checked.text
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("stale preflight must stop source read"))
        result = _verify_preview(client, headers, a, links, token)
        assert result.status_code == 409, result.text
        with sqlite3.connect(ledger._path()) as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1


def test_dispute_after_preview_invalidates_checkpoint_and_plan_reports_dispute(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, b, links = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        checked = client.post(SINGLE, headers=headers, json={
            **_cas(b), "confirm_source_verification": True, "record_link": links[0],
        })
        assert checked.status_code == 200, checked.text
        old = _preview(client, headers, a, links).json()["expected_preflight"]
        disputed = client.post(BASE + "/operation-ledger/dispute", headers=headers, json={
            **_cas(b), "confirm_dispute": True,
            "target_sequence": 1, "reason_code": "operator_dispute",
        })
        assert disputed.status_code == 200, disputed.text
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("disputed history must stop provider read"))
        result = _verify_preview(client, headers, a, links, old)
        assert result.status_code == 409, result.text
        new = _preview(client, headers, a, links)
        assert new.status_code == 200, new.text
        b_row = next(r for r in new.json()["cases"] if r["work_item_ref"] == B)
        assert b_row["disputed_check_count"] == 1
        assert b_row["last_check_disputed"] is True
        assert b_row["historical_provider_state"] == "unknown"
        assert b_row["provider_goal_met_now"] is False


def test_revocation_and_account_change_fail_closed_without_provider_io(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, _b, links = _setup(client, headers, monkeypatch)
        token = _preview(client, headers, a, links).json()["expected_preflight"]
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("revoked preview must not read provider"))
        reviews.remove_review_intention(A)
        assert _verify_preview(client, headers, a, links, token).status_code in (403, 409, 422)
        assert _preview(client, headers, a, links).status_code in (403, 409, 422)
        local["id"] = "acct_CHANGED"
        assert _verify_preview(client, headers, a, links, token).status_code in (403, 409, 422)


def test_invalid_links_and_missing_confirmation_refuse_preflight_without_provider(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, _b, links = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("preflight cannot read provider"))
        for candidate in (
            links[:1],
            links + [links[0]],
            [links[0], dict(links[1], seal="0" * 64)],
        ):
            result = _preview(client, headers, a, candidate)
            assert result.status_code in (403, 409, 422), result.text
        assert client.post(PREVIEW, headers=headers, json={
            **_cas(a), "confirm_historical_review": False, "record_links": links,
        }).status_code == 403
        # No credentials: the global auth middleware refuses before the route.
        assert client.post(PREVIEW, json={
            **_cas(a), "confirm_historical_review": True, "record_links": links,
        }).status_code == 401
        assert not ledger._path().exists()
