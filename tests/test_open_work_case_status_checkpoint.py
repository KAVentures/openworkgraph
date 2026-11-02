"""Real local FastAPI/SQLite acceptance for operator-carried case status checkpoints.

Fake Stripe reader only; these tests do NOT verify a real account, customer
outcome, agent causality or commercial benefit.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.open_work_case_status_checkpoint import validate_prior_checkpoint
from server.local_auth import create_dashboard_session, ensure_api_token
from server.secure_app import app

A = "owg:w:" + "a" * 32
B = "owg:w:" + "b" * 32
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"
CHECK = BASE + "/status-checkpoint"


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
        lambda _name: "checkpoint-test-source-secret-" + "x" * 40,
    )
    for ref in (A, B):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def owner():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def setup_chain(client, headers):
    saved = {}
    for ref, deps in ((A, []), (B, [A])):
        resp = client.post(BASE, headers=headers, json={
            "confirm_owner_declaration": True,
            "work_item_ref": ref,
            "goal_code": "review_source_state",
            "expected_outcome_code": "source_state_rechecked",
            "depends_on_work_item_refs": deps,
            "expected_revision": 0,
            "expected_edit_token": None,
        })
        assert resp.status_code == 200, resp.text
        saved[ref] = resp.json()
    return saved


def request(row, previous=None, **changes):
    data = {
        "work_item_ref": row["work_item_ref"],
        "expected_revision": row["revision"],
        "expected_edit_token": row["edit_token"],
        "confirm_source_scan": True,
        "max_pages": 2,
        "previous_checkpoint": previous,
    }
    return {**data, **changes}


def reported(ref, *, stamp=None, complete=False):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe",
            "source_state": "open",
            "status_scope": "stripe_invoice_open",
            "verified_at": stamp or datetime.now(timezone.utc).isoformat(),
            "inventory_complete": complete,
            "native_invoice_id": "in_SECRET_MUST_NOT_LEAK",
        },
        "candidate": {"customer_email": "private@example.invalid", "amount": 12345},
        "related_owg_evidence": [{
            "resource_ref": R,
            "observed_event_count": 1,
            "observed_session_count": 1,
            "observed_actor_count": 1,
            "timeline": [{"private_text": "never return"}],
        }],
    }


def unknown(ref, reason="case_no_longer_open_or_reference_invalid"):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "unknown",
        "reason": reason,
        "source_evidence": {"source_state": "unknown"},
        "related_owg_evidence": [],
    }


def test_portable_sealed_checkpoint_compares_open_to_unknown_without_claiming_paid(local, monkeypatch):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    calls = []
    results = [reported(B), unknown(B)]
    def scan(ref, **opts):
        calls.append((ref, opts))
        return results.pop(0)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    first = client.post(CHECK, headers=headers, json=request(saved[B]))
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    initial = first.json()
    assert initial["format"] == "openworkgraph.local-case-status-comparison.v1"
    assert initial["comparison"] == {"available": False}
    receipt = initial["checkpoint"]
    assert receipt["provider_state"] == "open"
    assert receipt["revision"] == 1
    assert len(receipt["seal"]) == 64
    assert receipt["bounded_inventory_complete"] is False
    assert "scope_ref" not in receipt and "edit_token" not in receipt
    second = client.post(CHECK, headers=headers, json=request(saved[B], receipt))
    assert second.status_code == 200, second.text
    result = second.json()
    assert result["checkpoint"]["provider_state"] == "unknown"
    assert result["comparison"]["transition"] == "open_to_unknown"
    assert result["comparison"]["source_state_change_not_proof_of_payment"] is True
    assert result["comparison"]["provider_operation_verified"] is False
    assert result["customer_outcome_verified"] is False
    assert result["expected_outcome_verified"] is False
    assert result["agent_execution_authorized"] is False
    assert result["no_server_side_source_status_persistence"] is True
    assert calls == [(B, {"explicit_scan_authorized": True, "max_pages": 2})] * 2
    for private in ("private@example.invalid", "in_SECRET_MUST_NOT_LEAK",
                    "never return", "acct_12345678", "edit_token", "scope_ref",
                    "amount", "native_invoice_id"):
        assert private not in first.text + second.text


@pytest.mark.parametrize("before,after,transition", [
    ("open", "open", "open_to_open"),
    ("unknown", "open", "unknown_to_open"),
    ("unknown", "unknown", "unknown_to_unknown"),
])
def test_conservative_status_transitions(local, monkeypatch, before, after, transition):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    results = [reported(B) if before == "open" else unknown(B),
               reported(B) if after == "open" else unknown(B)]
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **opts: results.pop(0))
    first = client.post(CHECK, headers=headers, json=request(saved[B]))
    assert first.status_code == 200, first.text
    second = client.post(
        CHECK, headers=headers,
        json=request(saved[B], first.json()["checkpoint"]))
    assert second.status_code == 200, second.text
    assert second.json()["comparison"]["transition"] == transition
    assert second.json()["provider_operation_verified"] is False


@pytest.mark.parametrize("tamper", [
    lambda p: p.update(provider_state="unknown"),
    lambda p: p.update(seal="0" * 64),
    lambda p: p.update(source_binding="0" * 64),
    lambda p: p.update(graph_binding="0" * 64),
    lambda p: p.update(revision=True),
    lambda p: p.update(bounded_inventory_complete="false"),
    lambda p: p.update(customer_email="private@example.invalid"),
    lambda p: p.update(issued_at="2020-01-01T00:00:00+00:00"),
])
def test_forged_or_stale_checkpoint_rejected_before_source_io(local, monkeypatch, tamper):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    calls = []
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **opts: calls.append(ref) or reported(ref))
    first = client.post(CHECK, headers=headers, json=request(saved[B]))
    assert first.status_code == 200, first.text
    prior = deepcopy(first.json()["checkpoint"])
    tamper(prior)
    second = client.post(CHECK, headers=headers, json=request(saved[B], prior))
    assert second.status_code in (409, 422), second.text
    assert calls == [B]
    assert "private@example.invalid" not in second.text


def test_checkpoint_invalidated_by_dependency_reapproval_and_source_switch(local, monkeypatch):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    calls = []
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **opts: calls.append(ref) or reported(ref))
    first = client.post(CHECK, headers=headers, json=request(saved[B]))
    assert first.status_code == 200, first.text
    checkpoint = first.json()["checkpoint"]
    updated = declarations.save_case_declaration(
        A, routes._source_scope(), goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        expected_revision=saved[A]["revision"],
        expected_edit_token=saved[A]["edit_token"],
    )
    assert updated["revision"] == 2
    stale = client.post(CHECK, headers=headers, json=request(saved[B], checkpoint))
    assert stale.status_code == 409, stale.text
    assert calls == [B]
    # A current case on a new source account cannot inherit an old checkpoint.
    local["id"] = "acct_other_account"
    switched = client.post(CHECK, headers=headers, json=request(saved[B], checkpoint))
    assert switched.status_code in (403, 409), switched.text
    assert calls == [B]


def test_review_revocation_and_retention_switch_refuse_reuse(local, monkeypatch):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    calls = []
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **opts: calls.append(ref) or reported(ref))
    first = client.post(CHECK, headers=headers, json=request(saved[B]))
    assert first.status_code == 200, first.text
    checkpoint = first.json()["checkpoint"]
    reviews.remove_review_intention(A)
    assert client.post(CHECK, headers=headers, json=request(saved[B], checkpoint)).status_code in (403, 409)
    assert calls == [B]
    # A session-only retention policy never allows durable declaration reuse.
    monkeypatch.setattr(declarations, "read_policy",
                        lambda: {"human_retention": {"mode": "ephemeral"}})
    assert client.post(CHECK, headers=headers, json=request(saved[B], checkpoint)).status_code in (403, 409)
    assert calls == [B]


@pytest.mark.parametrize("change", ["source", "review", "dependency"])
def test_mid_scan_revocation_never_returns_signed_checkpoint(local, monkeypatch, change):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    def scan(ref, **opts):
        if change == "source":
            local["id"] = "acct_other_account"
        elif change == "review":
            reviews.remove_review_intention(A)
        else:
            declarations.save_case_declaration(
                A, routes._source_scope(), goal_code="review_source_state",
                expected_outcome_code="source_state_rechecked",
                expected_revision=saved[A]["revision"],
                expected_edit_token=saved[A]["edit_token"],
            )
        return reported(ref)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    resp = client.post(CHECK, headers=headers, json=request(saved[B]))
    assert resp.status_code == 409, resp.text
    assert "seal" not in resp.text


def test_stale_source_timestamp_degrades_to_unknown_not_verified(local, monkeypatch):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **opts: reported(ref, stamp=stale, complete=True))
    resp = client.post(CHECK, headers=headers, json=request(saved[B]))
    assert resp.status_code == 200, resp.text
    receipt = resp.json()["checkpoint"]
    assert receipt["provider_state"] == "unknown"
    assert receipt["reason"] == "source_stale_or_clock_conflict"
    assert receipt["provider_verified_at"] is None
    assert receipt["bounded_inventory_complete"] is False


def test_conflicting_provider_status_fails_closed(local, monkeypatch):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    def bad(ref, **opts):
        result = reported(ref)
        result["source_evidence"]["source_state"] = "paid"
        return result
    monkeypatch.setattr(routes, "stripe_open_work_case_context", bad)
    resp = client.post(CHECK, headers=headers, json=request(saved[B]))
    assert resp.status_code == 422, resp.text
    assert "seal" not in resp.text


def test_expired_receipt_refuses_without_source_read(local, monkeypatch):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **opts: reported(ref))
    resp = client.post(CHECK, headers=headers, json=request(saved[B]))
    assert resp.status_code == 200, resp.text
    checkpoint = resp.json()["checkpoint"]
    before = declarations.list_case_declarations(routes._source_scope())
    from server.open_work_case_dossier import admitted_case_dossier_snapshot
    admitted, signature = admitted_case_dossier_snapshot(
        before, work_item_ref=B, expected_revision=saved[B]["revision"],
        expected_edit_token=saved[B]["edit_token"],
    )
    later = datetime.fromisoformat(checkpoint["expires_at"]) + timedelta(seconds=1)
    with pytest.raises(declarations.CaseDeclarationError):
        validate_prior_checkpoint(
            checkpoint, admitted, signature, scope=routes._source_scope(),
            secret=routes._read_or_create_secret(".open_work_reference_key"),
            now=later,
        )


def test_auth_body_and_confirmation_refusal_never_scan(local, monkeypatch):
    client, headers = TestClient(app), owner()
    saved = setup_chain(client, headers)
    calls = []
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **opts: calls.append(ref) or reported(ref))
    payload = request(saved[B])
    for bad_headers in ({}, {"Authorization": "Bearer " + ensure_api_token()},
                        {**headers, "Origin": "https://attacker.example"}):
        assert client.post(CHECK, headers=bad_headers, json=payload).status_code in (401, 403)
    for bad in (
        request(saved[B], confirm_source_scan=False),
        request(saved[B], max_pages=0),
        request(saved[B], max_pages=True),
        request(saved[B], expected_revision=0),
        request(saved[B], expected_edit_token="0" * 32),
        {**payload, "foreign_account": "acct_attacker"},
        request(saved[B], previous_checkpoint={"seal": "0" * 64}),
    ):
        assert client.post(CHECK, headers=headers, json=bad).status_code in (403, 409, 422)
    assert calls == []
