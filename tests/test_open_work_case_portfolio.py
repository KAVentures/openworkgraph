"""FastAPI/SQLite portfolio acceptance with fake source reader, not live Stripe."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session, ensure_api_token
from server.secure_app import app

A, B, C = ["owg:w:" + char * 32 for char in "abc"]
R = "owg:r:" + "f" * 32
BASE = "/v1/open-work/declarations"
ENDPOINT = BASE + "/portfolio-status"


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
                        lambda _name: "test-portfolio-secret-" + "x" * 40)
    for ref in (A, B, C):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _saved(client, auth):
    saved = {}
    for ref, deps in ((A, []), (B, [A]), (C, [])):
        result = client.post(BASE, headers=auth, json={
            "confirm_owner_declaration": True,
            "work_item_ref": ref,
            "goal_code": "review_source_state",
            "expected_outcome_code": "source_state_rechecked",
            "depends_on_work_item_refs": deps,
            "expected_revision": 0,
            "expected_edit_token": None,
        })
        assert result.status_code == 200, result.text
        saved[ref] = result.json()
    return saved


def _payload(saved, refs=(B, C)):
    return {
        "confirm_source_scan": True,
        "max_pages": 2,
        "cases": [{
            "work_item_ref": ref,
            "expected_revision": saved[ref]["revision"],
            "expected_edit_token": saved[ref]["edit_token"],
        } for ref in refs],
    }


def _reported(ref):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "source_reported",
        "source_evidence": {
            "provider": "stripe", "source_state": "open",
            "status_scope": "stripe_invoice_open",
            "verified_at": "2026-11-01T20:00:00+00:00",
            "inventory_complete": False,
            "native_invoice_id": "in_PRIVATE",
        },
        "candidate": {"customer_email": "private@example.invalid", "amount": 9900},
        "related_owg_evidence": [{
            "resource_ref": R, "observed_event_count": 3,
            "observed_session_count": 1, "observed_actor_count": 1,
            "timeline_truncated": False,
            "timeline": [{"raw_private_text": "NEVER_EXPOSE"}],
        }],
        "related_record_history_incomplete": False,
    }


def _unknown(ref):
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "unknown",
        "reason": "case_not_in_bounded_inventory",
        "source_evidence": {"source_state": "unknown"},
    }


def test_portfolio_checks_independent_selected_cases_without_scanning_unselected_prerequisite(
    local, monkeypatch,
):
    client, auth = TestClient(app), _auth()
    saved = _saved(client, auth)
    calls = []
    def scan(ref, **options):
        calls.append((ref, options))
        return _reported(ref) if ref == B else _unknown(ref)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", scan)
    response = client.post(ENDPOINT, headers=auth, json=_payload(saved))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert calls == [
        (B, {"explicit_scan_authorized": True, "max_pages": 2}),
        (C, {"explicit_scan_authorized": True, "max_pages": 2}),
    ]
    body = response.json()
    assert body["format"] == "openworkgraph.local-reviewed-case-portfolio.v1"
    assert body["checked_case_count"] == 2
    assert [x["work_item_ref"] for x in body["case_statuses"]] == [B, C]
    assert [x["declared_prerequisite_count"] for x in body["case_statuses"]] == [1, 0]
    assert [x["unscanned_prerequisite_count"] for x in body["case_statuses"]] == [1, 0]
    assert [x["all_declared_prerequisites_selected"] for x in body["case_statuses"]] == [False, True]
    assert [x["source_reported_status"]["state"] for x in body["case_statuses"]] == ["open", "unknown"]
    assert body["case_statuses"][0]["retained_observation_summary"][0]["observed_event_count"] == 3
    assert body["case_statuses"][1]["source_reported_status"]["reason"] == "case_not_in_bounded_inventory"
    assert body["source_checks_sequential_not_atomic"] is True
    assert body["source_inventory_globally_complete"] is False
    for key in ("business_obligation_verified", "expected_outcome_verified",
                "customer_outcome_verified", "agent_execution_authorized"):
        assert body[key] is False
    for secret in ("in_PRIVATE", "private@example.invalid", "NEVER_EXPOSE",
                   "edit_token", "scope_ref", '"amount"'):
        assert secret not in response.text


def test_all_selected_cases_must_pass_exact_cas_before_any_provider_read(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _saved(client, auth)
    calls = []
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **kwargs: calls.append(ref) or _reported(ref))
    valid = _payload(saved)
    invalid_cases = [
        {**valid, "cases": []},
        {**valid, "cases": valid["cases"] * 3},
        {**valid, "cases": [valid["cases"][0]] * 2},
        {**valid, "cases": [valid["cases"][0],
                             {**valid["cases"][1], "expected_revision": 999}]},
        {**valid, "cases": [valid["cases"][0],
                             {**valid["cases"][1], "expected_edit_token": "0" * 32}]},
        {**valid, "cases": [valid["cases"][0],
                             {**valid["cases"][1], "tenant": "foreign"}]},
        {**valid, "confirm_source_scan": False},
        {**valid, "max_pages": True},
        {**valid, "max_pages": 4},
        {**valid, "extra": "unrecognized"},
    ]
    for invalid in invalid_cases:
        response = client.post(ENDPOINT, headers=auth, json=invalid)
        assert response.status_code in (403, 409, 422), response.text
    assert calls == []
    for headers in ({}, {"Authorization": "Bearer " + ensure_api_token()},
                    {**auth, "Origin": "https://evil.example"}):
        assert client.post(ENDPOINT, headers=headers, json=valid).status_code in (401, 403)
    assert calls == []
    local["id"] = None
    assert client.post(ENDPOINT, headers=auth, json=valid).status_code == 403
    assert calls == []


@pytest.mark.parametrize("change", ["source", "selected_review", "prerequisite"])
def test_mid_scan_source_review_or_prerequisite_revocation_stops_next_read(
    local, monkeypatch, change,
):
    client, auth = TestClient(app), _auth()
    saved = _saved(client, auth)
    calls = []
    def scan(ref, **kwargs):
        calls.append(ref)
        if change == "source":
            local["id"] = "acct_87654321"
        elif change == "selected_review":
            reviews.remove_review_intention(C)
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
    assert calls == [B]
    assert "in_PRIVATE" not in response.text


def test_provider_outage_is_unknown_and_contradiction_stops_remaining_reads(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _saved(client, auth)
    calls = []
    def outage(ref, **kwargs):
        calls.append(ref)
        if ref == B:
            raise OSError("secret provider outage")
        return _reported(ref)
    monkeypatch.setattr(routes, "stripe_open_work_case_context", outage)
    response = client.post(ENDPOINT, headers=auth, json=_payload(saved))
    assert response.status_code == 200, response.text
    assert calls == [B, C]
    assert response.json()["case_statuses"][0]["source_reported_status"]["state"] == "unknown"
    assert response.json()["case_statuses"][0]["source_reported_status"]["reason"] == "source_unavailable"
    assert "secret provider outage" not in response.text
    calls.clear()
    def conflicting(ref, **kwargs):
        calls.append(ref)
        bad = _reported(ref)
        bad["source_evidence"]["source_state"] = "paid"
        return bad
    monkeypatch.setattr(routes, "stripe_open_work_case_context", conflicting)
    response = client.post(ENDPOINT, headers=auth, json=_payload(saved))
    assert response.status_code == 422, response.text
    assert calls == [B]
    assert "in_PRIVATE" not in response.text


def test_selected_prerequisite_is_counted_as_checked_but_not_customer_verified(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    saved = _saved(client, auth)
    calls = []
    monkeypatch.setattr(routes, "stripe_open_work_case_context",
                        lambda ref, **kwargs: calls.append(ref) or _reported(ref))
    response = client.post(ENDPOINT, headers=auth, json=_payload(saved, refs=(A, B)))
    assert response.status_code == 200, response.text
    assert calls == [A, B]
    statuses = response.json()["case_statuses"]
    assert statuses[1]["declared_prerequisite_count"] == 1
    assert statuses[1]["unscanned_prerequisite_count"] == 0
    assert statuses[1]["all_declared_prerequisites_selected"] is True
    assert statuses[1]["prerequisite_status_not_implicitly_verified"] is True
    assert response.json()["customer_outcome_verified"] is False
