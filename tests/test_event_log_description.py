"""The owner's description of a saved log."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_description as d
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    rows = ["case,activity,time"] + [f"c{i},A,2024-01-0{1 + i}T08:00:00Z" for i in range(3)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="d", source_format="csv", mapping=MAPPING)["log_id"]


def test_set_read_clear_validate_and_delete():
    log = _log()
    assert d.description(log) == {"format": d.FORMAT, "log_id": log, "description": "", "updated_at": None}
    r = d.set_description(log, "  SAP export for the Q3 audit\nfinance only  ")
    assert r["description"] == "SAP export for the Q3 audit\nfinance only" and r["updated_at"]
    assert d.description(log)["description"].startswith("SAP export")
    assert d.set_description(log, "   ")["description"] == ""
    for bad in (5, None, "x" * 2001, "a\x01b"):
        with pytest.raises(EventLogWorkspaceError):
            d.set_description(log, bad)
    d.set_description(log, "keep")
    workspace.delete_log(log)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM log_descriptions WHERE log_id = ?", [log]).fetchone()[0] == 0
        finally:
            conn.close()
    with pytest.raises(EventLogWorkspaceError):
        d.description(log)


def test_a_failed_cleanup_rolls_the_whole_deletion_back(monkeypatch):
    log = _log()
    d.set_description(log, "keep me")
    original = d.erase

    def fail(conn, log_id):
        original(conn, log_id)
        raise RuntimeError("disk full")

    monkeypatch.setattr(d, "erase", fail)
    with pytest.raises(RuntimeError):
        workspace.delete_log(log)
    assert [l["log_id"] for l in workspace.list_logs()["logs"]] == [log]
    assert d.description(log)["description"] == "keep me"
    monkeypatch.setattr(d, "erase", original)
    workspace.delete_log(log)
    assert workspace.list_logs()["logs"] == []


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
    url='/v1/event-logs/'+log+'/description'
    assert client.get(url,headers=bearer).status_code in (401,403)
    assert client.post(url,json={"description":"x"},headers=bearer).status_code in (401,403)
    r=client.post(url,json={"description":"Audit log"},headers=owner)
    assert r.status_code==200 and r.json()['description']=='Audit log', r.text
    assert client.get(url,headers=owner).json()['description']=='Audit log'
    assert client.post(url,json={"text":"x"},headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
