"""A saved log can be renamed with the same name rule as at import; nothing else changes."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def test_rename_keeps_everything_else():
    rows = ["case,activity,time"] + [f"c{i},A,2024-01-0{1 + i}T08:00:00Z" for i in range(4)]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="orders", source_format="csv", mapping=MAPPING)["log_id"]
    before = workspace.summary(log)
    r = workspace.rename_log(log, "  Orders 2024 (EU)  ")
    assert r["renamed"] and r["name"] == "Orders 2024 (EU)" and r["log_id"] == log
    assert [l["name"] for l in workspace.list_logs()["logs"]] == ["Orders 2024 (EU)"]
    after = workspace.summary(log)
    assert after["cases"] == before["cases"] and after["log"]["events"] == before["log"]["events"]
    for bad in ("", "   ", "x" * 121, "a<b>", "line\nbreak", 5, None):
        with pytest.raises(EventLogWorkspaceError):
            workspace.rename_log(log, bad)
    with pytest.raises(EventLogWorkspaceError):
        workspace.rename_log("log_0000000000000000", "x")


def test_route_is_owner_only(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(3)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/'+log+'/rename'
    assert client.post(url,json={"name":"x"},headers=bearer).status_code in (401,403)
    r=client.post(url,json={"name":"Claims"},headers=owner)
    assert r.status_code==200 and r.json()['name']=='Claims', r.text
    assert client.post(url,json={"name":"<x>"},headers=owner).status_code==422
    assert client.post(url,json={"title":"x"},headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
