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
    assert grants.authenticate(header) is not None
    dispatch.set_enabled(False)
    assert grants.authenticate(header) is None
