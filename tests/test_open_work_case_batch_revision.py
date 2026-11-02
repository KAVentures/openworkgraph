"""FastAPI/SQLite acceptance for atomic owner-DAG reapproval (synthetic only)."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.local_auth import create_dashboard_session, ensure_api_token
from server.secure_app import app

A, B, C, D = ["owg:w:" + ch * 32 for ch in "abcd"]
BASE = "/v1/open-work/declarations"
BATCH = BASE + "/batch"
REVISE = BASE + "/batch-revision"


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
        lambda _name: "batch-revision-test-secret-" + "x" * 40,
    )
    monkeypatch.setattr(
        routes, "stripe_open_work_case_context",
        lambda *_args, **_kwargs: pytest.fail("revision must not read provider"),
    )
    for ref in (A, B, C, D):
        reviews.save_review_intention(
            ref, "assess_follow_up", provider_candidate_currently_open=True,
            now=datetime.now(timezone.utc),
        )
    return account


def auth():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def new_item(ref, deps=()):
    return {
        "work_item_ref": ref, "goal_code": "review_source_state",
        "expected_outcome_code": "source_state_rechecked",
        "depends_on_work_item_refs": list(deps),
        "expected_revision": 0, "expected_edit_token": None,
    }


def seeded(client, headers):
    response = client.post(BATCH, headers=headers, json={
        "confirm_batch_create": True,
        "items": [new_item(C, [B]), new_item(D), new_item(B, [A]), new_item(A)],
    })
    assert response.status_code == 200, response.text
    return {x["work_item_ref"]: x for x in response.json()["items"]}


def revised(row, deps=None):
    return {
        "work_item_ref": row["work_item_ref"],
        "goal_code": "verify_operation",
        "expected_outcome_code": "operation_receipt_reviewed",
        "depends_on_work_item_refs": (
            row["depends_on_work_item_refs"] if deps is None else list(deps)
        ),
        "expected_revision": row["revision"],
        "expected_edit_token": row["edit_token"],
    }


def payload(items, invalidated=()):
    return {
        "confirm_batch_revision": True,
        "confirm_cascade_invalidation": True,
        "expected_invalidated_work_item_refs": list(invalidated),
        "items": list(items),
    }


def current(client, headers):
    result = client.get(BASE, headers=headers)
    assert result.status_code == 200, result.text
    return {x["work_item_ref"]: x for x in result.json()["items"]}


def test_reapprove_out_of_order_full_chain_without_losing_dependents(local):
    client, headers = TestClient(app), auth()
    old = seeded(client, headers)
    result = client.post(REVISE, headers=headers, json=payload([
        revised(old[C]), revised(old[A]), revised(old[B]),
    ]))
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["reapproved_count"] == 3
    assert data["invalidated_work_item_refs"] == []
    assert data["atomic_local_commit"] is True
    assert data["source_read_performed"] is False
    assert data["customer_outcome_verified"] is False
    assert data["agent_execution_authorized"] is False
    assert result.headers["cache-control"] == "no-store"
    rows = current(client, headers)
    assert set(rows) == {A, B, C, D}
    for ref in (A, B, C):
        assert rows[ref]["revision"] == 2
        assert rows[ref]["edit_token"] == old[ref]["edit_token"]
        assert rows[ref]["created_at"] == old[ref]["created_at"]
        assert rows[ref]["expires_at"] <= old[ref]["expires_at"]
    assert rows[D] == old[D]
    graph = client.get(BASE + "/graph", headers=headers)
    assert graph.status_code == 200
    assert {(e["prerequisite_ref"], e["dependent_ref"])
            for e in graph.json()["edges"]} == {(A, B), (B, C)}


def test_acknowledged_cascade_deletes_only_omitted_descendants(local):
    client, headers = TestClient(app), auth()
    old = seeded(client, headers)
    result = client.post(REVISE, headers=headers, json=payload(
        [revised(old[B]), revised(old[A])], [C],
    ))
    assert result.status_code == 200, result.text
    assert result.json()["invalidated_work_item_refs"] == [C]
    assert result.json()["invalidated_declaration_count"] == 1
    rows = current(client, headers)
    assert set(rows) == {A, B, D}
    assert rows[B]["revision"] == rows[A]["revision"] == 2
    assert rows[D] == old[D]


def test_unacknowledged_or_unexpected_cascade_rolls_back(local):
    client, headers = TestClient(app), auth()
    old = seeded(client, headers)
    for expected in ([], [B], [C]):
        rejected = client.post(REVISE, headers=headers, json=payload(
            [revised(old[A])], expected,
        ))
        assert rejected.status_code in (409, 422), rejected.text
        assert current(client, headers) == old
    success = client.post(REVISE, headers=headers, json=payload(
        [revised(old[A])], [B, C],
    ))
    assert success.status_code == 200, success.text
    assert set(current(client, headers)) == {A, D}


def test_stale_incarnation_or_cycle_or_omitted_prerequisite_is_atomic(local):
    client, headers = TestClient(app), auth()
    old = seeded(client, headers)
    stale = {**revised(old[A]), "expected_edit_token": "0" * 32}
    cases = [
        payload([stale, revised(old[B]), revised(old[C])]),
        payload([revised(old[A], [C]), revised(old[B]), revised(old[C])]),
        payload([revised(old[A]), revised(old[C])], [B]),
        payload([revised(old[A]), revised(old[B], [C])], [C]),
    ]
    for body in cases:
        result = client.post(REVISE, headers=headers, json=body)
        assert result.status_code in (409, 422), result.text
        assert current(client, headers) == old


def test_confirmation_session_origin_source_and_scope_denial(local):
    client, headers = TestClient(app), auth()
    old = seeded(client, headers)
    good = payload([revised(old[A])], [B, C])
    assert client.post(REVISE, headers=headers, json={
        **good, "confirm_cascade_invalidation": False,
    }).status_code == 403
    assert client.post(REVISE, json=good).status_code == 401
    bearer = {"Authorization": "Bearer " + ensure_api_token()}
    assert client.post(REVISE, headers=bearer, json=good).status_code == 403
    assert client.post(REVISE, headers={
        **headers, "Origin": "https://foreign.invalid",
    }, json=good).status_code == 403
    local["id"] = "acct_87654321"
    assert client.post(REVISE, headers=headers, json=good).status_code in (409, 422)
    assert current(client, headers) == {}
    local["id"] = "acct_12345678"
    assert current(client, headers) == old


def test_mid_transaction_review_revocation_rolls_back(local, monkeypatch):
    client, headers = TestClient(app), auth()
    old = seeded(client, headers)
    original = declarations._reviewed
    calls = {"n": 0}

    def changing(now):
        calls["n"] += 1
        result = original(now)
        if calls["n"] >= 3:
            return {ref: window for ref, window in result.items() if ref != B}
        return result

    monkeypatch.setattr(declarations, "_reviewed", changing)
    response = client.post(REVISE, headers=headers, json=payload(
        [revised(old[A]), revised(old[B]), revised(old[C])],
    ))
    assert response.status_code in (403, 409), response.text
    monkeypatch.setattr(declarations, "_reviewed", original)
    assert current(client, headers) == old


def test_mid_transaction_retention_change_rolls_back(local, monkeypatch):
    client, headers = TestClient(app), auth()
    old = seeded(client, headers)
    original = declarations._retention_days
    calls = {"n": 0}

    def changing():
        calls["n"] += 1
        return 1 if calls["n"] >= 3 else original()

    monkeypatch.setattr(declarations, "_retention_days", changing)
    response = client.post(REVISE, headers=headers, json=payload(
        [revised(old[A]), revised(old[B]), revised(old[C])],
    ))
    assert response.status_code in (403, 409), response.text
    monkeypatch.setattr(declarations, "_retention_days", original)
    assert current(client, headers) == old


def test_preview_union_feeds_confirmed_cas_revision(local):
    client, headers = TestClient(app), auth()
    old = seeded(client, headers)
    preview = client.post(BASE + "/batch-revision-impact", headers=headers, json={
        "cases": [
            {key: old[ref][key] for key in ("work_item_ref", "revision", "edit_token")}
            for ref in (B, A)
        ],
    })
    # The preview API uses explicit expected_* names, not GET response names.
    assert preview.status_code == 422, preview.text
    versions = [
        {
            "work_item_ref": ref,
            "expected_revision": old[ref]["revision"],
            "expected_edit_token": old[ref]["edit_token"],
        }
        for ref in (B, A)
    ]
    preview = client.post(BASE + "/batch-revision-impact", headers=headers, json={
        "cases": versions,
    })
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["expected_invalidated_work_item_refs"] == [C]
    assert body["reapproved_case_refs"] == [A, B]
    assert body["mutation_performed"] is False
    assert body["source_read_performed"] is False
    assert current(client, headers) == old
    committed = client.post(REVISE, headers=headers, json=payload(
        [revised(old[B]), revised(old[A])],
        body["expected_invalidated_work_item_refs"],
    ))
    assert committed.status_code == 200, committed.text
    assert set(current(client, headers)) == {A, B, D}
    stale = client.post(BASE + "/batch-revision-impact", headers=headers, json={
        "cases": versions,
    })
    assert stale.status_code == 409


def test_preview_rejects_duplicate_versions_and_source_revocation(local):
    client, headers = TestClient(app), auth()
    old = seeded(client, headers)
    version = {
        "work_item_ref": A, "expected_revision": old[A]["revision"],
        "expected_edit_token": old[A]["edit_token"],
    }
    assert client.post(BASE + "/batch-revision-impact", headers=headers, json={
        "cases": [version, version],
    }).status_code == 422
    assert client.post(BASE + "/batch-revision-impact", json={
        "cases": [version],
    }).status_code == 401
    local["id"] = None
    assert client.post(BASE + "/batch-revision-impact", headers=headers, json={
        "cases": [version],
    }).status_code == 403
    local["id"] = "acct_12345678"
    assert current(client, headers) == old
