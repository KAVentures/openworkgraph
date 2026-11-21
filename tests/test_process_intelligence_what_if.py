"""Agents can run what-if simulations on a shared log, bounded, without the calibrated case paths."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log():
    """60 cases, several a day: Open > Approve (1 h, one manager) > Close (1 h). One case is Open > Rare > Close."""
    rows = ["case,activity,time,who"]
    for i in range(60):
        day, hour = 1 + i // 4, 8 + 2 * (i % 4)
        middle = "Rare" if i == 0 else "Approve"
        rows += [f"c{i},Open,2024-01-{day:02d}T{hour:02d}:00:00Z,clerk",
                 f"c{i},{middle},2024-01-{day:02d}T{hour + 1:02d}:00:00Z,mgr",
                 f"c{i},Close,2024-01-{day:02d}T{hour + 2:02d}:00:00Z,clerk"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="s", source_format="csv", mapping=MAPPING)["log_id"]


def test_scenario_compares_with_baseline_without_paths():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.simulate(log, {})
    sharing.set_share(log, "aggregates")
    r = sharing.simulate(log, {"cases": 1000, "activities": {"Approve": {"minutes": 30, "resources": 3}}})
    assert r["format"] == sharing.FORMAT and r["simulated_not_observed"] is True and r["calibrated_paths_returned"] is False
    assert "paths" not in r["calibration"] and r["calibration"]["paths_count"] == 2 and "paths" not in r["scenario"]
    approve = {a["activity"]: a for a in r["result"]["activities"]}["Approve"]
    assert approve["resources"] == 3
    assert approve["minutes"] == 30 and r["median_cycle_time_change_seconds"] < 0   # faster approval, shorter cycle
    assert "mgr" not in repr(r) and "clerk" not in repr(r)
    own = sharing.simulate(log, {"cases": 200, "paths": [{"activities": ["Open", "Close"], "weight": 1}]})
    assert own["scenario"]["paths"] == [{"activities": ["Open", "Close"], "weight": 1.0}]


def test_bounds_for_agents():
    log = _log()
    sharing.set_share(log, "aggregates")
    for bad in ({"cases": 5001}, {"cases": True}, {"uncertainty": {"replications": 21}}, {"bogus": 1}, "x"):
        with pytest.raises(EventLogWorkspaceError):
            sharing.simulate(log, bad)
    spread = sharing.simulate(log, {"cases": 200, "uncertainty": {"replications": 3}})
    assert "uncertainty" in spread


def test_agent_route(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},{a},2024-01-{1+i//3:02d}T{8+3*(i%3)+j:02d}:00:00Z" for i in range(30) for j, a in enumerate("ABC")]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/process-intelligence/'+log+'/simulate'
    body={"scenario":{"cases":300,"activities":{"B":{"minutes":5}}}}
    assert client.post(url,json=body,headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    r=client.post(url,json=body,headers=bearer)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert r.json()['median_cycle_time_change_seconds']<0 and 'paths' not in r.json()['calibration']
    assert client.post(url,json={"scenario":{"cases":9999}},headers=bearer).status_code==422
    assert client.post(url,json={"scenario":{},"x":1},headers=bearer).status_code==422
    assert client.post(url,json=body).status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
