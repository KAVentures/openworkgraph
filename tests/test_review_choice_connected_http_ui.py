"""Connected review workbench acceptance: real HTTP -> FastAPI -> SQLite -> UI.

Unlike the isolated VM regressions, fetches here reach mounted, authenticated
routes. The DOM is deliberately minimal; this does not replace Chromium E2E.
"""
import json
import shutil
import socket
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_review_workbench_connected_http_journey(monkeypatch, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable; browser CI runs separate JavaScript checks")
    from server import review_choice_routes as routes

    db = tmp_path / "review-connected.sqlite3"
    monkeypatch.setattr(routes.pipeline, "_connect", lambda: sqlite3.connect(db))
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "owner")
    evidence = {"digest": "b" * 64}

    def journey(idea_id):
        return {
            "idea": {"idea_id": idea_id, "revision": 3, "state": "approved"},
            "process_status": {"status": "current", "process_reference": {}},
            "projects": [{
                "project_id": "p1", "idea_revision": 3,
                "idea_revision_is_current": True,
                "spec_sha256": evidence["digest"], "archive_sha256": "c" * 64,
                "reported_specifications": [{"status": "matching_reported_specification"}],
            }],
            "projects_truncated": False,
            "dataset_origins": [{"status": "current",
                "source_content_sha256": "e" * 64, "selection_sha256": "f" * 64}],
            "dataset_origin_check_available": True,
        }

    monkeypatch.setattr(routes.opportunity_journey, "journey", journey)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    class Handler(BaseHTTPRequestHandler):
        def dispatch(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            headers = {"Authorization": self.headers.get("Authorization", "")}
            if self.command == "POST":
                response = client.post(self.path, content=body, headers={
                    **headers, "Content-Type": self.headers.get("Content-Type", "application/json")})
            else:
                response = client.get(self.path, headers=headers)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        do_GET = dispatch
        do_POST = dispatch

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node {
 constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.value='';this.disabled=false;this.textContent='';}
 append(...xs){this.children.push(...xs);}
 replaceChildren(...xs){this.children=[...xs];}
 setAttribute(){}
 addEventListener(k,fn){this.listeners[k]=fn;}
 scrollIntoView(){}
}
(async()=>{
 const panel=new Node('section'),pipeline=new Node('section');
 const doc={createElement:t=>new Node(t),getElementById:id=>id==='panel-evidence'?panel:id==='owgPipelineCard'?pipeline:null,addEventListener(k,fn){this[k]=fn;}};
 let handoffs=0,receipt=null;
 const context={document:doc,CustomEvent:class {constructor(type,init){this.type=type;this.detail=init.detail;}},
 window:{__owgAuthReady:Promise.resolve(),dispatchEvent:e=>{handoffs++;receipt=e.detail;}},
 fetch:(path,opts={})=>fetch(process.env.OWG_TEST_URL+path,{...opts,headers:{...opts.headers,Authorization:'OWG-Session owner'}})};
 vm.runInNewContext(fs.readFileSync(process.env.OWG_TEST_SCRIPT,'utf8'),context);
 doc.DOMContentLoaded();
 const [input,load,status,results,details]=panel.children[0].children.slice(3);
 input.value='idea-1';await load.onclick();
 assert.equal(results.children.some(x=>x.tagName==='select'),true,status.textContent);
 const choice=results.children.find(x=>x.tagName==='select');
 const review=results.children.find(x=>x.tagName==='button');
 choice.value='p1';await review.onclick();
 assert.match(status.textContent,/Read-only choice inspected/);
 const preflight=details.children.find(x=>x.textContent==='Revalidate review basis on server');
 await preflight.onclick();
 for(let i=0;i<12;i++)await new Promise(r=>setTimeout(r,5));
 const state=details.children.find(x=>x.tagName==='p'&&/Saved review:/.test(x.textContent));
 assert.match(state.textContent,/unreviewed \(revision 0\)/);
 const approve=details.children.find(x=>x.textContent==='Approve reviewed choice');
 const consent=details.children.find(x=>x.tagName==='label'&&x.children.some(y=>y.tagName==='input')).children[0];
 const proceed=details.children.find(x=>x.textContent==='Open opportunity decision and handoff');
 assert.equal(proceed.disabled,true);
 await approve.onclick();
 assert.match(state.textContent,/Saved approved at revision 1/);
 assert.equal(proceed.disabled,true,'approval alone is not consent');
 consent.checked=true;consent.listeners.change();
 assert.equal(proceed.disabled,false);
 await proceed.onclick();
 assert.equal(handoffs,1);
 assert.equal(receipt.review_revision,1);
 assert.match(status.textContent,/No agent execution access was granted/i);
 console.log('PASS real HTTP review/approval/consent/handoff with SQLite persistence');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    try:
        result = subprocess.run(
            [node, "-e", script], cwd=ROOT, capture_output=True, text=True,
            timeout=45, env={
                **__import__("os").environ,
                "OWG_TEST_URL": f"http://127.0.0.1:{server.server_port}",
                "OWG_TEST_SCRIPT": str(ROOT / "dashboard/review_choice_workbench.js"),
            },
        )
        assert result.returncode == 0, result.stdout + "\n" + result.stderr
        assert "PASS real HTTP" in result.stdout
        saved = client.get("/v1/review-choice-workbench/idea-1/review/p1",
                           headers={"Authorization": "OWG-Session owner"})
        assert saved.status_code == 200
        assert saved.json()["decision"] == "approved"
        assert saved.json()["execution_authorized"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
