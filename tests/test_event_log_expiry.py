"""The owner can have a log deleted automatically after a date; an expired log is never answered again."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_expiry as expiry
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log(name="x"):
    rows = ["case,activity,time"] + [f"c{i},A,2024-01-0{1 + i}T08:00:00Z" for i in range(6)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def test_set_change_clear_and_validate():
    log = _log()
    assert expiry.status(log)["delete_after"] is None
    r = expiry.set_expiry(log, _iso(timedelta(days=30)))
    assert r["delete_after"].endswith("Z") and r["set_at"]
    later = expiry.set_expiry(log, "2030-01-01T00:00:00+01:00")
    assert later["delete_after"] == "2029-12-31T23:00:00Z"
    assert expiry.set_expiry(log, None)["delete_after"] is None
    for bad in ("tomorrow", "2030-01-01T00:00:00", _iso(timedelta(days=-1)), _iso(timedelta(days=366 * 11)), 5):
        with pytest.raises(EventLogWorkspaceError):
            expiry.set_expiry(log, bad)
    with pytest.raises(EventLogWorkspaceError):
        expiry.status("log_0000000000000000")


def test_sweep_deletes_only_expired_logs_and_agents_never_see_them(monkeypatch):
    old, kept, plain = _log("old"), _log("kept"), _log("plain")
    expiry.set_expiry(old, _iso(timedelta(days=1)))
    expiry.set_expiry(kept, _iso(timedelta(days=60)))
    sharing.set_share(old, "aggregates")
    sharing.set_share(kept, "aggregates")
    assert sharing.overview(old)["cases"] == 6
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)
    monkeypatch.setattr(expiry, "_now", lambda: future)
    assert expiry.is_expired(old) and not expiry.is_expired(kept)
    with pytest.raises(sharing.NotShared):                 # an agent asking first: refused and deleted
        sharing.overview(old)
    assert {l["log_id"] for l in workspace.list_logs()["logs"]} == {kept, plain}
    assert expiry.sweep()["deleted"] == []
    assert [l["name"] for l in sharing.shared_logs()["logs"]] == ["kept"]
    workspace.delete_log(kept)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM log_expiry").fetchone()[0] == 0
        finally:
            conn.close()


def test_clearing_or_extending_the_date_wins_over_a_sweep_already_under_way(monkeypatch):
    """The sweep reads the expired IDs, then the owner acts before it deletes: the log must survive."""
    cleared, extended, due = _log("cleared"), _log("extended"), _log("due")
    for log in (cleared, extended, due):
        expiry.set_expiry(log, _iso(timedelta(days=1)))
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)
    original = expiry._expired

    def selected_then_owner_acts():
        ids = original()                                  # the sweep has picked all three
        monkeypatch.setattr(expiry, "_now", lambda: datetime.now(timezone.utc).replace(tzinfo=None))
        expiry.set_expiry(cleared, None)                  # Keep the log
        expiry.set_expiry(extended, _iso(timedelta(days=30)))
        monkeypatch.setattr(expiry, "_now", lambda: future)
        return ids

    monkeypatch.setattr(expiry, "_now", lambda: future)
    monkeypatch.setattr(expiry, "_expired", selected_then_owner_acts)
    assert sorted(original()) == sorted([cleared, extended, due])
    assert expiry.sweep()["deleted"] == [due]
    assert {l["name"] for l in workspace.list_logs()["logs"]} == {"cleared", "extended"}
    assert expiry.status(cleared)["delete_after"] is None
    assert workspace.delete_log(extended, only_if_expired_at=future)["deleted"] is False


def test_routes(tmp_path):
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
    url='/v1/event-logs/'+log+'/expiry'
    assert client.get(url,headers=bearer).status_code in (401,403)
    assert client.post(url,json={"delete_after":"2030-01-01T00:00:00Z"},headers=bearer).status_code in (401,403)
    r=client.post(url,json={"delete_after":"2030-01-01T00:00:00Z"},headers=owner)
    assert r.status_code==200 and r.json()['delete_after']=='2030-01-01T00:00:00Z', r.text
    assert client.get(url,headers=owner).json()['delete_after']=='2030-01-01T00:00:00Z'
    assert client.post(url,json={"delete_after":"2000-01-01T00:00:00Z"},headers=owner).status_code==422
    assert client.post(url,json={"when":"x"},headers=owner).status_code==422
    assert client.post(url,json={"delete_after":None},headers=owner).json()['delete_after'] is None
    assert any(l['log_id']==log for l in client.get('/v1/event-logs',headers=owner).json()['logs'])
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
