"""Agents can read how far a shared log can be trusted: rejected rows, diagnostics, integrity and trend."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def test_quality_over_import_and_append():
    rows = ["case,activity,time,who"] + [f"c{i},A,2024-01-0{1 + i}T08:00:00Z,u{i}" for i in range(6)]
    rows += ["c0,B,2024-01-01T08:00:00Z,u0",        # shares a timestamp with c0's A
             "c9,,2024-01-05T08:00:00Z,x",          # missing activity
             "c8,B,2024-01-05T08:00:00,y"]          # no UTC offset
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv", mapping=MAPPING)["log_id"]
    more = "case,activity,time,who\nc1,B,2024-02-01T08:00:00Z,\nc2,B,not-a-time,u2\n"
    workspace.append_log(log, BytesIO(more.encode()), source_format="csv", mapping=MAPPING)
    with pytest.raises(sharing.NotShared):
        sharing.data_quality(log)
    sharing.set_share(log, "aggregates")
    r = sharing.data_quality(log)
    assert r["batches"] == 2 and r["format"] == sharing.FORMAT
    assert r["rows"]["accepted"] == 8 and r["rows"]["rejected"] == 3
    reasons = r["rows"]["rejected_by_reason"]
    assert reasons["missing_activity"] == 1 and reasons["timestamp_without_utc_offset"] == 2   # one per batch
    d = r["diagnostics"]
    assert d["events"] == 8 and d["events_sharing_a_timestamp_in_a_case"] == 2 and d["events_without_resource"] == 1
    assert r["integrity"] == {"status": "not_verifiable", "reason": "historical_batches_or_missing_report"}  # appended
    assert len(r["batch_trend"]) == 2
    text = repr(r)
    for hidden in ("sha256", "mapping", "'c0'"):
        assert hidden not in text, (hidden, text[max(0, text.find(hidden) - 80):text.find(hidden) + 40])


def test_agent_route(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(5)] + ["c9,,2024-03-01T00:00:00Z"]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/process-intelligence/'+log+'/data-quality'
    assert client.get(url,headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    r=client.get(url,headers=bearer)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert r.json()['rows']['rejected']==1 and r.json()['batches']==1
    assert r.json()['integrity']['status']=='verified'
    assert client.get(url).status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
