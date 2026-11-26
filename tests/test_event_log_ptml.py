"""PTML export: the discovered process tree, operator for operator, in ProM/PM4Py's format."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from server import event_log_model_discovery as discovery
from server import event_log_ptml as ptml
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _parse(xml: str):
    tree = ET.fromstring(xml).find("processTree")
    nodes = {e.get("id"): e for e in tree if e.tag != "parentsNode"}
    children: dict[str, list[str]] = {}
    for edge in tree.findall("parentsNode"):
        children.setdefault(edge.get("sourceId"), []).append(edge.get("targetId"))

    def back(node_id):
        e = nodes[node_id]
        if e.tag == "manualTask":
            return {"op": "activity", "activity": e.get("name")}
        if e.tag == "automaticTask":
            return {"op": "tau"}
        op = {"sequence": "seq", "xor": "xor", "and": "and", "xorLoop": "loop"}[e.tag]
        kids = [back(c) for c in children.get(node_id, [])]
        if op == "loop":
            assert kids[-1] == {"op": "tau"}   # silent exit
            kids = kids[:-1]
        return {"op": op, "children": kids}
    return tree, nodes, back(tree.get("root"))


def _strip(node):
    if node["op"] == "activity":
        return {"op": "activity", "activity": node["activity"]}
    if node["op"] == "tau":
        return {"op": "tau"}
    return {"op": node["op"], "children": [_strip(c) for c in node["children"]]}


TREE = {"op": "seq", "children": [
    {"op": "activity", "activity": "Receive"},
    {"op": "xor", "children": [{"op": "tau"}, {"op": "activity", "activity": 'Check "A" & <B>'}]},
    {"op": "and", "children": [{"op": "activity", "activity": "Pay"}, {"op": "activity", "activity": "Ship"}]},
    {"op": "loop", "children": [{"op": "activity", "activity": "Fix"}, {"op": "tau"}]}]}


def test_tree_round_trips_operator_for_operator():
    xml = ptml.to_ptml(TREE, "demo")
    tree, nodes, back = _parse(xml)
    assert back == TREE and tree.get("name") == "demo"
    assert all(uuid.UUID(i) for i in nodes) and len(nodes) == 12   # 11 tree nodes + the loop's silent exit
    assert {e.tag for e in nodes.values()} == {"sequence", "xor", "and", "xorLoop", "manualTask", "automaticTask"}
    assert ptml.to_ptml(TREE, "demo") == xml   # deterministic


def test_unknown_operator_and_size_bound(monkeypatch):
    with pytest.raises(workspace.EventLogWorkspaceError):
        ptml.to_ptml({"op": "or", "children": []}, "x")
    monkeypatch.setattr(ptml, "MAX_NODES", 3)
    with pytest.raises(workspace.EventLogWorkspaceError, match="exceed 3 nodes"):
        ptml.to_ptml(TREE, "x")


def test_discovered_log_matches_the_discovery_tree():
    rows = ["case,activity,time"]
    for i in range(20):
        rows += [f"c{i},Receive,2024-03-01T08:00:00Z", f"c{i},{'Approve' if i % 2 else 'Reject'},2024-03-01T09:00:00Z",
                 f"c{i},Close,2024-03-01T10:00:00Z"]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="claims", source_format="csv",
                               mapping=MAPPING)["log_id"]
    xml, meta = ptml.discovered_ptml(log)
    assert 'name="claims (case) discovered tree"' in xml and meta["activities"] == 4
    assert _parse(xml)[2] == _strip(discovery.discover(log)["tree"])


@pytest.mark.skipif(not os.environ.get("OWG_PM4PY_PYTHON"), reason="set OWG_PM4PY_PYTHON to a Python with pm4py")
def test_pm4py_reads_the_file(tmp_path):
    path = tmp_path / "t.ptml"
    path.write_text(ptml.to_ptml(TREE, "demo"), encoding="utf-8")
    code = "import pm4py,sys; t=pm4py.read_ptml(sys.argv[1]); print(t)"
    done = subprocess.run([os.environ["OWG_PM4PY_PYTHON"], "-c", code, str(path)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    printed = done.stdout.strip().splitlines()[-1]
    # PM4Py folds the loop's silent exit into its two-child loop; parallel children have no order.
    assert printed in ("->( 'Receive', X( tau, 'Check \"A\" & <B>' ), +( 'Pay', 'Ship' ), *( 'Fix', tau ) )",
                       "->( 'Receive', X( tau, 'Check \"A\" & <B>' ), +( 'Ship', 'Pay' ), *( 'Fix', tau ) )"), printed


def test_route_is_dashboard_only(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T08:00:00Z" for i in range(6)] + [f"c{i},B,2024-03-0{1+i}T09:00:00Z" for i in range(6)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/model-discovery.ptml"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path + "?noise=0.1", headers=owner)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml"), r.text
    assert "<ptml>" in r.text and r.headers["x-owg-process-tree"] == "nodes=3; activities=2" and "attachment" in r.headers["content-disposition"]
    assert client.get(path + "?noise=1", headers=owner).status_code == 422
    assert client.get(path + "?resource=nobody", headers=owner).status_code == 422
print("ok")
'''
    env = {**os.environ, "WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
           "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")}
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
