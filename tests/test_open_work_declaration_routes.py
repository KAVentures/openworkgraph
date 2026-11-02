"""Actual local REST lifecycle for source-bound owner declarations (no provider IO)."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as facts
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session, ensure_api_token
from server.secure_app import app

A = "owg:w:" + "a" * 32
B = "owg:w:" + "b" * 32
URL = "/v1/open-work/declarations"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = lambda: {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(facts, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    connection = {"account": None}
    monkeypatch.setattr(routes, "_read_metadata",
                        lambda _path: ({"account": connection["account"]}
                                       if connection["account"] else None))
    monkeypatch.setattr(routes, "_read_or_create_secret",
                        lambda _name: "test-local-scope-secret-" + "x" * 40)
    return connection


def _owner():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _save(ref, *, dependencies=None, revision=0, token=None, confirm=True):
    return {
        "confirm_owner_declaration": confirm,
        "work_item_ref": ref, "goal_code": "review_source_state",
        "expected_outcome_code": "source_state_rechecked",
        "depends_on_work_item_refs": dependencies or [],
        "expected_revision": revision, "expected_edit_token": token,
    }


def _review(ref):
    reviews.save_review_intention(
        ref, "assess_follow_up", provider_candidate_currently_open=True,
        now=datetime.now(timezone.utc),
    )


def test_default_off_and_only_dashboard_session_can_read_or_write(local):
    client = TestClient(app)
    bearer = {"Authorization": "Bearer " + ensure_api_token()}
    for headers in ({}, bearer):
        assert client.get(URL, headers=headers).status_code in (401, 403)
        assert client.post(URL, headers=headers, json=_save(A)).status_code in (401, 403)
        assert client.request("DELETE", URL, headers=headers,
                              json={"confirm_delete": True, "work_item_ref": A,
                                    "expected_revision": 1,
                                    "expected_edit_token": "a" * 32}).status_code in (401, 403)
    owner = _owner()
    empty = client.get(URL, headers=owner)
    assert empty.status_code == 200 and empty.json()["available"] is False
    assert empty.json()["items"] == []
    assert empty.headers["cache-control"] == "no-store"
    assert client.post(URL, headers=owner, json=_save(A)).status_code == 403
    assert not facts._path().exists()
    assert client.get(URL, headers={**owner, "Origin": "https://evil.example"}).status_code == 403


def test_real_rest_cas_dependency_invalidation_and_explicit_delete(local):
    local["account"] = "acct_12345678"
    _review(A)
    _review(B)
    client, owner = TestClient(app), _owner()
    assert client.post(URL, headers=owner, json=_save(A, confirm=False)).status_code == 403
    assert client.get(URL, headers=owner).json()["items"] == []
    first = client.post(URL, headers=owner, json=_save(A))
    assert first.status_code == 200, first.text
    a = first.json()
    assert a["revision"] == 1
    assert a["provenance"] == "explicit_owner_declaration_not_source_verified"
    assert a["expected_outcome_verified"] is False
    assert a["customer_outcome_verified"] is False
    assert a["agent_execution_authorized"] is False
    assert a["scope_ref"].startswith("owg:scope:")
    second = client.post(URL, headers=owner, json=_save(B, dependencies=[A]))
    assert second.status_code == 200, second.text
    assert len(client.get(URL, headers=owner).json()["items"]) == 2

    changed = client.post(URL, headers=owner, json=_save(
        A, revision=a["revision"], token=a["edit_token"]))
    assert changed.status_code == 200, changed.text
    new_a = changed.json()
    assert new_a["revision"] == 2
    assert len(client.get(URL, headers=owner).json()["items"]) == 1
    stale = client.post(URL, headers=owner, json=_save(
        A, revision=a["revision"], token=a["edit_token"]))
    assert stale.status_code == 409
    assert client.get(URL, headers=owner).json()["items"] == [new_a]

    delete_body = {"confirm_delete": True, "work_item_ref": A,
                   "expected_revision": new_a["revision"],
                   "expected_edit_token": new_a["edit_token"]}
    assert client.request("DELETE", URL, headers=owner,
                          json={**delete_body, "confirm_delete": False}).status_code == 403
    assert client.request("DELETE", URL, headers=owner,
                          json={**delete_body, "expected_revision": 1}).status_code == 409
    removed = client.request("DELETE", URL, headers=owner, json=delete_body)
    assert removed.status_code == 200, removed.text
    assert removed.json()["deleted"] is True
    assert removed.json()["source_read_performed"] is False
    assert client.get(URL, headers=owner).json()["items"] == []
    assert A in {x["work_item_ref"] for x in reviews.list_review_intentions()["items"]}



def test_delete_invalidates_transitive_dependents_without_erasing_reviews(local):
    local["account"] = "acct_12345678"
    for ref in (A, B):
        _review(ref)
    client, owner = TestClient(app), _owner()
    a_response = client.post(URL, headers=owner, json=_save(A))
    assert a_response.status_code == 200, a_response.text
    a = a_response.json()
    b_response = client.post(URL, headers=owner,
                             json=_save(B, dependencies=[A]))
    assert b_response.status_code == 200, b_response.text
    removed = client.request("DELETE", URL, headers=owner, json={
        "confirm_delete": True, "work_item_ref": A,
        "expected_revision": a["revision"],
        "expected_edit_token": a["edit_token"],
    })
    assert removed.status_code == 200, removed.text
    assert removed.json()["invalidated_declaration_count"] == 2
    assert client.get(URL, headers=owner).json()["items"] == []
    assert {x["work_item_ref"] for x in reviews.list_review_intentions()["items"]} == {A, B}



def test_source_scope_is_derived_not_caller_selected_and_revocation_scrubs(local):
    local["account"] = "acct_12345678"
    _review(A)
    client, owner = TestClient(app), _owner()
    a = client.post(URL, headers=owner, json=_save(A))
    assert a.status_code == 200, a.text
    original_scope = a.json()["scope_ref"]
    assert client.post(URL, headers=owner,
                       json={**_save(A), "scope_ref": original_scope}).status_code == 422
    local["account"] = "acct_87654321"
    assert client.get(URL, headers=owner).json()["items"] == []
    assert routes._source_scope() != original_scope
    # The actual source replacement/disconnect lifecycle removes reviews AND
    # declarations. Merely changing metadata in a fixture is not a real
    # connection event or a claim of an atomic provider-grant fence.
    reviews.clear_review_intentions()
    assert not facts._path().exists()
    assert client.post(URL, headers=owner, json=_save(A)).status_code == 403
    local["account"] = None
    assert client.get(URL, headers=owner).json()["available"] is False


@pytest.mark.parametrize("invalid", [
    b'{"confirm_owner_declaration":true,"confirm_owner_declaration":true}',
    b'{"confirm_owner_declaration":NaN}',
    b"{" + b"x" * 5000 + b"}",
    b'[]',
])
def test_reject_malformed_or_oversized_requests_before_any_write(local, invalid):
    local["account"] = "acct_12345678"
    _review(A)
    client, owner = TestClient(app), _owner()
    result = client.post(URL, headers={**owner, "content-type": "application/json"},
                         content=invalid)
    assert result.status_code in (413, 422)
    assert not facts._path().exists()
