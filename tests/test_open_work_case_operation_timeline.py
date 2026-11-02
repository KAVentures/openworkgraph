"""FastAPI/SQLite synthetic acceptance for portable provider-operation history.

A fake Stripe reader is NOT live-provider or customer-outcome verification.
"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session
from server.secure_app import app

A, B = "owg:w:" + "a" * 32, "owg:w:" + "b" * 32
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = lambda: {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    account = {"id": "acct_timeline_test"}
    monkeypatch.setattr(routes, "_read_metadata",
                        lambda _path: {"account": account["id"]} if account["id"] else None)
    monkeypatch.setattr(routes, "_read_or_create_secret",
                        lambda _name: "timeline-local-test-secret-" + "x" * 40)
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
        "candidate": {"customer": "PRIVATE", "amount": 987654},
    }


def _paid(ref, op):
    assert ref == R and op == "stripe_invoice_paid"
    return {
        "format": "openworkgraph.source-operation-receipt.v1",
        "resource_ref": R, "expected_operation": op,
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


def _case_payload(saved):
    return {
        "work_item_ref": B, "expected_revision": saved[B]["revision"],
        "expected_edit_token": saved[B]["edit_token"],
    }


def _link(client, headers, saved):
    result = client.post(BASE + "/operation-link", headers=headers, json={
        **_case_payload(saved), "confirm_source_scan": True,
        "max_pages": 2, "resource_ref": R,
    })
    assert result.status_code == 200, result.text
    return result.json()["record_link"]


def _timeline(client, headers, saved, link, prior=None, **overrides):
    return client.post(BASE + "/operation-timeline", headers=headers, json={
        **_case_payload(saved), "confirm_source_verification": True,
        "record_link": link, "prior_timeline": prior, **overrides,
    })


def test_paid_then_unknown_preserves_historical_evidence_without_claiming_customer_success(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    link = _link(client, headers, saved)
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda *_a, **_k: pytest.fail("timeline must not rescan open inventory"))
    calls = []
    def source(ref, op):
        calls.append(ref)
        return _paid(ref, op) if len(calls) == 1 else None
    monkeypatch.setattr(routes, "source_operation_receipt", source)
    first = _timeline(client, headers, saved, link)
    assert first.status_code == 200, first.text
    assert first.json()["latest_provider_state"] == "paid"
    second = _timeline(client, headers, saved, link, first.json()["timeline"])
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["latest_provider_state"] == "unknown"
    assert body["historical_paid_observation_count"] == 1
    assert body["historical_unknown_observation_count"] == 1
    assert [r["provider_state"] for r in body["timeline"]["checks"]] == ["paid", "unknown"]
    assert body["customer_outcome_verified"] is False
    assert body["agent_execution_authorized"] is False
    assert body["server_side_timeline_persistence"] is False
    assert len(calls) == 2
    assert "PRIVATE" not in str(body)
    assert "in_PRIVATE" not in str(body)
    assert R not in str(body)


def test_tampered_replayed_or_full_history_refuses_before_provider_read(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    link = _link(client, headers, saved)
    reads = []
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda ref, op: (reads.append(ref), _paid(ref, op))[1])
    first = _timeline(client, headers, saved, link)
    assert first.status_code == 200, first.text
    prior = first.json()["timeline"]
    forged = deepcopy(prior)
    forged["checks"][0]["provider_state"] = "unknown"
    refused = _timeline(client, headers, saved, link, forged)
    assert refused.status_code in (409, 422)
    assert reads == [R]
    for _ in range(7):
        result = _timeline(client, headers, saved, link, prior)
        assert result.status_code == 200, result.text
        prior = result.json()["timeline"]
    assert len(prior["checks"]) == 8
    refused = _timeline(client, headers, saved, link, prior)
    assert refused.status_code in (409, 422)
    assert len(reads) == 8
    other = deepcopy(link)
    other["resource_ref"] = "owg:r:" + "e" * 32
    assert _timeline(client, headers, saved, other).status_code in (409, 422)
    assert len(reads) == 8


def test_review_revocation_and_source_switch_refuse_before_read(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    link = _link(client, headers, saved)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("revoked operation must not read provider"))
    local["id"] = "acct_other"
    assert _timeline(client, headers, saved, link).status_code in (403, 409, 422)
    local["id"] = "acct_timeline_test"
    reviews.remove_review_intention(A)
    assert _timeline(client, headers, saved, link).status_code in (403, 409, 422)


def test_mid_read_revocation_discards_result(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    link = _link(client, headers, saved)
    def revoke(ref, op):
        reviews.remove_review_intention(A)
        return _paid(ref, op)
    monkeypatch.setattr(routes, "source_operation_receipt", revoke)
    result = _timeline(client, headers, saved, link)
    assert result.status_code in (403, 409, 422)


def test_explicit_consent_and_session_required_without_provider_read(local, monkeypatch):
    client, headers = TestClient(app), _auth()
    saved = _cases(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", _scan)
    link = _link(client, headers, saved)
    monkeypatch.setattr(routes, "source_operation_receipt",
                        lambda *_a: pytest.fail("unauthorized provider read"))
    body = {**_case_payload(saved), "record_link": link, "prior_timeline": None,
            "confirm_source_verification": False}
    assert client.post(BASE + "/operation-timeline", headers=headers, json=body).status_code == 403
    body["confirm_source_verification"] = True
    assert client.post(BASE + "/operation-timeline", json=body).status_code == 403
