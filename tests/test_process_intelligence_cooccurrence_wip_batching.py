"""Agents can read co-occurrence (with small counts withheld), work in progress and batching of a shared log."""
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
    """20 cases a day apart. 10 Approve (6 also Ship), 10 Reject, Escalate in 7 (3 Approve, 4 Reject), 3 Rare.
    Every Close is done by "clerk" at 18:00 on 2024-02-01, so they form one simultaneous batch."""
    rows = ["case,activity,time,who"]
    for i in range(20):
        steps = ["Open", "Approve" if i < 10 else "Reject"]
        steps += ["Ship"] if i < 6 else (["Escalate"] if 7 <= i < 14 else [])
        steps += ["Rare"] if i in (0, 1, 12) else []
        day = f"2024-01-{1 + i:02d}"
        for j, a in enumerate(steps):
            rows.append(f"c{i},{a},{day}T{8 + j:02d}:00:00Z,u{i}")
        rows.append(f"c{i},Close,2024-02-01T18:00:00Z,clerk")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="w", source_format="csv", mapping=MAPPING)["log_id"]


def test_cooccurrence_withholds_rare_activities_and_small_pairs():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.cooccurrence(log)
    sharing.set_share(log, "aggregates")
    r = sharing.cooccurrence(log, limit=8)
    names = [a["activity"] for a in r["activities"]]
    assert "Rare" not in names and {"Open", "Close", "Approve", "Reject", "Ship"} <= set(names)
    assert r["withheld"]["activities"] == 1 and r["min_support"] == 5
    row = {m["activity"]: {w["activity"]: w for w in m["with"]} for m in r["matrix"]}
    assert "Rare" not in row and all("Rare" not in cells for cells in row.values())
    assert row["Approve"]["Ship"]["both"] == 6 and row["Approve"]["Reject"]["both"] == 0
    assert row["Reject"]["Escalate"] == {"activity": "Escalate", "both": None, "share_of_a": None, "lift": None,
                                         "withheld": True}
    assert row["Approve"]["Escalate"]["withheld"] and r["withheld"]["pair_cells"] == 2
    assert any({p["a"], p["b"]} == {"Escalate", "Ship"} for p in r["never_together"])
    assert all(p["both"] >= 5 for p in r["go_together"])
    assert all("Rare" not in (p["a"], p["b"]) for p in r["never_together"])
    assert sharing.cooccurrence(log, limit=8, min_support=1)["min_support"] == 5
    with pytest.raises(EventLogWorkspaceError):
        sharing.cooccurrence(log, limit=1)


def test_work_in_progress_and_batching():
    log = _log()
    sharing.set_share(log, "aggregates")
    w = sharing.work_in_progress(log, bucket="day", end_activities=["Close"])
    assert w["cases"] == 20 and w["completed_cases"] == 20 and w["format"] == sharing.FORMAT
    assert max(b["open_at_end"] for b in w["buckets"]) == 20 and w["buckets"][-1]["open_at_end"] == 0
    assert sharing.work_in_progress(log)["settings"]["bucket"] == "week"
    with pytest.raises(EventLogWorkspaceError):
        sharing.work_in_progress(log, bucket="year")
    b = sharing.batching(log)
    close = {a["activity"]: a for a in b["activities"]}["Close"]
    assert close["batched_events"] == 20 and close["simultaneous_batches"] == 1 and close["resources_batching"] == 1
    assert "clerk" not in repr(b)
    with pytest.raises(EventLogWorkspaceError):
        sharing.batching(log, min_batch_size=1)


def test_agent_routes(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-{1+i:02d}T0{j}:00:00Z" for i in range(8) for j, a in enumerate("AB" + ("C" if i < 5 else "D"))]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    base='/v1/process-intelligence/'+log
    assert client.get(base+'/co-occurrence',headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    r=client.get(base+'/co-occurrence',headers=bearer)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert [a['activity'] for a in r.json()['activities']]==['A','B','C']
    assert client.get(base+'/co-occurrence?min_support=2',headers=bearer).status_code==422
    w=client.get(base+'/work-in-progress?bucket=day&end_activity=C&end_activity=D',headers=bearer)
    assert w.status_code==200 and w.json()['completed_cases']==8, w.text
    assert client.get(base+'/work-in-progress?bucket=year',headers=bearer).status_code==422
    b=client.get(base+'/batching?gap_seconds=0&min_batch_size=2&by_resource=false',headers=bearer)
    assert b.status_code==200 and b.json()['grouping']=='activity only', b.text
    assert client.get(base+'/batching').status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
