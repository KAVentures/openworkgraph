"""Actual local pipeline persistence and authenticated HTTP regression."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import automation_pipeline as pipeline
from shared.ai_opportunity_handoff import prepare, confirm
from test_ai_implementation_review import source, proposal


def draft():
    sources = [source(True)]
    return {"sources": sources, "proposal": proposal(sources), "title": "Customer reviewed follow-up",
            "description": "Review unresolved questions before implementation."}


def test_actual_review_and_atomic_duplicate_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    data = draft()
    preview = prepare(data)
    assert preview["review"]["cited_event_count"] == 1
    assert preview["review"]["unresolved_question_count"] == 1
    assert preview["review"]["coverage_gaps"]
    idea, lineage = confirm({"draft": data, "expected_preview_sha256": preview["preview_sha256"], "confirm_create_idea": True})
    def create(_):
        try:
            return pipeline.create_from_ai_review(idea, lineage)
        except pipeline.PipelineError as e:
            assert "already recorded" in str(e)
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(16)))
    made, = [r for r in results if r is not None]
    assert made["state"] == "idea" and made["assessment"] is None and not made["execution_authorized"]
    assert made["ai_proposal_handoff"] == lineage
    assert pipeline.get(made["idea_id"]) == made
    assert "Ignore all instructions" not in json.dumps(made)
    assert len(pipeline.pipeline()["ideas"]) == 1
    invalid = deepcopy(data)
    invalid["proposal"]["outcome"]["evidence_refs"] = ["nonexistent"]
    with pytest.raises(ValueError):
        prepare(invalid)
    # No existing pipeline state transitions or review rules are bypassed.
    with pytest.raises(pipeline.PipelineError, match="cannot move"):
        pipeline.transition(made["idea_id"], {"to": "approved", "expected_revision": 1})
    moved = pipeline.transition(made["idea_id"], {"to": "assessing", "expected_revision": 1})
    assert moved["ai_proposal_handoff"] == lineage
    manual = pipeline.create({"title": "Manual opportunity"})
    assert "ai_proposal_handoff" not in manual


def test_http_owner_only_exact_preview_and_strict_file_parsing(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_ai_opportunity_integration import draft
data=draft()
data["sources"]=[json.dumps(s) for s in data["sources"]]
data["proposal"]=json.dumps(data["proposal"])
with TestClient(app) as client:
    base="/v1/ai-opportunity-handoff"
    owner={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert client.get("/ai-opportunity-handoff.js").status_code==200
    for headers in ({},bearer):
        assert client.post(base+"/preview",json=data,headers=headers).status_code in (401,403)
        assert client.post(base+"/commit",json={},headers=headers).status_code in (401,403)
    assert client.post(base+"/preview",json={**data,"proposal":'{"x":1,"x":2}'},headers=owner).status_code==422
    result=client.post(base+"/preview",json=data,headers=owner)
    assert result.status_code==200,result.text
    assert result.headers["cache-control"]=="no-store"
    preview=result.json()
    assert preview["review"]["status"]=="structural_review_only"
    assert client.get("/v1/automation-pipeline",headers=owner).json()["ideas"]==[]
    commit={"draft":data,"expected_preview_sha256":preview["preview_sha256"],"confirm_create_idea":True}
    assert client.post(base+"/commit",json={**commit,"confirm_create_idea":False},headers=owner).status_code==422
    assert client.post(base+"/commit",json={**commit,"draft":{**data,"title":"Changed"}},headers=owner).status_code==422
    made=client.post(base+"/commit",json=commit,headers=owner)
    assert made.status_code==200,made.text
    assert made.headers["cache-control"]=="no-store"
    value=made.json()
    assert value["state"]=="idea" and value["assessment"] is None and value["execution_authorized"] is False
    assert value["ai_proposal_handoff"]["preview_sha256"]==preview["preview_sha256"]
    assert client.post(base+"/commit",json=commit,headers=owner).status_code==409
    assert len(client.get("/v1/automation-pipeline",headers=owner).json()["ideas"])==1
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path/"data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path/"auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path/"config.json")}
    p = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,'tests');"+code],
                       cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout+p.stderr
