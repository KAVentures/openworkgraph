"""PDD package with reviewed, redacted step screenshots (roadmap #326 D)."""
from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from PIL import Image
import pytest

from server import process_pdd_package as pdd

ROOT = Path(__file__).resolve().parents[1]


def _design(**overrides):
    design = {
        "format": "openworkgraph.proposed-process-design.v1",
        "status": "human_reviewed_draft_not_approved_policy",
        "business_goal_declared_by_user": "Resolve the reported business case",
        "user_reviewed_export": True, "approved_business_policy": False, "authorized_agent_actions": False,
        "agent_execution_triggered": False, "source_case_ids_included": False, "uploaded_data_retained_by_owg": False,
        "as_is": {"path": ["Receive", "Review", "Close"], "source_observation_not_policy": True},
        "to_be": {"proposed_path": ["Receive", "Validate", "Close"], "proposed_not_deployed": True},
    }
    design.update(overrides)
    return design


def _png(color=(200, 30, 30), size=(64, 48)) -> str:
    out = BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


def _jpeg_with_exif() -> str:
    out = BytesIO()
    image = Image.new("RGB", (40, 30), (10, 120, 10))
    exif = Image.Exif()
    exif[0x013B] = "Secret Photographer"  # Artist
    exif[0x8825] = {2: (59, 20, 0)}  # GPS latitude
    image.save(out, format="JPEG", exif=exif)
    return base64.b64encode(out.getvalue()).decode()


def _request(**overrides):
    request = {"format": pdd.FORMAT, "design": _design(), "screenshots": [], "narrative": {"text": "", "ai_drafted": False},
               "screenshots_reviewed_and_redacted_by_user": False}
    request.update(overrides)
    return request


def _files(archive: bytes) -> dict[str, bytes]:
    with ZipFile(BytesIO(archive)) as z:
        return {name: z.read(name) for name in z.namelist()}


def test_package_without_screenshots_is_the_reviewed_pdd():
    files = _files(pdd.build_pdd_package(_request()))
    assert sorted(files) == ["PDD.html", "PDD.md", "manifest.json"]
    md = files["PDD.md"].decode()
    assert "| step_002 | Validate | New | Unmapped |" in md and "## 10. Sign-off" in md
    assert "## 8. Narrative" not in md and "## 9. Step screenshots" not in md
    manifest = json.loads(files["manifest.json"])
    assert manifest["screenshots"] == [] and manifest["stored_by_owg"] is False


def test_screenshots_are_reencoded_without_metadata_and_placed_per_step():
    shots = [{"step_id": "step_002", "image_base64": _jpeg_with_exif(), "caption": "Validation form", "masked_regions": 2},
             {"step_id": "step_002", "image_base64": _png(), "caption": "", "masked_regions": 0}]
    archive = pdd.build_pdd_package(_request(screenshots=shots, screenshots_reviewed_and_redacted_by_user=True))
    files = _files(archive)
    assert {"images/step_002_1.png", "images/step_002_2.png"} <= set(files)
    for name in ("images/step_002_1.png", "images/step_002_2.png"):
        data = files[name]
        assert data.startswith(b"\x89PNG") and b"Secret Photographer" not in data and b"Exif" not in data
        with Image.open(BytesIO(data)) as image:
            assert not image.info.get("exif") and not image.getexif()
    md = files["PDD.md"].decode()
    assert "### step_002: Validate" in md and "![Validation form](images/step_002_1.png)" in md
    assert "(masked regions: 2)" in md and "masking is manual and may miss information" in md
    manifest = json.loads(files["manifest.json"])
    assert [s["masked_regions_declared"] for s in manifest["screenshots"]] == [2, 0]
    assert manifest["image_metadata_removed"] is True
    html = files["PDD.html"].decode()
    assert html.count("data:image/png;base64,") == 2 and "<script" not in html
    assert archive == pdd.build_pdd_package(_request(screenshots=shots, screenshots_reviewed_and_redacted_by_user=True))


def test_large_screenshots_are_scaled_down():
    shot = {"step_id": "step_001", "image_base64": _png(size=(3000, 1000)), "caption": "", "masked_regions": 0}
    files = _files(pdd.build_pdd_package(_request(screenshots=[shot], screenshots_reviewed_and_redacted_by_user=True)))
    with Image.open(BytesIO(files["images/step_001_1.png"])) as image:
        assert max(image.size) == pdd.MAX_EDGE


def test_ai_drafted_narrative_is_marked_and_user_text_is_inert():
    hostile = "<script>alert(1)</script> ``` # Injected | heading"
    files = _files(pdd.build_pdd_package(_request(
        narrative={"text": hostile, "ai_drafted": True},
        screenshots=[{"step_id": "step_001", "image_base64": _png(), "caption": "<img src=x onerror=alert(1)> | x", "masked_regions": 1}],
        screenshots_reviewed_and_redacted_by_user=True)))
    md, html = files["PDD.md"].decode(), files["PDD.html"].decode()
    assert "**AI-drafted text, not reviewed or verified.**" in md
    assert "```text\n<script>alert(1)</script> ''' # Injected | heading\n```" in md
    assert "<script" not in html and "onerror=alert" not in html.replace("onerror=alert(1)&gt;", "")
    assert "&lt;script&gt;" in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "Content-Security-Policy" in html and "default-src 'none'" in html
    reviewer = _files(pdd.build_pdd_package(_request(narrative={"text": "Plain words", "ai_drafted": False})))["PDD.md"].decode()
    assert "Reviewer-written description" in reviewer and "AI-drafted" not in reviewer


@pytest.mark.parametrize("change,message", [
    ({"format": "x"}, "format"),
    ({"extra": 1}, "unknown"),
    ({"design": _design(user_reviewed_export=False)}, "human-reviewed"),
    ({"design": _design(authorized_agent_actions=True)}, "authorize"),
    ({"screenshots": [{"step_id": "step_001", "image_base64": _png(), "caption": "", "masked_regions": 0}]}, "confirm"),
    ({"screenshots": [{"step_id": "step_009", "image_base64": _png()}], "screenshots_reviewed_and_redacted_by_user": True}, "unknown step"),
    ({"screenshots": [{"step_id": "step_001", "image_base64": "%%%"}], "screenshots_reviewed_and_redacted_by_user": True}, "base64"),
    ({"screenshots": [{"step_id": "step_001", "image_base64": base64.b64encode(b"GIF89a....").decode()}],
      "screenshots_reviewed_and_redacted_by_user": True}, "PNG or JPEG"),
    ({"screenshots": [{"step_id": "step_001", "image_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nbroken").decode()}],
      "screenshots_reviewed_and_redacted_by_user": True}, "could not be read"),
    ({"screenshots": [{"step_id": "step_001", "image_base64": _png(), "masked_regions": True}],
      "screenshots_reviewed_and_redacted_by_user": True}, "masked_regions"),
    ({"screenshots": [{"step_id": "step_001", "image_base64": _png()}] * 4, "screenshots_reviewed_and_redacted_by_user": True}, "per step"),
    ({"screenshots": [{"step_id": "step_001", "image_base64": _png(), "caption": "x" * 301}],
      "screenshots_reviewed_and_redacted_by_user": True}, "caption"),
    ({"narrative": {"text": "x", "tone": "fun"}}, "narrative"),
])
def test_invalid_requests_are_refused(change, message):
    with pytest.raises(pdd.PddPackageError, match=message):
        pdd.build_pdd_package(_request(**change))


def test_decompression_bombs_are_refused():
    out = BytesIO()
    Image.new("1", (20000, 20000)).save(out, format="PNG")  # small file, 400 megapixels
    shot = {"step_id": "step_001", "image_base64": base64.b64encode(out.getvalue()).decode()}
    with pytest.raises(pdd.PddPackageError):
        pdd.build_pdd_package(_request(screenshots=[shot], screenshots_reviewed_and_redacted_by_user=True))


def test_real_http_route(tmp_path):
    code = r'''
import json
from zipfile import ZipFile
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from tests.test_process_pdd_package import _request, _png

with TestClient(app) as client:
    body = _request(screenshots=[{"step_id": "step_003", "image_base64": _png(), "caption": "Done", "masked_regions": 1}],
                    screenshots_reviewed_and_redacted_by_user=True)
    assert client.post("/v1/process-design/pdd-package", json=body).status_code == 401
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    ok = client.post("/v1/process-design/pdd-package", json=body, headers=headers)
    assert ok.status_code == 200 and ok.headers["content-type"] == "application/zip", ok.text
    assert ok.headers["cache-control"] == "no-store" and "openworkgraph-pdd.zip" in ok.headers["content-disposition"]
    assert "images/step_003_1.png" in ZipFile(BytesIO(ok.content)).namelist()
    assert client.post("/v1/process-design/pdd-package", content=b"x", headers={**headers, "Content-Type": "text/plain"}).status_code == 415
    assert client.post("/v1/process-design/pdd-package", content=b"{", headers={**headers, "Content-Type": "application/json"}).status_code == 422
    assert client.post("/v1/process-design/pdd-package", json={"format": "x"}, headers=headers).status_code == 422
    assert client.get("/process-pdd.js").status_code == 200
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "PYTHONPATH": str(ROOT),
    })
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_dashboard_client_helpers():
    html = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
    script = (ROOT / "dashboard/process_pdd.js").read_text(encoding="utf-8")
    assert '<script src="/process-pdd.js"></script>' in html
    assert "innerHTML" not in script and "localStorage" not in script
    import shutil
    if not shutil.which("node"):
        return
    check = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/process_pdd.js','utf8'),scope);
const m=scope.window.__owgPddModel;const plain=v=>JSON.parse(JSON.stringify(v));
const design={format:'openworkgraph.proposed-process-design.v1',business_goal_declared_by_user:'Close cases',to_be:{proposed_path:['Receive','Close']}};
assert.deepEqual(plain(m.stepsFromDesign(design)),[{id:'step_001',activity:'Receive'},{id:'step_002',activity:'Close'}]);
assert.throws(()=>m.stepsFromDesign({format:'x'}));assert.throws(()=>m.stepsFromDesign({...design,to_be:{proposed_path:[]}}));
const prompt=m.aiPrompt(design);assert.ok(prompt.includes('step_002: Close'));assert.ok(prompt.includes('Do not invent'));
const req=plain(m.buildRequest(design,[{step_id:'step_001',image_base64:'AA',caption:'c',masked_regions:2,preview:'data:'}],{text:'n',ai_drafted:true},true));
assert.equal(req.format,'openworkgraph.pdd-package-request.v1');assert.equal(req.screenshots[0].preview,undefined);
assert.equal(req.narrative.ai_drafted,true);assert.equal(req.screenshots_reviewed_and_redacted_by_user,true);
const saved={format:'openworkgraph.versioned-process-export.v1',workflow_id:'saved_graph',revision:3,content_sha256:'a'.repeat(64),
process_model:{goal:'Review both branches',activities:[{id:'fork',label:'Both',kind:'parallel_split'},{id:'A',label:'Review',kind:'activity'}],transitions:[{source:'fork',target:'A'}],business_rules:[]}};
assert.deepEqual(plain(m.stepsFromDesign(saved)),[{id:'fork',activity:'Both'},{id:'A',activity:'Review'}]);
const source=plain(m.buildRequest(saved,[],{text:'',ai_drafted:false},false));
assert.deepEqual(source.process_reference,{workflow_id:'saved_graph',revision:3,content_sha256:'a'.repeat(64)});
assert.equal(source.design,undefined);assert.equal(source.process_model,undefined);
assert.match(m.aiPrompt(saved),/parallel_split/);assert.match(m.aiPrompt(saved),/fork → A/);
assert.match(m.aiPrompt(saved),/not an execution sequence/);
"""
    result = subprocess.run(["node", "-e", check], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
