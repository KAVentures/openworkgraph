"""Agents can read process cost from the owner's saved cost model or from their own prices, without
per-resource costs and with rare activities and variants withheld."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_costs as cost_model
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log():
    """10 cases Open > Check > Close (6 of them check twice), 1 case Open > Escalate > Close."""
    rows = ["case,activity,time,who"]
    for i in range(11):
        steps = ["Open", "Escalate", "Close"] if i == 10 else ["Open", "Check"] + (["Check"] if i < 6 else []) + ["Close"]
        rows += [f"c{i},{a},2024-01-{1 + i:02d}T{8 + j:02d}:00:00Z,ana" for j, a in enumerate(steps)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="c", source_format="csv", mapping=MAPPING)["log_id"]


PRICES = {"currency": "EUR", "activity_costs": {"Open": 1, "Check": 10, "Close": 2, "Escalate": 50}}


def test_saved_model_and_agent_prices():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.costs(log)
    sharing.set_share(log, "aggregates")
    with pytest.raises(EventLogWorkspaceError, match="not saved a cost model"):
        sharing.costs(log)
    cost_model.save_model(log, PRICES)
    r = sharing.costs(log)
    assert r["model_source"] == "owner_saved_model" and r["currency"] == "EUR" and r["format"] == sharing.FORMAT
    assert r["total_cost"] == 11 * 3 + 16 * 10 + 50   # Open+Close per case, 16 checks, 1 escalation
    assert r["repeated_execution_cost"] == 60                       # 6 second checks
    names = [a["activity"] for a in r["activities"]]
    assert "Escalate" not in names and {"Open", "Check", "Close"} <= set(names)
    assert [(v["path"], v["cases"]) for v in r["costliest_variants"]] == [(["Open", "Check", "Check", "Close"], 6)]
    assert "max" not in r["cost_per_case"] and "resources" not in r and "ana" not in repr(r)
    assert r["withheld"]["activities"] == 1 and r["withheld"]["variants"] == 2     # 4-case and 1-case paths
    what_if = sharing.costs(log, {"currency": "EUR", "activity_costs": {"Check": 5}, "default_activity_cost": 0})
    assert what_if["model_source"] == "agent_supplied_model" and what_if["total_cost"] == 80
    for bad in ({"activity_costs": {"Check": -1}}, {"limit": 3}, "x"):
        with pytest.raises(EventLogWorkspaceError):
            sharing.costs(log, bad)
    with pytest.raises(EventLogWorkspaceError):
        sharing.costs(log, limit=0)


def test_agent_route(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing, event_log_costs as cm
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-{1+i:02d}T0{j}:00:00Z" for i in range(6) for j, a in enumerate("AB")]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/process-intelligence/'+log+'/costs'
    assert client.post(url,json={},headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    assert client.post(url,json={},headers=bearer).status_code==422
    cm.save_model(log,{"currency":"USD","activity_costs":{"A":1,"B":2}})
    r=client.post(url,json={},headers=bearer)
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and r.json()['total_cost']==18, r.text
    w=client.post(url,json={"model":{"activity_costs":{"A":10,"B":0}}},headers=bearer)
    assert w.json()['total_cost']==60 and w.json()['model_source']=='agent_supplied_model', w.text
    assert client.post(url,json={"x":1},headers=bearer).status_code==422
    assert client.post(url,json={}).status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
