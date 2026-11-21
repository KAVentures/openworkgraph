"""The owner sees which agent tools read a shared log, when and whether they were answered."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_access as access
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    rows = ["case,activity,time"] + [f"c{i},A,2024-01-0{1 + i}T08:00:00Z" for i in range(6)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="a", source_format="csv", mapping=MAPPING)["log_id"]


def test_record_list_trim_and_delete(monkeypatch):
    log = _log()
    assert access.access(log)["recorded"] == 0
    access.record(log, "overview", "ok")
    access.record(log, "explain_case", "not_shared")
    access.record(log, "overview", "ok")
    access.record("log_0000000000000000", "overview", "ok")      # unknown log: ignored
    access.record(log, "overview", "bogus")                      # unknown outcome: ignored
    r = access.access(log)
    assert r["recorded"] == 3 and [c["operation"] for c in r["calls"]] == ["overview", "explain_case", "overview"]
    assert r["last_30_days"] == [{"operation": "overview", "calls": 2, "refused": 0},
                                 {"operation": "explain_case", "calls": 1, "refused": 1}]
    assert set(r["calls"][0]) == {"at", "operation", "outcome"}
    monkeypatch.setattr(access, "MAX_ROWS_PER_LOG", 5)
    for _ in range(8):
        access.record(log, "variants", "ok")
    assert access.access(log)["recorded"] <= 5
    with pytest.raises(EventLogWorkspaceError):
        access.access(log, limit=0)
    workspace.delete_log(log)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM agent_access_log WHERE log_id = ?", [log]).fetchone()[0] == 0
        finally:
            conn.close()


def test_agent_calls_are_recorded_owner_calls_are_not(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-0{1+i}T0{j}:00:00Z" for i in range(6) for j, a in enumerate("AB")]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    base='/v1/process-intelligence/'+log
    assert client.get(base+'/overview',headers=bearer).status_code==404          # not shared yet
    sharing.set_share(log,'aggregates')
    assert client.get(base+'/overview',headers=bearer).status_code==200
    assert client.get(base+'/case?case_id=c1',headers=bearer).status_code==404     # needs the cases level
    assert client.get(base+'/overview',headers=owner).status_code==200            # the owner: not recorded
    url='/v1/event-logs/'+log+'/agent-access'
    assert client.get(url,headers=bearer).status_code in (401,403) and client.get(url).status_code in (401,403)
    r=client.get(url,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    calls=r.json()['calls']
    assert [(c['operation'],c['outcome']) for c in calls]==[('explain_case','not_shared'),('overview','ok'),('overview','not_shared')], calls
    assert client.get(url+'?limit=0',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)


def test_a_failing_tool_is_recorded_as_error(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(6)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
sharing.set_share(log,'aggregates')
def overview(*args, **kwargs):
    raise RuntimeError("disk went away")
sharing.overview = overview
with TestClient(app, raise_server_exceptions=False) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.get('/v1/process-intelligence/'+log+'/overview',headers=bearer).status_code==500
    calls=client.get('/v1/event-logs/'+log+'/agent-access',headers=owner).json()['calls']
    assert [(c['operation'],c['outcome']) for c in calls]==[('overview','error')], calls
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
