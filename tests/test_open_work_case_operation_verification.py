"""Real local FastAPI/SQLite acceptance with synthetic Stripe source and receipts.

No live Stripe test account, real customer outcome or agent causality is
established by these fake-source regressions.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session
from server.secure_app import app

A, B = ["owg:w:" + c * 32 for c in "ab"]
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"
LINK = BASE + "/operation-link"
VERIFY = BASE + "/verify-operation"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = lambda: {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    account = {"id": "acct_test_123"}
    monkeypatch.setattr(routes, "_read_metadata",
                        lambda _path: {"account": account["id"]} if account["id"] else None)
    monkeypatch.setattr(routes, "_read_or_create_secret",
                        lambda _name: "case-record-link-test-secret-" + "x" * 40)
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _cases(client, headers):
    saved = {}
    for ref, deps in ((A, []), (B, [A])):
        result = client.post(BASE, headers=headers, json={
            "confirm_owner_declaration": True, "work_item_ref": ref,
            "goal_code": "verify_operation",
            "expected_outcome_code": "operation_receipt_reviewed",
            "depends_on_work_item_refs": deps,
            "expected_revision": 0, "expected_edit_token": None,
        })
        assert result.status_code == 200, result.text
        saved[ref] = result.json()
    return saved


def _payload(saved):
    return {
        "work_item_ref": B,
        "expected_revision": saved[B]["revision"],
        "expected_edit_token": saved[B]["edit_token"],
    }


def _scan(ref, *, linked=True, incomplete=False):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe", "source_state": "open",
            "status_scope": "stripe_invoice_open",
            "inventory_complete": False,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "native_id": "in_PRIVATE",
        },
        "related_owg_evidence": ([{
            "resource_ref": R,
            "observed_event_count": 3,
            "observed_session_count": 1,
            "observed_actor_count": 1,
            "timeline": [{"customer_email": "PRIVATE@example.invalid"}],
        }] if linked else []),
        "related_record_history_incomplete": incomplete,
        "candidate": {"amount": 987654, "customer_name": "PRIVATE"},
    }


def _paid(ref, operation):
    assert ref == R and operation == "stripe_invoice_paid"
    return {
        "format": "openworkgraph.source-operation-receipt.v1",
        "resource_ref": R, "expected_operation": "stripe_invoice_paid",
        "availability": "source_reported", "operation_verified": True,
        "provider": "stripe", "provider_record_kind": "invoice",
        "provider_reported_state": "paid",
        "verification_scope": "authenticated_source_provider_operational_state",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False, "read_only": True,
        "native_id": "in_PRIVATE",
    }


def _issue(client, headers, saved):
    return client.post(LINK, headers=headers, json={
        **_payload(saved), "confirm_source_scan": True,
        "max_pages": 2, "resource_ref": R,
    })


def _verify(client, headers, saved, link):
    return client.post(VERIFY, headers=headers, json={
        **_payload(saved), "confirm_source_verification": True,
        "record_link": link,
    })


def test_source_join_then_independent_paid_receipt_after_open_inventory_disappears(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    scans, receipts = [], []
    def scan(ref, **options):
        scans.append((ref, options))
        return _scan(ref)
    def receipt(ref, op):
        receipts.append((ref, op))
        return _paid(ref, op)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    monkeypatch.setattr(routes, "source_operation_receipt", receipt)
    first = _issue(client, headers, saved)
    assert first.status_code == 200, first.text
    link = first.json()["record_link"]
    assert link["resource_ref"] == R
    assert first.json()["provider_operation_verified"] is False
    assert scans == [(B, {"explicit_scan_authorized": True, "max_pages": 2})]
    # Later verification does not consult the open-invoice inventory, which
    # may no longer contain this invoice after the provider reports PAID.
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda *_a, **_k: pytest.fail("unexpected open-inventory rescan"))
    second = _verify(client, headers, saved, link)
    assert second.status_code == 200, second.text
    result = second.json()
    assert result["provider_operation_state"] == "paid"
    assert result["provider_operation_verified"] is True
    assert result["customer_outcome_verified"] is False
    assert result["agent_caused_operation_verified"] is False
    assert result["expected_outcome_verified"] is False
    assert receipts == [(R, "stripe_invoice_paid")]
    assert first.headers["cache-control"] == second.headers["cache-control"] == "no-store"
    for private in ("in_PRIVATE", "PRIVATE@example.invalid", "987654",
                    "acct_test_123", "customer_name", "scope_ref", "edit_token"):
        assert private not in first.text + second.text


@pytest.mark.parametrize("tamper", [
    lambda p: p.update(seal="0" * 64),
    lambda p: p.update(graph_binding="0" * 64),
    lambda p: p.update(source_binding="0" * 64),
    lambda p: p.update(resource_ref="owg:r:" + "a" * 32),
    lambda p: p.update(revision=True),
    lambda p: p.update(expected_operation="stripe_refund_processor_succeeded"),
    lambda p: p.update(issued_at="2020-01-01T00:00:00+00:00"),
    lambda p: p.update(customer_name="PRIVATE"),
])
def test_tampered_or_stale_link_denied_before_source_io(local, monkeypatch, tamper):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", lambda ref, **opts: _scan(ref))
    link = _issue(client, headers, saved).json()["record_link"]
    tampered = deepcopy(link)
    tamper(tampered)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a, **_k: pytest.fail("provider IO before admission"))
    result = _verify(client, headers, saved, tampered)
    assert result.status_code in (409, 422), result.text


@pytest.mark.parametrize("linked,incomplete", [(False, False), (True, True)])
def test_no_exact_complete_source_join_never_issues_link(local, monkeypatch, linked, incomplete):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **opts: _scan(ref, linked=linked, incomplete=incomplete))
    result = _issue(client, headers, saved)
    assert result.status_code in (409, 422)
    assert "record_link" not in result.text


def test_prerequisite_reapproval_and_source_switch_invalidate_link(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", lambda ref, **opts: _scan(ref))
    link = _issue(client, headers, saved).json()["record_link"]
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a, **_k: pytest.fail("stale link must not read provider"))
    revision = client.post(BASE, headers=headers, json={
        "confirm_owner_declaration": True, "work_item_ref": A,
        "goal_code": "review_source_state",
        "expected_outcome_code": "source_state_rechecked",
        "depends_on_work_item_refs": [],
        "expected_revision": saved[A]["revision"],
        "expected_edit_token": saved[A]["edit_token"],
    })
    assert revision.status_code == 200, revision.text
    assert _verify(client, headers, saved, link).status_code in (403, 409, 422)


def test_provider_outage_is_unknown_not_paid(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", lambda ref, **opts: _scan(ref))
    link = _issue(client, headers, saved).json()["record_link"]
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("private provider error")))
    result = _verify(client, headers, saved, link)
    assert result.status_code == 200, result.text
    assert result.json()["provider_operation_state"] == "unknown"
    assert result.json()["provider_operation_verified"] is False
    assert "private provider error" not in result.text


def test_revocation_during_provider_check_refuses_verified_response(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", lambda ref, **opts: _scan(ref))
    link = _issue(client, headers, saved).json()["record_link"]
    def revoke(ref, op):
        reviews.remove_review_intention(B)
        return _paid(ref, op)
    monkeypatch.setattr(routes, "source_operation_receipt", revoke)
    result = _verify(client, headers, saved, link)
    assert result.status_code in (403, 409, 422)
    assert "provider_operation_verified" not in result.text


def test_auth_and_confirmation_refuse_before_source_io(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda *_a, **_k: pytest.fail("unauthorized source scan"))
    data = {**_payload(saved), "confirm_source_scan": False,
            "max_pages": 2, "resource_ref": R}
    assert client.post(LINK, headers=headers, json=data).status_code == 403
    assert client.post(LINK, json={**data, "confirm_source_scan": True}).status_code == 403
