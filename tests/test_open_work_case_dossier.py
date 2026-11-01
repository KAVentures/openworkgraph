"""FastAPI + real SQLite review-ledger acceptance for explicitly scanned dossiers.

The Stripe scan is a fake, injected at the EXISTING reader boundary. These
tests do not claim a live account, customer outcome or commercial benefit.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session, ensure_api_token
from server.secure_app import app

A = "owg:w:" + "a" * 32
B = "owg:w:" + "b" * 32
C = "owg:w:" + "c" * 32
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"
DOSSIER = BASE + "/dossier"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = lambda: {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    account = {"id": "acct_12345678"}
    monkeypatch.setattr(
        routes, "_read_metadata",
        lambda _path: {"account": account["id"]} if account["id"] else None,
    )
    monkeypatch.setattr(
        routes, "_read_or_create_secret",
        lambda _name: "test-dossier-source-secret-" + "x" * 40,
    )
    for ref in (A, B, C):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _owner():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _save(ref, deps=(), revision=0, token=None):
    return {
        "confirm_owner_declaration": True,
        "work_item_ref": ref,
        "goal_code": "review_source_state",
        "expected_outcome_code": "source_state_rechecked",
        "depends_on_work_item_refs": list(deps),
        "expected_revision": revision,
        "expected_edit_token": token,
    }


def _chain(client, owner):
    saved = {}
    for ref, deps in ((A, ()), (B, (A,)), (C, (B,))):
        result = client.post(BASE, headers=owner, json=_save(ref, deps))
        assert result.status_code == 200, result.text
        saved[ref] = result.json()
    return saved


def _request(ref, row, *, confirmed=True, max_pages=2):
    return {
        "work_item_ref": ref,
        "expected_revision": row["revision"],
        "expected_edit_token": row["edit_token"],
        "confirm_source_scan": confirmed,
        "max_pages": max_pages,
    }


def _reported(ref):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe",
            "source_state": "open",
            "status_scope": "stripe_invoice_open",
            "verified_at": "2026-11-01T19:00:00+00:00",
            "inventory_complete": False,
            "native_invoice_id": "in_SECRET_MUST_NOT_LEAK",
        },
        "candidate": {
            "customer_email": "private@example.invalid",
            "amount": 12345,
        },
        "related_owg_evidence": [{
            "resource_ref": R,
            "observed_event_count": 7,
            "observed_session_count": 2,
            "observed_actor_count": 1,
            "timeline_truncated": True,
            "timeline": [{"sensitive_text": "never return this"}],
        }],
        "related_record_history_incomplete": True,
    }


def _unknown(ref, reason="case_not_in_bounded_inventory"):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "unknown",
        "reason": reason,
        "source_evidence": {"source_state": "unknown"},
        "related_owg_evidence": [],
    }


def test_dossier_joins_real_reviewed_dag_with_explicit_fake_source_and_retained_counts(local, monkeypatch):
    client, owner = TestClient(app), _owner()
    saved = _chain(client, owner)
    calls = []
    def scan(ref, **options):
        calls.append((ref, options))
        return _reported(ref)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    response = client.post(DOSSIER, headers=owner, json=_request(C, saved[C]))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert calls == [(C, {"explicit_scan_authorized": True, "max_pages": 2})]
    assert result["format"] == "openworkgraph.local-reviewed-case-dossier.v1"
    assert result["owner_declaration"]["revision"] == 1
    assert result["owner_declaration"]["goal_code"] == "review_source_state"
    assert [x["work_item_ref"] for x in result["declared_prerequisites"]] == [A, B]
    assert result["declared_prerequisite_count"] == 2
    assert all(x["expected_outcome_verified"] is False for x in result["declared_prerequisites"])
    assert result["source_reported_status"]["state"] == "open"
    assert result["source_reported_status"]["bounded_inventory_complete"] is False
    assert result["retained_observation_summary"] == [{
        "resource_ref": R, "observed_event_count": 7,
        "observed_session_count": 2, "observed_actor_count": 1,
        "timeline_truncated": True, "observed_not_verified_handoff": True,
    }]
    assert result["related_record_history_incomplete"] is True
    assert result["customer_outcome_verified"] is False
    assert result["business_obligation_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["source_write_performed"] is False
    for forbidden in ("private@example.invalid", "in_SECRET_MUST_NOT_LEAK",
                      "never return this", "edit_token", "scope_ref", "amount"):
        assert forbidden not in response.text


def test_dossier_denials_and_stale_cas_do_not_invoke_source_reader(local, monkeypatch):
    client, owner = TestClient(app), _owner()
    saved = _chain(client, owner)
    calls = []
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda *args, **kwargs: calls.append(args) or _reported(C))
    bearer = {"Authorization": "Bearer " + ensure_api_token()}
    payload = _request(C, saved[C])
    for headers in ({}, bearer, {**owner, "Origin": "https://evil.example"}):
        assert client.post(DOSSIER, headers=headers, json=payload).status_code in (401, 403)
    for bad in (
        {**payload, "confirm_source_scan": False},
        {**payload, "max_pages": 0},
        {**payload, "max_pages": True},
        {**payload, "expected_revision": 0},
        {**payload, "expected_edit_token": "0" * 32},
        {**payload, "source_account": "acct_attacker"},
    ):
        assert client.post(DOSSIER, headers=owner, json=bad).status_code in (403, 409, 422)
    assert calls == []
    local["id"] = None
    assert client.post(DOSSIER, headers=owner, json=payload).status_code == 403
    assert calls == []


@pytest.mark.parametrize("reason", [
    "case_not_in_bounded_inventory",
    "case_no_longer_open_or_reference_invalid",
    "stripe_invoice_inventory_unavailable",
])
def test_unknown_or_partial_inventory_never_claims_payment(local, monkeypatch, reason):
    client, owner = TestClient(app), _owner()
    saved = _chain(client, owner)
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **kwargs: _unknown(ref, reason))
    response = client.post(DOSSIER, headers=owner, json=_request(C, saved[C]))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source_reported_status"] == {
        "availability": "unknown", "state": "unknown",
        "verified_at": None, "bounded_inventory_complete": False,
        "reason": reason, "provider_status_not_customer_outcome": True,
    }
    assert result["retained_observation_summary"] == []
    assert result["customer_outcome_verified"] is False
    assert result["expected_outcome_verified"] is False


@pytest.mark.parametrize("change", ["review", "source", "prerequisite_revision"])
def test_scan_race_fails_closed_without_returning_stale_owner_approval(local, monkeypatch, change):
    client, owner = TestClient(app), _owner()
    saved = _chain(client, owner)
    called = []
    def scan(ref, **options):
        called.append(ref)
        if change == "review":
            reviews.remove_review_intention(B)
        elif change == "source":
            local["id"] = "acct_87654321"
        else:
            declarations.save_case_declaration(
                A, routes._source_scope(),
                goal_code="review_source_state",
                expected_outcome_code="source_state_rechecked",
                expected_revision=saved[A]["revision"],
                expected_edit_token=saved[A]["edit_token"],
            )
        return _reported(ref)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    response = client.post(DOSSIER, headers=owner, json=_request(C, saved[C]))
    assert called == [C]
    assert response.status_code == 409, response.text
    assert "private@example.invalid" not in response.text


def test_conflicting_scan_identity_and_provider_claims_are_rejected(local, monkeypatch):
    client, owner = TestClient(app), _owner()
    saved = _chain(client, owner)
    def bad(ref, **kwargs):
        result = _reported(ref)
        result["source_evidence"]["source_state"] = "paid"
        return result
    monkeypatch.setattr(routes, "stripe_open_work_case_context", bad)
    response = client.post(DOSSIER, headers=owner, json=_request(C, saved[C]))
    assert response.status_code == 422
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **kwargs: _reported(A))
    assert client.post(DOSSIER, headers=owner,
                       json=_request(C, saved[C])).status_code == 422
