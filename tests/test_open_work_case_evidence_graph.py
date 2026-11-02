"""FastAPI + private SQLite vertical acceptance with a synthetic Stripe reader.

No real provider/customer/agent outcome is verified by these tests.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session, ensure_api_token
from server.secure_app import app

A, B, C, D = ["owg:w:" + char * 32 for char in "abcd"]
BASE = "/v1/open-work/declarations"
ENDPOINT = BASE + "/evidence-graph"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = lambda: {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    account = {"id": "acct_synthetic_123"}
    monkeypatch.setattr(
        routes, "_read_metadata",
        lambda _path: {"account": account["id"]} if account["id"] else None,
    )
    monkeypatch.setattr(
        routes, "_read_or_create_secret",
        lambda _name: "synthetic-evidence-graph-secret-" + "x" * 40,
    )
    for ref in (A, B, C, D):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _chain(client, auth):
    saved = {}
    for ref, deps in ((A, []), (B, [A]), (C, [B]), (D, [C])):
        response = client.post(BASE, headers=auth, json={
            "confirm_owner_declaration": True,
            "work_item_ref": ref,
            "goal_code": "review_source_state",
            "expected_outcome_code": "source_state_rechecked",
            "depends_on_work_item_refs": deps,
            "expected_revision": 0,
            "expected_edit_token": None,
        })
        assert response.status_code == 200, response.text
        saved[ref] = response.json()
    return saved


def _payload(saved, *, cases=4, confirmed=True):
    return {
        "work_item_ref": D,
        "expected_revision": saved[D]["revision"],
        "expected_edit_token": saved[D]["edit_token"],
        "confirm_source_scan": confirmed,
        "max_cases": cases,
        "max_pages": 2,
    }


def _reported(ref, *, offset=0):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe", "source_state": "open",
            "status_scope": "stripe_invoice_open",
            "inventory_complete": False,
            "verified_at": (datetime.now(timezone.utc) + timedelta(minutes=offset)).isoformat(),
            "native_invoice_id": "in_PRIVATE_MUST_NOT_LEAK",
        },
        "candidate": {"customer_email": "private@example.invalid", "amount": 9000},
        "related_owg_evidence": [{
            "resource_ref": "owg:r:" + "f" * 32,
            "observed_event_count": 2,
            "observed_session_count": 1,
            "observed_actor_count": 1,
            "timeline_truncated": False,
            "timeline": [{"secret": "PRIVATE_TIMELINE"}],
        }],
        "related_record_history_incomplete": False,
    }


def _unknown(ref):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref, "availability": "unknown",
        "reason": "case_not_in_bounded_inventory",
        "source_evidence": {"source_state": "unknown"},
    }


def test_typed_graph_separates_declared_edges_fresh_source_and_unknown_outcomes(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    def scan(ref, **options):
        calls.append((ref, options))
        if ref == B:
            return _unknown(ref)
        if ref == C:
            return _reported(ref, offset=-60)
        if ref == D:
            return _reported(ref, offset=60)
        return _reported(ref)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    response = client.post(ENDPOINT, headers=auth, json=_payload(saved))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert calls == [(ref, {"explicit_scan_authorized": True, "max_pages": 2})
                     for ref in (A, B, C, D)]
    graph = response.json()
    assert graph["format"] == "openworkgraph.local-case-outcome-evidence-graph.v1"
    assert graph["case_count"] == 4
    assert graph["checked_case_count"] == 4
    assert graph["unscanned_prerequisite_count"] == 0
    assert graph["source_evidence_counts"] == {
        "fresh_source_open": 1, "source_unknown": 1,
        "source_stale": 1, "source_clock_conflict": 1, "unscanned": 0,
    }
    dependencies = [(e["from"], e["to"]) for e in graph["edges"]
                    if e["type"] == "declared_prerequisite"]
    assert dependencies == [(A, B), (B, C), (C, D)]
    assert all(e["provenance"] == "explicit_owner_declaration"
               and not e["source_verified"] for e in graph["edges"]
               if e["type"] == "declared_prerequisite")
    source_nodes = [node for node in graph["nodes"]
                    if node["type"] == "provider_status_evidence"]
    assert [(n["state"], n["freshness"]) for n in source_nodes] == [
        ("open", "fresh"), ("unknown", "unknown"),
        ("unknown", "stale"), ("unknown", "clock_conflict"),
    ]
    assert sum(e["type"] == "source_reported_open" for e in graph["edges"]) == 1
    assert sum(e["type"] == "declared_expected_outcome" for e in graph["edges"]) == 4
    assert graph["inferred_edge_count"] == 0
    assert graph["expected_outcome_verified"] is False
    assert graph["customer_outcome_verified"] is False
    assert graph["provider_operation_verified"] is False
    assert graph["agent_execution_authorized"] is False
    for secret in ("in_PRIVATE", "private@example.invalid", "PRIVATE_TIMELINE",
                   "edit_token", "scope_ref", '"amount"', "acct_synthetic"):
        assert secret not in response.text


def test_bounded_scan_preserves_unscanned_dependency_edges_without_inventing_state(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    monkeypatch.setattr(
        routes, "stripe_open_work_case_context",
        lambda ref, **kwargs: calls.append(ref) or _reported(ref),
    )
    response = client.post(ENDPOINT, headers=auth, json=_payload(saved, cases=2))
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == [A, D]
    assert body["checked_case_count"] == 2
    assert body["unscanned_prerequisite_count"] == 2
    assert body["source_evidence_counts"]["unscanned"] == 2
    assert [gap["work_item_ref"] for gap in body["evidence_gaps"]
            if gap["kind"] == "prerequisite_not_scanned"] == [B, C]
    assert len([e for e in body["edges"] if e["type"] == "declared_prerequisite"]) == 3
    assert len([n for n in body["nodes"] if n["type"] == "provider_status_evidence"]) == 2
    assert body["source_inventory_globally_complete"] is False


def test_explicit_consent_auth_and_exact_revision_denied_before_provider_io(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    monkeypatch.setattr(
        routes, "stripe_open_work_case_context",
        lambda ref, **kwargs: calls.append(ref) or _reported(ref),
    )
    payload = _payload(saved)
    for headers in ({}, {"Authorization": "Bearer " + ensure_api_token()},
                    {**auth, "Origin": "https://evil.example"}):
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code in (401, 403)
    for bad in (
        {**payload, "confirm_source_scan": False},
        {**payload, "max_cases": 0},
        {**payload, "max_cases": True},
        {**payload, "max_cases": 5},
        {**payload, "max_pages": 4},
        {**payload, "expected_revision": 0},
        {**payload, "expected_edit_token": "0" * 32},
        {**payload, "source_account": "acct_foreign"},
    ):
        assert client.post(ENDPOINT, headers=auth, json=bad).status_code in (403, 409, 422)
    assert calls == []


@pytest.mark.parametrize("change", ["source", "review", "prerequisite"])
def test_mid_scan_revocation_stops_next_provider_read(local, monkeypatch, change):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    def scan(ref, **options):
        calls.append(ref)
        if change == "source":
            local["id"] = "acct_other"
        elif change == "review":
            reviews.remove_review_intention(B)
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
    response = client.post(ENDPOINT, headers=auth, json=_payload(saved))
    assert response.status_code == 409, response.text
    assert calls == [A]
    assert "PRIVATE_TIMELINE" not in response.text


def test_source_outage_is_unknown_and_old_dependency_status_route_still_works(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    def scan(ref, **kwargs):
        if ref == B:
            raise OSError("provider private outage")
        return _reported(ref)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    response = client.post(ENDPOINT, headers=auth, json=_payload(saved))
    assert response.status_code == 200, response.text
    assert response.json()["source_evidence_counts"]["source_unknown"] == 1
    assert "provider private outage" not in response.text
    legacy = client.post(BASE + "/dependency-status", headers=auth, json=_payload(saved))
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["format"] == "openworkgraph.local-reviewed-dependency-status.v1"


def test_conflicting_provider_evidence_fails_closed_before_second_read(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    def scan(ref, **kwargs):
        calls.append(ref)
        item = _reported(ref)
        item["source_evidence"]["source_state"] = "paid"
        return item
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    response = client.post(ENDPOINT, headers=auth, json=_payload(saved))
    assert response.status_code == 422, response.text
    assert calls == [A]
    assert "in_PRIVATE" not in response.text
