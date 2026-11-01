"""Real local FastAPI/SQLite atomic owner-DAG acceptance; no live provider calls."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session, ensure_api_token
from server.secure_app import app


A, B, C, D = ["owg:w:" + letter * 32 for letter in "abcd"]
BASE = "/v1/open-work/declarations"
BATCH = BASE + "/batch"


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
        lambda _name: "atomic-batch-test-secret-" + "x" * 40,
    )
    monkeypatch.setattr(
        routes, "stripe_open_work_case_context",
        lambda *_args, **_kwargs: pytest.fail("batch must not call provider"),
    )
    for ref in (A, B, C, D):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def _auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _item(ref, deps=()):
    return {
        "work_item_ref": ref,
        "goal_code": "review_source_state",
        "expected_outcome_code": "source_state_rechecked",
        "depends_on_work_item_refs": list(deps),
        "expected_revision": 0,
        "expected_edit_token": None,
    }


def _batch(*items):
    return {"confirm_batch_create": True, "items": list(items)}


def test_atomic_out_of_order_dag_and_existing_graph(local):
    client, auth = TestClient(app), _auth()
    result = client.post(BATCH, headers=auth, json=_batch(
        _item(C, [B]), _item(B, [A]), _item(A),
    ))
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["format"] == "openworkgraph.local-case-declaration-batch.v1"
    assert payload["created_count"] == 3
    assert payload["atomic_local_commit"] is True
    assert [row["work_item_ref"] for row in payload["items"]] == [A, B, C]
    assert all(row["revision"] == 1 and len(row["edit_token"]) == 32
               for row in payload["items"])
    assert payload["source_read_performed"] is False
    assert payload["customer_outcome_verified"] is False
    assert payload["agent_execution_authorized"] is False
    assert result.headers["cache-control"] == "no-store"
    graph = client.get(BASE + "/graph", headers=auth)
    assert graph.status_code == 200, graph.text
    assert [node["work_item_ref"] for node in graph.json()["nodes"]] == [A, B, C]
    assert {(edge["prerequisite_ref"], edge["dependent_ref"])
            for edge in graph.json()["edges"]} == {(A, B), (B, C)}


def test_existing_approved_prerequisite_and_all_or_none_stale_create(local):
    client, auth = TestClient(app), _auth()
    existing = client.post(BASE, headers=auth, json={
        "confirm_owner_declaration": True, **_item(A),
    })
    assert existing.status_code == 200, existing.text
    result = client.post(BATCH, headers=auth, json=_batch(
        _item(C, [B]), _item(B, [A]),
    ))
    assert result.status_code == 200, result.text
    assert [row["work_item_ref"] for row in result.json()["items"]] == [B, C]

    # One colliding create must roll back every new row in that batch.
    collision = client.post(BATCH, headers=auth, json=_batch(
        _item(D), _item(B),
    ))
    assert collision.status_code == 409, collision.text
    listed = client.get(BASE, headers=auth)
    assert listed.status_code == 200, listed.text
    assert {row["work_item_ref"] for row in listed.json()["items"]} == {A, B, C}


@pytest.mark.parametrize("items", [
    [_item(A, [B]), _item(B, [A])],
    [_item(A), _item(A)],
    [_item(A, [A])],
    [_item(A, [B]), _item(B, [C]), _item(C, [A])],
    [{**_item(A), "expected_revision": True}],
    [{**_item(A), "goal_code": ["not", "a", "code"]}],
    [{**_item(A), "expected_edit_token": "0" * 32}],
])
def test_invalid_batch_never_partially_creates(local, items):
    client, auth = TestClient(app), _auth()
    response = client.post(BATCH, headers=auth, json=_batch(*items))
    assert response.status_code in (409, 422), response.text
    listed = client.get(BASE, headers=auth)
    assert listed.status_code == 200
    assert listed.json()["items"] == []


def test_authorization_confirmation_source_revocation_and_scope(local):
    client, auth = TestClient(app), _auth()
    assert client.post(BATCH, headers=auth, json={
        "confirm_batch_create": False, "items": [_item(A)],
    }).status_code == 403
    assert client.post(BATCH, json=_batch(_item(A))).status_code == 403
    bearer = {"Authorization": "Bearer " + ensure_api_token()}
    assert client.post(BATCH, headers=bearer, json=_batch(_item(A))).status_code == 403
    foreign = {**auth, "Origin": "https://foreign.invalid"}
    assert client.post(BATCH, headers=foreign, json=_batch(_item(A))).status_code == 403
    local["id"] = None
    assert client.post(BATCH, headers=auth, json=_batch(_item(A))).status_code in (403, 409)
    local["id"] = "acct_12345678"
    assert client.get(BASE, headers=auth).json()["items"] == []
    good = client.post(BATCH, headers=auth, json=_batch(_item(A)))
    assert good.status_code == 200, good.text
    local["id"] = "acct_87654321"
    assert client.get(BASE, headers=auth).json()["items"] == []


def test_review_revoked_during_transaction_rolls_back_every_row(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    original = declarations._reviewed
    calls = {"count": 0}

    def changing_review(now):
        calls["count"] += 1
        result = original(now)
        if calls["count"] >= 3:
            return {ref: window for ref, window in result.items() if ref != B}
        return result

    monkeypatch.setattr(declarations, "_reviewed", changing_review)
    result = client.post(BATCH, headers=auth, json=_batch(_item(A), _item(B, [A])))
    assert result.status_code in (403, 409), result.text
    monkeypatch.setattr(declarations, "_reviewed", original)
    assert client.get(BASE, headers=auth).json()["items"] == []


def test_retention_changed_before_commit_rolls_back(local, monkeypatch):
    client, auth = TestClient(app), _auth()
    original = declarations._retention_days
    calls = {"count": 0}

    def changing_retention():
        calls["count"] += 1
        return 1 if calls["count"] >= 3 else original()

    monkeypatch.setattr(declarations, "_retention_days", changing_retention)
    result = client.post(BATCH, headers=auth, json=_batch(_item(A), _item(B, [A])))
    assert result.status_code in (403, 409), result.text
    monkeypatch.setattr(declarations, "_retention_days", original)
    assert client.get(BASE, headers=auth).json()["items"] == []


def test_cross_account_prerequisite_cannot_be_rebound(local):
    client, auth = TestClient(app), _auth()
    first = client.post(BATCH, headers=auth, json=_batch(_item(A)))
    assert first.status_code == 200, first.text
    local["id"] = "acct_87654321"
    rejected = client.post(BATCH, headers=auth, json=_batch(_item(B, [A])))
    assert rejected.status_code in (409, 422), rejected.text
    assert client.get(BASE, headers=auth).json()["items"] == []
    local["id"] = "acct_12345678"
    assert {row["work_item_ref"] for row in client.get(BASE, headers=auth).json()["items"]} == {A}
