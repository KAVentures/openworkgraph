"""Agents can list the owner's saved reference nets and see where cases depart from one (aggregates level)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_reference_nets as nets
from server import event_log_workspace as workspace
from tests.test_event_log_petri_replay import _import, _sequence_net


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def test_reference_nets_and_deviations_need_aggregates():
    log = _import([["A", "B", "C"]] * 4 + [["A", "C"]] * 2)
    saved = nets.save(log, "Approved process", _sequence_net(["A", "B", "C"]).decode())
    net_id = saved["net_id"]
    with pytest.raises(sharing.NotShared):
        sharing.reference_nets(log)
    with pytest.raises(sharing.NotShared):
        sharing.reference_deviations(log, net_id)
    sharing.set_share(log, "aggregates")
    listed = sharing.reference_nets(log)
    assert [(n["net_id"], n["name"]) for n in listed["nets"]] == [(net_id, "Approved process")]
    assert "pnml" not in str(listed)
    r = sharing.reference_deviations(log, net_id)
    assert r["net_name"] == "Approved process" and r["cases"] == 6
    assert r["fitness"]["fitting_cases"] == 4
    assert r["model_moves"][0]["activity"] == "B" and r["model_moves"][0]["cases"] == 2
    with pytest.raises(workspace.EventLogWorkspaceError, match="no reference net"):
        sharing.reference_deviations(log, "net_missing")


def test_agent_routes(tmp_path):
    net = _sequence_net(["A", "B", "C"]).decode()
    code = r'''
import sys
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
from server import event_log_reference_nets as nets
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-{1+i:02d}T0{j}:00:00Z" for i in range(4) for j, a in enumerate("ABC")]
rows += ["d,A,2024-03-09T00:00:00Z", "d,C,2024-03-09T01:00:00Z"]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
saved = nets.save(log, "Approved", sys.argv[1])
net_id = saved["net_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    base='/v1/process-intelligence/'+log
    assert client.get(base+'/reference-nets',headers=bearer).status_code in (403,404)
    sharing.set_share(log,'aggregates')
    listed=client.get(base+'/reference-nets',headers=bearer)
    assert listed.status_code==200 and listed.json()['nets'][0]['name']=='Approved', listed.text
    r=client.get(base+'/reference-nets/'+net_id+'/deviations',headers=bearer)
    assert r.status_code==200 and r.json()['fitness']['fitting_cases']==4, r.text
    assert client.get(base+'/reference-nets/net_nope/deviations',headers=bearer).status_code==422
    assert client.get(base+'/reference-nets').status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code, net], env=env, check=True, timeout=60)
