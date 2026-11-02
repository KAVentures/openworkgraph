"""Real FastAPI/SQLite contract acceptance; fake Stripe reader, not live pilot."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session, ensure_api_token
from server.secure_app import app

A, B, C, D = ["owg:w:" + char * 32 for char in "abcd"]
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"
ENDPOINT = BASE + "/dependency-status"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = lambda: {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    account = {"id": "acct_12345678"}
    monkeypatch.setattr(routes, "_read_metadata",
                        lambda _path: {"account": account["id"]} if account["id"] else None)
    monkeypatch.setattr(routes, "_read_or_create_secret",
                        lambda _name: "test-dependency-secret-" + "x" * 40)
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


def _request(ref, saved, *, max_cases=4, confirmed=True):
    return {
        "work_item_ref": ref,
        "expected_revision": saved[ref]["revision"],
        "expected_edit_token": saved[ref]["edit_token"],
        "confirm_source_scan": confirmed,
        "max_pages": 2,
        "max_cases": max_cases,
    }


def _reported(ref):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe", "source_state": "open",
            "status_scope": "stripe_invoice_open",
            "verified_at": "2026-11-01T19:00:00+00:00",
            "inventory_complete": False,
            "native_invoice_id": "in_PRIVATE_MUST_NOT_LEAK",
        },
        "candidate": {"customer_email": "private@example.invalid", "amount": 9000},
        "related_owg_evidence": [{
            "resource_ref": R,
            "observed_event_count": 2, "observed_session_count": 1,
            "observed_actor_count": 1, "timeline_truncated": False,
            "timeline": [{"secret": "NEVER_RETURN_THIS"}],
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


def test_dependency_scan_reuses_reviewed_dag_and_minimizes_provider_data(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    def scan(ref, **options):
        calls.append((ref, options))
        return _reported(ref) if ref != B else _unknown(ref)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    response = client.post(ENDPOINT, headers=auth, json=_request(D, saved))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert calls == [(ref, {"explicit_scan_authorized": True, "max_pages": 2})
                     for ref in (A, B, C, D)]
    body = response.json()
    assert body["format"] == "openworkgraph.local-reviewed-dependency-status.v1"
    assert body["checked_case_count"] == 4
    assert body["declared_prerequisite_count"] == 3
    assert body["unscanned_prerequisite_count"] == 0
    assert body["all_declared_prerequisites_scanned"] is True
    assert [x["work_item_ref"] for x in body["case_statuses"]] == [A, B, C, D]
    assert [x["source_reported_status"]["state"] for x in body["case_statuses"]] == [
        "open", "unknown", "open", "open",
    ]
    assert body["case_statuses"][-1]["role"] == "requested_case"
    assert body["case_statuses"][0]["retained_observation_summary"][0]["observed_event_count"] == 2
    assert body["source_checks_sequential_not_atomic"] is True
    assert body["customer_outcome_verified"] is False
    assert body["business_obligation_verified"] is False
    assert body["agent_execution_authorized"] is False
    for secret in ("in_PRIVATE", "private@example.invalid", "NEVER_RETURN_THIS",
                   "edit_token", "scope_ref", '"amount"'):
        assert secret not in response.text


def test_cap_checks_prefix_and_target_and_marks_omitted_prerequisites(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **kwargs: calls.append(ref) or _reported(ref))
    response = client.post(ENDPOINT, headers=auth, json=_request(D, saved, max_cases=2))
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == [A, D]
    assert body["unscanned_prerequisite_count"] == 2
    assert body["all_declared_prerequisites_scanned"] is False
    assert body["checked_case_count"] == 2
    assert [x["work_item_ref"] for x in body["case_statuses"]] == [A, D]


def test_denials_and_stale_revision_never_read_provider(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **kwargs: calls.append(ref) or _reported(ref))
    payload = _request(D, saved)
    bearer = {"Authorization": "Bearer " + ensure_api_token()}
    for headers in ({}, bearer, {**auth, "Origin": "https://evil.example"}):
        assert client.post(ENDPOINT, headers=headers, json=payload).status_code in (401, 403)
    for bad in (
        {**payload, "confirm_source_scan": False},
        {**payload, "max_cases": 0},
        {**payload, "max_cases": True},
        {**payload, "max_cases": 5},
        {**payload, "max_pages": 4},
        {**payload, "expected_revision": 0},
        {**payload, "expected_edit_token": "0" * 32},
        {**payload, "foreign_account": "acct_other"},
    ):
        assert client.post(ENDPOINT, headers=auth, json=bad).status_code in (403, 409, 422)
    assert calls == []
    local["id"] = None
    assert client.post(ENDPOINT, headers=auth, json=payload).status_code == 403
    assert calls == []


@pytest.mark.parametrize("change", ["source", "review", "prerequisite"])
def test_mid_scan_revocation_stops_before_next_provider_read(local, monkeypatch, change):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    def scan(ref, **kwargs):
        calls.append(ref)
        if change == "source":
            local["id"] = "acct_87654321"
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
    response = client.post(ENDPOINT, headers=auth, json=_request(D, saved))
    assert response.status_code == 409, response.text
    assert calls == [A]
    assert "in_PRIVATE" not in response.text


def test_provider_exception_is_unknown_not_customer_completion(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    def scan(ref, **kwargs):
        calls.append(ref)
        if ref == B:
            raise OSError("private provider failure")
        return _reported(ref)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    response = client.post(ENDPOINT, headers=auth, json=_request(C, saved))
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == [A, B, C]
    assert body["case_statuses"][1]["source_reported_status"]["reason"] == "source_unavailable"
    assert body["case_statuses"][1]["source_reported_status"]["state"] == "unknown"
    assert body["customer_outcome_verified"] is False
    assert "private provider failure" not in response.text


def test_conflicting_first_source_receipt_stops_remaining_scans(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _chain(client, auth)
    calls = []
    def scan(ref, **kwargs):
        calls.append(ref)
        bad = _reported(ref)
        bad["source_evidence"]["source_state"] = "paid"
        return bad
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    response = client.post(ENDPOINT, headers=auth, json=_request(D, saved))
    assert response.status_code == 422, response.text
    assert calls == [A]
    assert "in_PRIVATE" not in response.text
