"""End-to-end dashboard/SQLite acceptance for the declared case dependency graph.

All source accounts and provider reads are synthetic. These tests do not
establish customer outcomes, tenant-provider acceptance or agent permissions.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server import open_work_case_declarations as declarations
from server import open_work_reviews as reviews
from server import open_work_declaration_routes as routes
from server.open_work_case_graph import project_declared_case_graph
from server.local_auth import create_dashboard_session, ensure_api_token
from server.secure_app import app

A = "owg:w:" + "a" * 32
B = "owg:w:" + "b" * 32
C = "owg:w:" + "c" * 32
D = "owg:w:" + "d" * 32
BASE = "/v1/open-work/declarations"
GRAPH = BASE + "/graph"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = lambda: {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(declarations, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    connection = {"account": None}
    monkeypatch.setattr(
        routes, "_read_metadata",
        lambda _path: ({"account": connection["account"]}
                       if connection["account"] else None),
    )
    monkeypatch.setattr(routes, "_read_or_create_secret",
                        lambda _name: "test-local-graph-secret-" + "x" * 40)
    return connection


def _owner():
    return {"Authorization": "OWG-Session " + create_dashboard_session()}


def _review(ref):
    reviews.save_review_intention(
        ref, "assess_follow_up", provider_candidate_currently_open=True,
        now=datetime.now(timezone.utc),
    )


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


def test_graph_real_rest_three_level_declarations_and_revision_invalidation(local):
    local["account"] = "acct_test_12345678"
    for ref in (A, B, C, D):
        _review(ref)
    client, headers = TestClient(app), _owner()
    a = client.post(BASE, headers=headers, json=_save(A))
    b = client.post(BASE, headers=headers, json=_save(B, [A]))
    c = client.post(BASE, headers=headers, json=_save(C, [B]))
    d = client.post(BASE, headers=headers, json=_save(D))
    for response in (a, b, c, d):
        assert response.status_code == 200, response.text

    result = client.get(GRAPH, headers=headers)
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    graph = result.json()
    assert graph["available"] is True
    assert graph["case_count"] == 4
    assert graph["declared_dependency_count"] == 2
    assert [(node["work_item_ref"], node["dependency_depth"]) for node in graph["nodes"]] == [
        (A, 0), (D, 0), (B, 1), (C, 2),
    ]
    assert [(edge["prerequisite_ref"], edge["dependent_ref"]) for edge in graph["edges"]] == [
        (A, B), (B, C),
    ]
    assert all(edge["provenance"] == "explicit_owner_declaration_not_source_verified"
               and edge["source_verified"] is False for edge in graph["edges"])
    assert all("edit_token" not in node and "scope_ref" not in node
               and node["customer_outcome_verified"] is False
               and node["agent_execution_authorized"] is False
               for node in graph["nodes"])
    assert graph["source_read_performed"] is False
    assert graph["source_inventory_complete"] is False
    assert graph["business_obligation_verified"] is False

    # Editing prerequisite A invalidates B and C transactionally, not just
    # their displayed edges. Independent D survives unchanged.
    changed = client.post(BASE, headers=headers,
                          json=_save(A, revision=a.json()["revision"],
                                     token=a.json()["edit_token"]))
    assert changed.status_code == 200, changed.text
    reduced = client.get(GRAPH, headers=headers).json()
    assert [node["work_item_ref"] for node in reduced["nodes"]] == [A, D]
    assert reduced["edges"] == []
    assert reduced["declared_dependency_count"] == 0


def test_graph_auth_default_off_scope_change_and_review_revocation(local):
    client = TestClient(app)
    bearer = {"Authorization": "Bearer " + ensure_api_token()}
    for headers in ({}, bearer):
        assert client.get(GRAPH, headers=headers).status_code in (401, 403)
    owner = _owner()
    assert client.get(GRAPH, headers={**owner, "Origin": "https://evil.example"}).status_code == 403
    off = client.get(GRAPH, headers=owner)
    assert off.status_code == 200
    assert off.json()["available"] is False
    assert off.json()["nodes"] == []
    assert not declarations._path().exists()

    local["account"] = "acct_test_12345678"
    _review(A)
    assert client.post(BASE, headers=owner, json=_save(A)).status_code == 200
    assert client.get(GRAPH, headers=owner).json()["case_count"] == 1
    local["account"] = "acct_test_other"
    assert client.get(GRAPH, headers=owner).json()["nodes"] == []
    local["account"] = "acct_test_12345678"
    reviews.remove_review_intention(A)
    assert client.get(GRAPH, headers=owner).json()["nodes"] == []
    reviews.clear_review_intentions()
    assert not declarations._path().exists()
    local["account"] = None
    assert client.get(GRAPH, headers=owner).json()["available"] is False


@pytest.mark.parametrize("corruption", ["missing", "cycle", "duplicate", "claim"])
def test_projection_refuses_incomplete_or_overclaimed_snapshots(corruption):
    def item(ref, deps):
        return {
            "work_item_ref": ref, "depends_on_work_item_refs": deps,
            "goal_code": "review_source_state",
            "expected_outcome_code": "source_state_rechecked",
            "revision": 1, "updated_at": "2026-11-01T00:00:00+00:00",
            "expires_at": "2026-11-02T00:00:00+00:00",
            "provenance": "explicit_owner_declaration_not_source_verified",
            "source_state": "unknown_until_explicit_recheck",
            "expected_outcome_verified": False,
            "business_obligation_verified": False,
            "customer_outcome_verified": False,
            "agent_execution_authorized": False,
        }
    snapshot = {
        "format": "openworkgraph.local-case-declarations.v1",
        "items": [item(A, []), item(B, [A])],
    }
    if corruption == "missing":
        snapshot["items"] = [item(B, [A])]
    elif corruption == "cycle":
        snapshot["items"][0]["depends_on_work_item_refs"] = [B]
    elif corruption == "duplicate":
        snapshot["items"].append(deepcopy(snapshot["items"][0]))
    else:
        snapshot["items"][1]["customer_outcome_verified"] = True
    with pytest.raises(declarations.CaseDeclarationError):
        project_declared_case_graph(snapshot)
