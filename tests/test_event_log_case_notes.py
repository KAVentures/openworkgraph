"""Case notes: the owner's notes on single cases, owner-only, deleted with the log."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_case_notes as cn
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    rows = ["case,activity,time"] + [f"c{i},A,2024-01-0{1 + i}T08:00:00Z" for i in range(5)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="n", source_format="csv", mapping=MAPPING)["log_id"]


def test_add_list_per_case_and_remove():
    log = _log()
    assert cn.notes(log)["notes"] == [] and cn.notes(log)["cases_with_notes"] == 0
    first = cn.add(log, {"case_id": "c1", "text": "  Customer called twice\nabout the delay "})
    assert first["case_id"] == "c1" and first["notes"][0]["text"] == "Customer called twice\nabout the delay"
    cn.add(log, {"case_id": "c1", "text": "Waiting on credit check"})
    cn.add(log, {"case_id": "c3", "text": "Fine"})
    assert cn.notes(log)["notes_total"] == 3 and cn.notes(log)["cases_with_notes"] == 2
    assert [n["text"] for n in cn.notes(log, "c1")["notes"]] == ["Waiting on credit check", "Customer called twice\nabout the delay"]
    assert cn.notes(log, "c4")["notes"] == []
    gone = cn.remove(log, first["added"])
    assert gone["removed_from_case"] == "c1" and gone["notes_total"] == 2
    with pytest.raises(EventLogWorkspaceError):
        cn.remove(log, first["added"])


def test_validation_and_limit(monkeypatch):
    log = _log()
    for bad, match in (({"case_id": "nope", "text": "x"}, "no case"), ({"case_id": "c1", "text": "   "}, "write something"),
                       ({"case_id": "c1", "text": "a\x01b"}, "control"), ({"case_id": "c1"}, "invalid"),
                       ({"case_id": "c1", "text": "x", "extra": 1}, "invalid"),
                       ({"case_id": "c1", "text": "x" * 2001}, "invalid")):
        with pytest.raises(EventLogWorkspaceError, match=match):
            cn.add(log, bad)
    monkeypatch.setattr(cn, "MAX_NOTES_PER_LOG", 1)
    cn.add(log, {"case_id": "c1", "text": "one"})
    with pytest.raises(EventLogWorkspaceError, match="at most 1"):
        cn.add(log, {"case_id": "c2", "text": "two"})


def test_deleting_the_log_deletes_its_notes_only():
    log, other = _log(), _log()
    cn.add(log, {"case_id": "c1", "text": "gone with the log"})
    cn.add(other, {"case_id": "c1", "text": "stays"})
    workspace.delete_log(log)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT log_id, text FROM case_notes").fetchall() == [(other, "stays")]
        finally:
            conn.close()


def test_a_failed_note_cleanup_rolls_the_deletion_back(monkeypatch):
    log = _log()
    cn.add(log, {"case_id": "c1", "text": "keep me"})

    def fail(conn, log_id):
        conn.execute("DELETE FROM case_notes WHERE log_id = ?", [log_id])
        raise RuntimeError("disk full")
    monkeypatch.setattr(cn, "erase", fail)
    with pytest.raises(RuntimeError):
        workspace.delete_log(log)
    assert cn.notes(log)["notes"][0]["text"] == "keep me" and workspace.summary(log)["cases"] == 5


def test_routes_are_owner_only(tmp_path):
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
    url='/v1/event-logs/'+log+'/case-notes'
    body={"case_id":"c1","text":"Called"}
    assert client.get(url,headers=bearer).status_code in (401,403)
    assert client.post(url,json=body,headers=bearer).status_code in (401,403)
    r=client.post(url,json=body,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    note=r.json()['added']
    assert client.get(url+'?case_id=c1',headers=owner).json()['notes'][0]['text']=='Called'
    assert client.post(url,json={"case_id":"zz","text":"x"},headers=owner).status_code==422
    assert client.post(url+'/'+note+'/delete',headers=bearer).status_code in (401,403)
    assert client.post(url+'/'+note+'/delete',headers=owner).json()['notes']==[]
    assert client.post(url+'/'+note+'/delete',headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
