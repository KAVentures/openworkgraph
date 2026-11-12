"""Operator-issued external agent grants: opt-in, case-bound, revocable."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import open_work_agent_grants as grants, open_work_dispatch as dispatch, open_work_reviews as reviews
from server.local_auth import create_dashboard_session
from server.open_work_handoff_routes import router

REF = "owg:w:" + "a" * 32
OTHER = "owg:w:" + "b" * 32

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(grants, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(dispatch, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(reviews, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(reviews, "_allowed_days", lambda: 7)
    reviews.save_review_intention(REF, "assess_follow_up", provider_candidate_currently_open=True)
    dispatch.set_enabled(True)
    dispatch.offer(REF)
    return tmp_path

def test_grants_are_opt_in_case_scoped_and_revocable_across_processes(setup):
    with pytest.raises(grants.GrantError, match="confirmation"):
        grants.issue(REF, "agent-a", 1, confirmed=False)
    with pytest.raises(grants.GrantError, match="offered"):
        grants.issue(OTHER, "agent-a", 1, confirmed=True)
    issued = grants.issue(REF, "agent-a", 1, confirmed=True)
    header = "Bearer " + issued["token"]
    assert grants.authenticate(header, REF)["agent"] == "agent-a"
    assert grants.authenticate(header, OTHER) is None
    assert grants.authenticate("Bearer wrong") is None
    assert "token" not in str(grants.list_grants())
    assert grants.revoke(issued["grant_id"])
    assert grants.authenticate(header) is None
    assert not grants.revoke(issued["grant_id"])

def test_granted_rest_lifecycle_and_owner_revoke(setup):
    app = FastAPI()
    app.include_router(router)
    owner = {"Authorization": "OWG-Session " + create_dashboard_session()}
    with TestClient(app) as client:
        assert client.post("/v1/open-work/dispatch/grants", json={
            "work_item_ref": REF, "agent": "agent-a", "hours": 1, "confirm_grant": True
        }).status_code == 403
        assert client.post("/v1/open-work/dispatch/grants", headers=owner, json={
            "work_item_ref": REF, "agent": "agent-a", "hours": 1
        }).status_code == 409
        created = client.post("/v1/open-work/dispatch/grants", headers=owner, json={
            "work_item_ref": REF, "agent": "agent-a", "hours": 1, "confirm_grant": True
        })
        assert created.status_code == 200, created.text
        grant = created.json()
        token = {"Authorization": "Bearer " + grant["token"]}
        assert client.get("/v1/open-work/dispatch/granted", headers=token).json()["offers"][0]["work_item_ref"] == REF
        assert client.post("/v1/open-work/dispatch/granted/claim", headers=token,
                           json={"work_item_ref": OTHER}).status_code == 401
        claim = client.post("/v1/open-work/dispatch/granted/claim", headers=token,
                            json={"work_item_ref": REF, "agent": "spoofed"})
        assert claim.status_code == 200, claim.text
        assert claim.json()["claimed_by"] == "agent-a"
        assert client.post("/v1/open-work/dispatch/grants/revoke", headers=owner,
                           json={"grant_id": grant["grant_id"]}).json()["revoked"]
        assert client.post("/v1/open-work/dispatch/granted/report", headers=token,
                           json={"work_item_ref": REF, "lease_token": claim.json()["lease_token"],
                                 "outcome": "attempted"}).status_code == 401
        assert client.get("/v1/open-work/dispatch/granted", headers=token).status_code == 401

def test_secure_app_rejects_grant_as_general_api_credential(tmp_path):
    code = """
from fastapi.testclient import TestClient
from server.secure_app import app
from server import open_work_reviews as reviews, open_work_dispatch as dispatch
from server.local_auth import create_dashboard_session
ref = 'owg:w:' + 'a' * 32
reviews._allowed_days = lambda: 7
reviews.save_review_intention(ref, 'assess_follow_up', provider_candidate_currently_open=True)
owner = {'Authorization': 'OWG-Session ' + create_dashboard_session()}
with TestClient(app) as c:
    assert c.post('/v1/open-work/dispatch/settings', headers=owner, json={'enabled':True}).status_code == 200
    assert c.post('/v1/open-work/dispatch/offers', headers=owner, json={'work_item_ref':ref,'confirm_offer':True}).status_code == 200
    result = c.post('/v1/open-work/dispatch/grants', headers=owner, json={'work_item_ref':ref,'agent':'a','hours':1,'confirm_grant':True})
    assert result.status_code == 200, result.text
    token = {'Authorization': 'Bearer ' + result.json()['token']}
    assert c.get('/v1/open-work/dispatch/granted', headers=token).status_code == 200
    assert c.get('/v1/open-work/dispatch', headers=token).status_code in (401,403)
    assert c.get('/v1/workflow-trace', headers=token).status_code == 401
"""
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path / "data"),
               WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path / "auth"),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path / "config.json"))
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_grant_loses_authority_when_offer_withdrawn_or_dispatch_disabled(setup):
    issued = grants.issue(REF, "agent-a", 1, confirmed=True)
    header = "Bearer " + issued["token"]
    assert grants.authenticate(header) is not None
    assert dispatch.withdraw(REF)
    assert grants.authenticate(header) is None
    dispatch.offer(REF)
    # A fresh offer must never resurrect an earlier approval.
    assert grants.authenticate(header) is None
    replacement = grants.issue(REF, "agent-a", 1, confirmed=True)
    assert grants.authenticate("Bearer " + replacement["token"]) is not None
    dispatch.set_enabled(False)
    assert grants.authenticate(header) is None


def test_grant_never_revives_after_dispatch_restart(setup):
    old = grants.issue(REF, "agent-a", 1, confirmed=True)
    dispatch.set_enabled(False)
    dispatch.set_enabled(True)
    dispatch.offer(REF)
    assert grants.authenticate("Bearer " + old["token"]) is None
    assert grants.authenticate("Bearer " + grants.issue(REF, "agent-b", 1, confirmed=True)["token"]) is not None


def test_owner_acknowledge_reoffer_requires_new_grant(setup):
    issued = grants.issue(REF, "agent-a", 1, confirmed=True)
    header = "Bearer " + issued["token"]
    claim = dispatch.claim(REF, "agent-a")
    dispatch.report(REF, claim["lease_token"], "attempted")
    assert grants.authenticate(header) is None
    dispatch.acknowledge(REF, reoffer=True)
    assert grants.authenticate(header) is None
    replacement = grants.issue(REF, "agent-a", 1, confirmed=True)
    assert grants.authenticate("Bearer " + replacement["token"]) is not None


def test_one_granted_agent_cannot_release_or_report_anothers_lease(setup):
    """Two valid case grants are not interchangeable lease principals."""
    app = FastAPI()
    app.include_router(router)
    first = grants.issue(REF, "agent-a", 1, confirmed=True)
    second = grants.issue(REF, "agent-b", 1, confirmed=True)
    a = {"Authorization": "Bearer " + first["token"]}
    b = {"Authorization": "Bearer " + second["token"]}
    with TestClient(app) as client:
        claim = client.post("/v1/open-work/dispatch/granted/claim", headers=a,
                            json={"work_item_ref": REF})
        assert claim.status_code == 200, claim.text
        lease = claim.json()["lease_token"]
        for route, payload in (
            ("release", {"work_item_ref": REF, "lease_token": lease}),
            ("report", {"work_item_ref": REF, "lease_token": lease, "outcome": "attempted"}),
        ):
            denied = client.post("/v1/open-work/dispatch/granted/" + route,
                                 headers=b, json=payload)
            assert denied.status_code == 409, denied.text
        state = dispatch.status(owner=True)["offers"][0]
        assert state["status"] == "claimed"
        assert state["claimed_by"] == "agent-a"
        assert state["last_report"] is None
        # The actual principal can still complete the original lease.
        accepted = client.post("/v1/open-work/dispatch/granted/report", headers=a,
                               json={"work_item_ref": REF, "lease_token": lease,
                                     "outcome": "attempted"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["last_report"]["verified"] is False


def test_legacy_local_dispatch_lease_contract_still_works(setup):
    """The new identity constraint must not silently break existing MCP callers."""
    lease = dispatch.claim(REF, "local-agent")["lease_token"]
    assert dispatch.release(REF, lease)["status"] == "offered"
    lease = dispatch.claim(REF, "local-agent")["lease_token"]
    assert dispatch.report(REF, lease, "declined")["last_report"]["verified"] is False


def test_granted_case_context_is_source_bound_reviewed_and_revocable(setup, monkeypatch):
    from server import open_work_declaration_routes as declarations
    from server import open_work_case_declarations as case_store
    scope = "owg:scope:" + "c" * 32
    monkeypatch.setattr(declarations, "_source_scope", lambda: scope)
    approved = grants.issue(REF, "agent-a", 1, confirmed=True, source_scope=scope, context_approved=True)
    legacy = grants.issue(REF, "agent-b", 1, confirmed=True)
    no_context = grants.issue(REF, "agent-c", 1, confirmed=True, source_scope=scope)
    monkeypatch.setattr(case_store, "list_case_declarations", lambda current_scope: {
        "items": [{
            "work_item_ref": REF, "scope_ref": current_scope,
            "edit_token": "secret-owner-only", "goal_code": "verify_operation",
            "depends_on_work_item_refs": [], "expected_outcome_verified": False,
        }]
    })
    app = FastAPI()
    app.include_router(router)
    path = "/v1/open-work/dispatch/granted/case-context"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + legacy["token"]}).status_code == 403
        assert client.get(path, headers={"Authorization": "Bearer " + no_context["token"]}).status_code == 403
        headers = {"Authorization": "Bearer " + approved["token"]}
        result = client.get(path, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["case"]["work_item_ref"] == REF
        assert "edit_token" not in result.text
        assert "scope_ref" not in result.text
        assert result.json()["customer_outcome_verified"] is False
        assert result.json()["source_read_performed"] is False
        monkeypatch.setattr(declarations, "_source_scope", lambda: "owg:scope:" + "d" * 32)
        assert client.get(path, headers=headers).status_code == 403
        monkeypatch.setattr(declarations, "_source_scope", lambda: scope)
        assert grants.revoke(approved["grant_id"])
        assert client.get(path, headers=headers).status_code == 401


def test_granted_context_denies_missing_review_and_cross_case(setup, monkeypatch):
    from server import open_work_declaration_routes as declarations
    from server import open_work_case_declarations as case_store
    scope = "owg:scope:" + "c" * 32
    monkeypatch.setattr(declarations, "_source_scope", lambda: scope)
    issued = grants.issue(REF, "agent-a", 1, confirmed=True, source_scope=scope, context_approved=True)
    monkeypatch.setattr(case_store, "list_case_declarations", lambda current_scope: {
        "items": [{"work_item_ref": OTHER, "edit_token": "private"}]
    })
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        result = client.get("/v1/open-work/dispatch/granted/case-context",
                            headers={"Authorization": "Bearer " + issued["token"]})
        assert result.status_code == 404
        assert OTHER not in result.text


def test_operator_inventory_tracks_effective_authority_not_only_expiry(setup):
    """The owner must not mistake an invalidated grant for live access."""
    issued = grants.issue(REF, "agent-a", 1, confirmed=True, context_approved=False)
    def listed():
        return next(item for item in grants.list_grants() if item["grant_id"] == issued["grant_id"])
    assert listed()["active"] is True
    assert listed()["case_context_approved"] is False
    assert "token" not in str(listed())
    dispatch.withdraw(REF)
    assert listed()["active"] is False
    dispatch.offer(REF)
    assert listed()["active"] is False  # no resurrection after re-offer
    replacement = grants.issue(REF, "agent-b", 1, confirmed=True)
    assert next(g for g in grants.list_grants() if g["grant_id"] == replacement["grant_id"])["active"]
    lease = dispatch.claim(REF, "agent-b")
    dispatch.report(REF, lease["lease_token"], "attempted")
    assert next(g for g in grants.list_grants() if g["grant_id"] == replacement["grant_id"])["active"] is False
    dispatch.acknowledge(REF, reoffer=True)
    assert next(g for g in grants.list_grants() if g["grant_id"] == replacement["grant_id"])["active"] is False
    assert grants.revoke(replacement["grant_id"])
    assert next(g for g in grants.list_grants() if g["grant_id"] == replacement["grant_id"])["active"] is False


def test_operator_grant_inventory_rest_requires_dashboard_session(setup):
    app = FastAPI()
    app.include_router(router)
    grant = grants.issue(REF, "agent-a", 1, confirmed=True)
    with TestClient(app) as client:
        path = "/v1/open-work/dispatch/grants"
        assert client.get(path).status_code == 403
        assert client.get(path, headers={"Authorization": "Bearer " + grant["token"]}).status_code == 403
        owner = {"Authorization": "OWG-Session " + create_dashboard_session()}
        result = client.get(path, headers=owner)
        assert result.status_code == 200
        entry = next(g for g in result.json()["grants"] if g["grant_id"] == grant["grant_id"])
        assert entry["active"] is True
        assert entry["case_context_approved"] is False
        assert "token" not in result.text
