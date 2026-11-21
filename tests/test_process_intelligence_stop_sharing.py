"""The owner can stop sharing every log with agents at once, tag permissions included."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log(name):
    rows = ["case,activity,time"] + [f"c{i},A,2024-01-0{1 + i}T08:00:00Z" for i in range(6)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def test_stop_sharing_every_log():
    a, b, c = _log("orders"), _log("claims"), _log("hr")
    sharing.set_share(a, "aggregates")
    sharing.set_share(b, "cases")
    sharing.set_tag_grant(b, True)
    assert sharing.tools_enabled() is True
    r = sharing.stop_sharing_all()
    assert (r["stopped"], r["logs"], r["shared_logs"]) == (2, ["claims", "orders"], 0)
    assert sharing.shared_logs()["logs"] == []
    for log in (a, b, c):
        status = sharing.share_status(log)
        assert status["level"] is None and status["agents_may_tag_cases"] is False
    assert sharing.tools_enabled() is False
    with pytest.raises(sharing.NotShared):
        sharing.overview(b)
    assert sharing.stop_sharing_all()["stopped"] == 0


def test_route_is_dashboard_only(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(6)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
sharing.set_share(log,'aggregates')
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/process-intelligence/stop-sharing'
    assert client.post(url,headers=bearer).status_code in (401,403)     # an agent cannot stop (or start) sharing
    assert client.post(url).status_code in (401,403)
    assert client.get('/v1/process-intelligence/'+log+'/overview',headers=bearer).status_code==200
    r=client.post(url,headers=owner)
    assert r.status_code==200 and r.json()['stopped']==1 and r.headers['cache-control']=='no-store', r.text
    assert client.get('/v1/process-intelligence/'+log+'/overview',headers=bearer).status_code==404
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
