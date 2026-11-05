"""R24 full-app user interface for imported OCEL relationship timings."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_standalone_page_has_no_inlined_script_or_untrusted_html_interpolation():
    html = (ROOT / "dashboard" / "object_relationship_lead_times.html").read_text(encoding="utf-8")
    javascript = (ROOT / "dashboard" / "object_relationship_lead_times.js").read_text(encoding="utf-8")
    mining = (ROOT / "dashboard" / "process_mining.js").read_text(encoding="utf-8")
    assert '<script src="/object-relationship-lead-times.js" defer></script>' in html
    assert html.count("<script") == 1
    assert "innerHTML" not in javascript and "insertAdjacentHTML" not in javascript
    assert "textContent" in javascript
    assert "location.href = 'http" not in javascript
    assert "fetch(path," in javascript and "'OWG-Session '" in javascript
    assert "object_relationship_lead_times" in html
    assert "href = '/object-relationship-lead-times'" in mining
    for control in ("logSelect", "relationshipSelect", "sourceType", "targetType",
                    "sourceActivity", "targetActivity", "qualifierMode", "sampleLimit",
                    "queryForm", "sampleRows", "resultSection"):
        assert f'id="{control}"' in html


def test_http_ui_same_origin_csp_and_session_only_analytics(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    page = client.get("/object-relationship-lead-times")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    policy = page.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "script-src 'self'" in policy
    assert "connect-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
    assert "src=\"/object-relationship-lead-times.js\"" in page.text
    script = client.get("/object-relationship-lead-times.js")
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    assert script.headers["cache-control"] == "no-store"
    assert "document.addEventListener('DOMContentLoaded', init)" in script.text

    # The public page and script contain no user data. All API data remains
    # inaccessible with a generic AI bearer or without a dashboard session.
    assert client.get("/v1/event-logs").status_code in (401, 403)
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get("/v1/event-logs", headers=bearer).status_code in (401,403)

    doc = {"objectTypes": [], "eventTypes": [],
      "objects": [
        {"id":"order1","type":"order","attributes":[],
         "relationships":[{"objectId":"delivery1","qualifier":"ships"}]},
        {"id":"delivery1","type":"delivery","attributes":[],"relationships":[]}],
      "events":[
        {"id":"e1","type":"Approve","time":"2026-01-01T08:00:00Z",
         "attributes":[],"relationships":[{"objectId":"order1","qualifier":""}]},
        {"id":"e2","type":"Ship","time":"2026-01-01T10:00:00Z",
         "attributes":[],"relationships":[{"objectId":"delivery1","qualifier":""}]}]}
    header = {"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    import_header = {**header, "X-OWG-Local-Storage-Confirmed":"true"}
    made = client.post("/v1/event-logs/import?format=ocel&name=UI", json=doc,
                       headers=import_header)
    assert made.status_code == 200, made.text
    log_id = made.json()["log_id"]
    listing = client.get("/v1/event-logs", headers=header)
    assert listing.status_code == 200
    assert any(row["log_id"] == log_id for row in listing.json()["logs"])
    relationships = client.get("/v1/event-logs/"+log_id+"/object-relationships",headers=header)
    assert relationships.status_code == 200
    assert relationships.json()["groups"][0]["qualifier"] == "ships"
    endpoint = "/v1/event-logs/"+log_id+"/object-relationships/lead-times"
    req = {"source_type":"order","target_type":"delivery",
           "source_activity":"Approve","target_activity":"Ship",
           "qualifier":"ships","sample_limit":20}
    assert client.post(endpoint, json=req).status_code in (401,403)
    assert client.post(endpoint, json=req, headers=bearer).status_code in (401,403)
    output = client.post(endpoint, json=req, headers=header)
    assert output.status_code == 200 and output.headers["cache-control"] == "no-store", output.text
    assert output.json()["forward_pairs"] == 1
    assert output.json()["forward_lead_time_seconds"]["p50"] == 7200.0
    assert output.json()["execution_authorized"] is False

    removed = client.post("/v1/event-logs/"+log_id+"/delete",headers=header)
    assert removed.status_code == 200
    assert client.post(endpoint,json=req,headers=header).status_code == 422
'''
    env = {
        **os.environ,
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
    }
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr + result.stdout
