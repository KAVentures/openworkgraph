"""Retained OCEL and authenticated API acceptance for directed topology views."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_topology_review as topology
from server import event_log_workspace as workspace
from test_event_log_relationship_comparison import document, import_doc, pins


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_pinned_directed_components_and_members():
    log = import_doc(document("left"))
    spec = {"snapshot": pins(log), "confirm": True, "view": "directed_components"}
    report = topology.review(log, spec)
    assert report["undirected_projection"] is False
    assert report["counts"]["objects"] == 5
    assert report["counts"]["literal_assertions"] == 7
    assert report["counts"]["strong_components"] == 4
    assert report["counts"]["cyclic_components"] == 1
    assert report["counts"]["source_components"] == 1
    assert report["counts"]["sink_components"] == 3
    assert report["counts"]["condensation_edges"] == 3
    assert not report["execution_authorized"]
    assert report["rows"][0]["component_id"] == "i"
    assert report["rows"][0]["objects"] == 2
    assert report["rows"][0]["internal_literal_assertions"] == 4

    member_spec = {**spec, "view": "directed_members", "limit": 2}
    page = topology.review(log, member_spec)
    all_rows = list(page["rows"])
    while page["next_offset"] is not None:
        page = topology.review(log, {**member_spec, "offset": page["next_offset"],
                                     "expected_review_sha256": page["review_sha256"]})
        all_rows.extend(page["rows"])
    assert len(all_rows) == len({row["object_id"] for row in all_rows}) == 5
    assert {row["object_id"] for row in all_rows if row["cyclic_component"]} == {"o", "i"}
    empty = topology.review(log, {**spec, "qualifier": ""})
    assert empty["counts"]["literal_assertions"] == 1
    assert empty["counts"]["strong_components"] == 5
    assert empty["counts"]["cyclic_components"] == 0
    assert empty["counts"]["source_components"] == empty["counts"]["sink_components"] == 4
    weak = topology.review(log, {**spec, "view": "components"})
    assert weak["undirected_projection"] is True
    assert weak["counts"]["weak_components"] == 1


def test_pagination_requires_exact_scope_and_snapshot():
    log = import_doc(document("left"))
    spec = {"snapshot": pins(log), "confirm": True, "view": "directed_members", "limit": 1}
    first = topology.review(log, spec)
    with pytest.raises(workspace.EventLogWorkspaceError):
        topology.review(log, {**spec, "offset": 1})
    for change in ({"view": "directed_components"}, {"qualifier": ""}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            topology.review(log, {**spec, **change, "offset": 1,
                                  "expected_review_sha256": first["review_sha256"]})
    workspace.append_log(log, BytesIO(json.dumps(document("left")).encode()), source_format="ocel")
    with pytest.raises(workspace.EventLogWorkspaceError, match="snapshot changed"):
        topology.review(log, spec)


def test_dashboard_only_http_views_and_assets(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_relationship_comparison import document
with TestClient(app) as client:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    stored={**h,"X-OWG-Local-Storage-Confirmed":"true"}
    result=client.post("/v1/event-logs/import?name=Directed&format=ocel",json=document("left"),headers=stored)
    assert result.status_code==200,result.text
    base="/v1/event-logs/"+result.json()["log_id"]+"/object-relationships"
    p=client.get(base+"/snapshot",headers=h)
    assert p.status_code==200,p.text
    pin={k:p.json()[k] for k in ("revision","relationships_sha256","object_index_sha256")}
    for view in ("directed_components","directed_members"):
        body={"snapshot":pin,"confirm":True,"view":view}
        assert client.post(base+"/topology",json=body,headers=bearer).status_code in (401,403)
        response=client.post(base+"/topology",json=body,headers=h)
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="no-store"
        assert response.json()["undirected_projection"] is False
        assert response.json()["counts"]["strong_components"]==4
        assert client.post(base+"/topology",json={**body,"confirm":False},headers=h).status_code==422
    page=client.get("/object-topology-review")
    assert page.status_code==200
    assert 'value="directed_components"' in page.text and 'value="directed_members"' in page.text
    assert client.get("/object-topology-review.js").status_code==200
"""
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path/"data"),
           "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path/"auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path/"config.json")}
    proc = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,'tests');"+code],
                          cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout+proc.stderr
