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
        assert client.post(base+"/prepare-packet",json={"sources":data["sources"],"max_bytes":131072},headers=headers).status_code in (401,403)
        assert client.post(base+"/generate-local",json={},headers=headers).status_code in (401,403)
        assert client.post(base+"/preview",json=data,headers=headers).status_code in (401,403)
        assert client.post(base+"/commit",json={},headers=headers).status_code in (401,403)
    assert client.post(base+"/preview",json={**data,"proposal":'{"x":1,"x":2}'},headers=owner).status_code==422
    packet_request={"sources":data["sources"],"max_bytes":131072}
    packet_result=client.post(base+"/prepare-packet",json=packet_request,headers=owner)
    assert packet_result.status_code==200,packet_result.text
    assert packet_result.headers["cache-control"]=="no-store"
    prepared=packet_result.json()
    assert prepared["external_model_invoked"] is False
    assert prepared["external_sharing_authorized"] is False
    assert json.loads(prepared["packet_json"])["packet_sha256"]==json.loads(data["proposal"])["packet_sha256"]
    assert client.post(base+"/prepare-packet",json={**packet_request,"max_bytes":True},headers=owner).status_code==422
    assert client.post(base+"/prepare-packet",json={**packet_request,"sources":['{"x":1,"x":2}']},headers=owner).status_code==422
    # Actual loopback proposal adapter through the owner-only dashboard route.
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    calls=[]
    answer=json.loads(data["proposal"])
    answer["author_model"]="local-test:1"
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body=json.dumps({"model":"local-test:1","choices":[{"finish_reason":"stop","message":{"role":"assistant","content":json.dumps(answer)}}],"usage":{"prompt_tokens":12,"completion_tokens":8,"total_tokens":20}}).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*_): pass
    listener=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=Thread(target=listener.serve_forever,daemon=True)
    thread.start()
    try:
        req={"sources":data["sources"],"expected_packet_sha256":prepared["packet_sha256"],"max_bytes":131072,
             "model":"local-test:1","port":listener.server_port,"max_output_tokens":4096,"json_mode":False,"confirm_local_inference":True}
        for change in ({"confirm_local_inference":False},{"expected_packet_sha256":"0"*64},{"port":0}):
            assert client.post(base+"/generate-local",json={**req,**change},headers=owner).status_code==422
        assert not calls
        generated=client.post(base+"/generate-local",json=req,headers=owner)
        assert generated.status_code==200,generated.text
        assert generated.headers["cache-control"]=="no-store"
        value=generated.json()
        assert len(calls)==1 and value["receipt"]["usage"]["total_tokens"]==20
        assert value["opportunity_created"] is False
        assert client.get("/v1/automation-pipeline",headers=owner).json()["ideas"]==[]
        returned=client.post(base+"/preview",json={**data,"proposal":value["proposal_json"]},headers=owner)
        assert returned.status_code==200,returned.text
        assert returned.json()["lineage"]["proposal_sha256"]==value["receipt"]["proposal_sha256"]
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)
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
