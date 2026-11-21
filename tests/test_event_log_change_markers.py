"""Change markers: the owner's dated notes on a log's timeline."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_change_markers as cm
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    rows = ["case,activity,time"] + [f"c{i},A,2024-01-{1 + i:02d}T08:00:00Z" for i in range(20)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="m", source_format="csv", mapping=MAPPING)["log_id"]


def test_add_list_remove_and_validate():
    log = _log()
    assert cm.markers(log)["markers"] == [] and cm.markers(log)["log_span"]["first_event_at"] == "2024-01-01T08:00:00Z"
    r = cm.add(log, {"marked_at": "2024-01-10T09:00:00+01:00", "label": "  Automation went live ", "note": "Bot v1"})
    cm.add(log, {"marked_at": "2023-06-01T00:00:00Z", "label": "Old rule"})
    listing = cm.markers(log)["markers"]
    assert [(m["marked_at"], m["label"], m["within_log"]) for m in listing] == [
        ("2023-06-01T00:00:00Z", "Old rule", False), ("2024-01-10T08:00:00Z", "Automation went live", True)]
    assert listing[1]["note"] == "Bot v1" and listing[1]["marker_id"] == r["added"]
    assert cm.remove(log, r["added"])["removed"] == r["added"] and len(cm.markers(log)["markers"]) == 1
    with pytest.raises(EventLogWorkspaceError):
        cm.remove(log, r["added"])
    for bad in ({"marked_at": "2024-01-10", "label": "x"}, {"marked_at": "2024-01-10T00:00:00", "label": "x"},
                {"marked_at": "2024-01-10T00:00:00Z", "label": " "}, {"marked_at": "2024-01-10T00:00:00Z"},
                {"marked_at": "2024-01-10T00:00:00Z", "label": "x", "extra": 1},
                {"marked_at": "2024-01-10T00:00:00Z", "label": "a\x01b"}):
        with pytest.raises(EventLogWorkspaceError):
            cm.add(log, bad)


def test_limit_and_deletion_with_the_log(monkeypatch):
    log = _log()
    monkeypatch.setattr(cm, "MAX_MARKERS_PER_LOG", 2)
    cm.add(log, {"marked_at": "2024-01-02T00:00:00Z", "label": "a"})
    cm.add(log, {"marked_at": "2024-01-03T00:00:00Z", "label": "b"})
    with pytest.raises(EventLogWorkspaceError, match="at most 2"):
        cm.add(log, {"marked_at": "2024-01-04T00:00:00Z", "label": "c"})
    workspace.delete_log(log)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM change_markers WHERE log_id = ?", [log]).fetchone()[0] == 0
        finally:
            conn.close()


def test_routes_are_owner_only(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(6)]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/'+log+'/change-markers'
    body={"marked_at":"2024-03-03T00:00:00Z","label":"Go-live"}
    assert client.get(url,headers=bearer).status_code in (401,403) and client.post(url,json=body,headers=bearer).status_code in (401,403)
    r=client.post(url,json=body,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    marker=r.json()['added']
    assert client.get(url,headers=owner).json()['markers'][0]['label']=='Go-live'
    assert client.post(url,json={"label":"x"},headers=owner).status_code==422
    assert client.post(url+'/bogus/delete',headers=owner).status_code==422
    assert client.post(url+'/'+marker+'/delete',headers=bearer).status_code in (401,403)
    assert client.post(url+'/'+marker+'/delete',headers=owner).json()['markers']==[]
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
