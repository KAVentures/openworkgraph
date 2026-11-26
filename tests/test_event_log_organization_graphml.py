"""GraphML export of the resource network: same counts, pseudonymous by default, valid GraphML."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO
from xml.etree import ElementTree as ET

import pytest

from server import event_log_organization_graphml as graphml
from server import event_log_workspace as workspace

NS = {"g": "http://graphml.graphdrawing.org/xmlns"}
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "resource"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log() -> str:
    rows = ["case,activity,time,resource"]
    for i in range(4):   # ann -> <bob & co> -> ann in every case
        rows += [f"c{i},Receive,2024-03-01T08:00:00Z,ann", f'c{i},Check,2024-03-01T09:00:00Z,"bob & <co>"',
                 f"c{i},Close,2024-03-01T10:00:00Z,ann"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="claims", source_format="csv",
                                mapping=MAPPING)["log_id"]


def _graph(xml: str):
    root = ET.fromstring(xml)
    keys = {k.get("id"): k.get("for") for k in root.findall("g:key", NS)}
    graph = root.find("g:graph", NS)
    nodes = {n.get("id"): {d.get("key"): d.text for d in n.findall("g:data", NS)} for n in graph.findall("g:node", NS)}
    edges = [{**e.attrib, **{d.get("key"): d.text for d in e.findall("g:data", NS)}} for e in graph.findall("g:edge", NS)]
    return keys, graph, nodes, edges


def test_pseudonymous_network_with_counts():
    xml, meta = graphml.organization_graphml(_log())
    assert "ann" not in xml and "bob" not in xml
    keys, graph, nodes, edges = _graph(xml)
    assert keys["label"] == "node" and keys["relation"] == "edge" and graph.get("edgedefault") == "directed"
    assert "pseudonyms valid within this file only" in graph.find("g:desc", NS).text
    labels = {n["label"]: n for n in nodes.values()}
    assert set(labels) == {"Resource 01", "Resource 02"} and labels["Resource 01"]["events"] == "8"
    assert all(e["source"] in nodes and e["target"] in nodes for e in edges)
    assert not any("directed" in e for e in edges)   # readers such as networkx refuse mixed graphs
    relations = sorted((e["relation"], e["weight"], e["edge_cases"], e["symmetric"]) for e in edges)
    assert relations == [("handover", "4", "4", "false"), ("handover", "4", "4", "false"),
                         ("subcontracting", "4", "4", "false"), ("working_together", "4", "4", "true")]
    assert meta == {"resources": 2, "handovers": 2, "working_together": 1, "names": "pseudonymous"}


def test_names_are_escaped_when_shown():
    xml, meta = graphml.organization_graphml(_log(), names="shown")
    assert "bob &amp; &lt;co&gt;" in xml and meta["names"] == "shown"
    _, graph, nodes, _ = _graph(xml)
    assert {n["label"] for n in nodes.values()} == {"ann", "bob & <co>"}
    assert "names from the log" in graph.find("g:desc", NS).text


def test_grouped_resources_get_a_node():
    rows = ["case,activity,time,resource"] + [
        f"c{i},{a},2024-03-01T0{8 + k}:00:00Z,r{(i + k) % 5}" for i in range(10) for k, a in enumerate("ABC")]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="x", source_format="csv", mapping=MAPPING)["log_id"]
    from server import event_log_organization as organization
    original = organization.MAX_RESOURCES
    organization.MAX_RESOURCES = 2
    try:
        xml, _ = graphml.organization_graphml(log)
    finally:
        organization.MAX_RESOURCES = original
    _, _, nodes, edges = _graph(xml)
    assert "Other resources" in {n["label"] for n in nodes.values()}
    assert all(e["source"] in nodes and e["target"] in nodes for e in edges)


def test_route_is_dashboard_only(tmp_path):
    from pathlib import Path
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time,resource"] + [f"c{i},A,2024-03-0{1+i}T08:00:00Z,ann" for i in range(3)] + [f"c{i},B,2024-03-0{1+i}T09:00:00Z,bob" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time&resource_column=resource",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/organization.graphml"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path, headers=owner)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/graphml+xml"), r.text
    assert "ann" not in r.text and r.headers["x-owg-network"] == "resources=2; handovers=1; working_together=1; names=pseudonymous"
    assert "ann" in client.get(path + "?names=shown", headers=owner).text
    assert client.get(path + "?names=everyone", headers=owner).status_code == 422
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
