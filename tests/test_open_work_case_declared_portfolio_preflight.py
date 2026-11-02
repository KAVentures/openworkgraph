"""FastAPI/private-SQLite acceptance for sealed portfolio preflight.

Synthetic provider receipts do not establish live Stripe or customer acceptance.
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
PORTFOLIO = BASE + "/source-goals/verify-portfolio-payments"
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
        lambda _name: "selected-portfolio-secret-" + "x" * 40,
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


def _declare(client, headers, ref, dependencies=(), outcome="stripe_invoice_paid_reported"):
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


def _setup(client, headers, monkeypatch, *, dependent=False):
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    b = _declare(client, headers, B)
    a = _declare(client, headers, A, (B,) if dependent else ())
    selections = []
    for row, resource in ((a, RA), (b, RB)):
        linked = client.post(BASE + "/operation-link", headers=headers, json={
            **_cas(row), "confirm_source_scan": True,
            "max_pages": 2, "resource_ref": resource,
        })
        assert linked.status_code == 200, linked.text
        selections.append({**_cas(row), "record_link": linked.json()["record_link"]})
    return a, b, selections


def _verify(client, headers, selections, *, confirm=True):
    return client.post(PORTFOLIO, headers=headers, json={
        "confirm_source_verification": confirm,
        "selections": selections,
    })



PREVIEW = BASE + "/source-goals/preview-portfolio-payments"


def _preview(client, headers, selections, *, confirm=True):
    return client.post(PREVIEW, headers=headers, json={
        "confirm_historical_review": confirm, "selections": selections,
    })


def _verify_token(client, headers, selections, token):
    return client.post(PORTFOLIO, headers=headers, json={
        "confirm_source_verification": True, "selections": selections,
        "expected_preflight": token,
    })


def test_preview_then_exact_portfolio_verification_persists_atomically(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("preview must never read Stripe"))
        preview = _preview(client, headers, selections)
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data["format"] == "openworkgraph.local-case-declared-portfolio-payment-preflight.v1"
        assert [row["work_item_ref"] for row in data["cases"]] == [A, B]
        assert data["selected_case_count"] == 2
        assert all(row["historical_provider_state"] == "unknown" for row in data["cases"])
        assert all(row["remaining_private_ledger_event_slots"] == 32 for row in data["cases"])
        assert data["source_read_performed"] is False
        assert data["new_evidence_appended"] is False
        assert data["global_private_ledger_case_capacity_not_reserved"] is True
        assert data["customer_outcome_verified"] is False
        assert data["agent_execution_authorized"] is False
        assert preview.headers["cache-control"] == "no-store"
        assert not ledger._path().exists()
        for sensitive in (RA, RB, "acct_PRIVATE", "in_PRIVATE", "PRIVATE@example.invalid"):
            assert sensitive not in preview.text
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        checked = _verify_token(client, headers, selections, data["expected_preflight"])
        assert checked.status_code == 200, checked.text
        assert checked.json()["fresh_provider_paid_count"] == 2
        with sqlite3.connect(ledger._path()) as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2


def test_existing_portfolio_verification_without_preview_remains_compatible(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        assert _verify(client, headers, selections).status_code == 200


def test_forged_expired_wrong_order_and_missing_preflight_rejected_before_io(local, monkeypatch):
    from datetime import timedelta
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        token = _preview(client, headers, selections).json()["expected_preflight"]
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("invalid checkpoint must stop source IO"))
        invalid = [
            dict(token, seal="0" * 64),
            dict(token, snapshot_seal="0" * 64),
            dict(token, work_item_refs=[B, A]),
            dict(token, issued_at="invalid"),
            dict(token, expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()),
            dict(token, extra="unapproved"),
            None,
        ]
        for candidate in invalid:
            response = _verify_token(client, headers, selections, candidate)
            assert response.status_code in (403, 409, 422), response.text
        swapped = _verify_token(client, headers, list(reversed(selections)), token)
        assert swapped.status_code in (403, 409, 422), swapped.text
        assert not ledger._path().exists()


def test_new_provider_check_invalidates_portfolio_checkpoint(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, _b, selections = _setup(client, headers, monkeypatch)
        token = _preview(client, headers, selections).json()["expected_preflight"]
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        result = client.post(SINGLE, headers=headers, json={
            **_cas(a), "confirm_source_verification": True,
            "record_link": selections[0]["record_link"],
        })
        assert result.status_code == 200, result.text
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("stale checkpoint must stop source IO"))
        stale = _verify_token(client, headers, selections, token)
        assert stale.status_code == 409, stale.text
        with sqlite3.connect(ledger._path()) as db:
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1


def test_dispute_invalidates_checkpoint_and_new_preview_discloses_dispute(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        a, _b, selections = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt", _paid)
        assert client.post(SINGLE, headers=headers, json={
            **_cas(a), "confirm_source_verification": True,
            "record_link": selections[0]["record_link"],
        }).status_code == 200
        token = _preview(client, headers, selections).json()["expected_preflight"]
        disputed = client.post(BASE + "/operation-ledger/dispute", headers=headers, json={
            **_cas(a), "confirm_dispute": True,
            "target_sequence": 1, "reason_code": "operator_dispute",
        })
        assert disputed.status_code == 200, disputed.text
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("dispute must stop source IO"))
        assert _verify_token(client, headers, selections, token).status_code == 409
        new = _preview(client, headers, selections)
        assert new.status_code == 200, new.text
        assert new.json()["cases"][0]["last_check_disputed"] is True
        assert new.json()["cases"][0]["historical_provider_state"] == "unknown"


def test_revocation_and_source_account_switch_refuse_stale_portfolio_preflight(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        token = _preview(client, headers, selections).json()["expected_preflight"]
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("revoked preflight must stop source IO"))
        reviews.remove_review_intention(B)
        assert _verify_token(client, headers, selections, token).status_code in (403, 409, 422)
        assert not ledger._path().exists()
        reviews.save_review_intention(
            B, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
        local["id"] = "acct_OTHER"
        assert _verify_token(client, headers, selections, token).status_code in (403, 409, 422)
        assert not ledger._path().exists()


def test_invalid_selection_confirmation_and_auth_refuse_portfolio_preview(local, monkeypatch):
    with TestClient(app) as client:
        headers = _auth()
        _a, _b, selections = _setup(client, headers, monkeypatch)
        monkeypatch.setattr(routes, "source_operation_receipt",
                            lambda *_: pytest.fail("preview must never read provider"))
        assert _preview(client, headers, selections, confirm=False).status_code == 403
        assert _preview(client, headers, selections[:1]).status_code in (403, 409, 422)
        assert _preview(client, headers, [selections[0], selections[0]]).status_code in (403, 409, 422)
        assert client.post(PREVIEW, json={
            "confirm_historical_review": True, "selections": selections,
        }).status_code == 403
        assert not ledger._path().exists()
