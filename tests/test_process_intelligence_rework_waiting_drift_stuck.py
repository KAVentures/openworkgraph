"""Agent access to rework, waiting times and drift (aggregates) and stuck cases (cases level)."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
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


def _log():
    """30 closed cases over Jan–Mar (every 3rd repeats Check) and 3 open cases idle since early March."""
    rows = ["case,activity,time"]
    for i in range(30):
        month = 1 + i // 10
        steps = ["Open", "Check"] + (["Check"] if i % 3 == 0 else []) + ["Close"]
        for j, a in enumerate(steps):
            rows.append(f"c{i},{a},2024-0{month}-{1 + i % 10:02d}T{8 + j:02d}:00:00Z")
    for k in range(3):
        rows.append(f"open{k},Open,2024-03-0{1 + k}T08:00:00Z")
    rows.append("late,Open,2024-03-30T08:00:00Z")
    rows.append("late,Close,2024-03-30T09:00:00Z")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="w", source_format="csv", mapping=MAPPING)["log_id"]


def test_aggregate_tools_need_aggregates():
    log = _log()
    for call in (lambda: sharing.rework(log), lambda: sharing.waiting_times(log), lambda: sharing.drift(log)):
        with pytest.raises(sharing.NotShared):
            call()
    sharing.set_share(log, "aggregates")
    r = sharing.rework(log)
    assert r["cases_with_rework"] == 10 and r["activities"][0]["activity"] == "Check" and r["log_id"] == log
    assert "case_id" not in str(r)
    w = sharing.waiting_times(log)
    assert w["log_id"] == log and "available" in w
    d = sharing.drift(log, window="month")
    assert d["log_id"] == log and "windows" in d
    with pytest.raises(EventLogWorkspaceError):
        sharing.drift(log, window="cases")


def test_stuck_cases_need_the_cases_level():
    log = _log()
    sharing.set_share(log, "aggregates")
    with pytest.raises(sharing.NotShared):
        sharing.stuck_cases(log)
    sharing.set_share(log, "cases")
    s = sharing.stuck_cases(log, limit=5)
    assert s["log_id"] == log and s["stuck_cases"] == 3
    assert sorted(c["case_id"] for c in s["cases"]) == ["open0", "open1", "open2"]
    assert all(c["latest_activity"] == "Open" for c in s["cases"])


def test_agent_routes(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},{a},2024-0{1+i//5}-{1+i%5:02d}T0{j}:00:00Z" for i in range(15) for j, a in enumerate(["A","B"] + (["B"] if i%2 else []) + ["C"])]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    base='/v1/process-intelligence/'+log
    assert client.get(base+'/rework',headers=bearer).status_code in (403,404)
    sharing.set_share(log,'aggregates')
    r=client.get(base+'/rework',headers=bearer)
    assert r.status_code==200 and r.json()['cases_with_rework']==7, r.text
    assert client.get(base+'/waiting-times',headers=bearer).status_code==200
    assert client.get(base+'/drift?window=week',headers=bearer).status_code==200
    assert client.get(base+'/drift?window=cases',headers=bearer).status_code==422
    assert client.get(base+'/stuck-cases',headers=bearer).status_code in (403,404)
    sharing.set_share(log,'cases')
    assert client.get(base+'/stuck-cases?limit=3',headers=bearer).status_code==200
    assert client.get(base+'/rework').status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
