"""BPMN export of a discovered model: valid BPMN 2.0 with DI that OWG's own importer reads back."""
from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree as ET

import pytest

from server import event_log_bpmn_export as bpmn
from server import event_log_workspace as workspace
from shared.process_bpmn_import import import_bpmn

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL", "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log(traces) -> str:
    rows = ["case,activity,time"]
    for i, trace in enumerate(traces):
        rows += [f"c{i},{a},2024-03-01T{8 + k:02d}:00:00Z" for k, a in enumerate(trace)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="claims", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_choice_becomes_exclusive_gateways_with_diagram():
    log = _log([["Receive", "Approve" if i % 2 else "Reject", "Close"] for i in range(20)])
    xml, meta = bpmn.discovered_bpmn(log)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xml)
    process = root.find("bpmn:process", NS)
    assert process.get("isExecutable") == "false" and process.get("name") == "claims (case) discovered model"
    doc = process.find("bpmn:documentation", NS).text
    assert doc.startswith("Unreviewed draft discovered") and meta["process_model_sha256"] in doc
    assert "Reviewed design" not in xml
    names = {t.get("name") for t in process.findall("bpmn:task", NS)}
    assert {"Receive", "Approve", "Reject", "Close"} <= names
    assert process.findall("bpmn:exclusiveGateway", NS) and process.find("bpmn:startEvent", NS) is not None
    assert process.find("bpmn:endEvent", NS) is not None
    ids = {e.get("id") for e in process}
    flows = process.findall("bpmn:sequenceFlow", NS)
    assert all(f.get("sourceRef") in ids and f.get("targetRef") in ids for f in flows)
    shapes = root.findall(".//bpmndi:BPMNShape", NS)
    assert {s.get("bpmnElement") for s in shapes} >= {t.get("id") for t in process.findall("bpmn:task", NS)}
    assert len(root.findall(".//bpmndi:BPMNEdge", NS)) == len(flows)
    assert meta["tasks"] == 4 and meta["flows"] == len(flows) and meta["fitting_case_share"] == 1.0


def test_concurrency_becomes_parallel_gateways():
    log = _log([["Start", "Check", "Pay", "End"] if i % 2 else ["Start", "Pay", "Check", "End"] for i in range(20)])
    xml, meta = bpmn.discovered_bpmn(log, noise=0)
    gateways = ET.fromstring(xml).findall(".//bpmn:parallelGateway", NS)
    assert {g.get("gatewayDirection") for g in gateways} == {"Diverging", "Converging"}
    assert meta["gateways"] >= 2


def test_owg_bpmn_import_reads_the_export_back():
    log = _log([["Receive", "Approve" if i % 3 else "Reject", "Close"] for i in range(30)])
    xml, _ = bpmn.discovered_bpmn(log)
    draft = import_bpmn(xml.encode())
    labels = {a["label"] for a in draft["process_model"]["activities"] if a["kind"] == "activity"}
    assert labels == {"Receive", "Approve", "Reject", "Close"}


def test_route_is_dashboard_only(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
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
    path = f"/v1/event-logs/{log_id}/model-discovery.bpmn"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path + "?noise=0.1", headers=owner)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml"), r.text
    assert "definitions" in r.text and r.headers["x-owg-bpmn"].startswith("tasks=2") and "attachment" in r.headers["content-disposition"]
    assert client.get(path + "?noise=1", headers=owner).status_code == 422
    assert client.get(path + "?resource=nobody", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
