"""The owner sees how much disk the saved logs use and which logs take the most."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_storage as st
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log(name, cases):
    rows = ["case,activity,time"] + [f"c{i},{a},2024-01-01T0{j}:00:00Z" for i in range(cases) for j, a in enumerate("AB")]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def test_sizes_shares_and_order():
    empty = st.storage()
    assert empty["log_count"] == 0 and empty["logs"] == []
    small, big = _log("small", 5), _log("big", 45)
    r = st.storage()
    assert [l["name"] for l in r["logs"]] == ["big", "small"] and r["log_count"] == 2
    assert r["database_bytes"] > 0 and r["free_disk_bytes"] and r["free_disk_bytes"] > 0
    shares = {l["name"]: l["share"] for l in r["logs"]}
    assert abs(sum(shares.values()) - 1) < 0.01 and shares["big"] > 0.85
    assert r["logs"][0]["events"] == 90 and r["logs"][0]["estimated_bytes"] <= r["database_bytes"]
    workspace.delete_log(big)
    assert [l["name"] for l in st.storage()["logs"]] == ["small"] and st.storage()["logs"][0]["share"] == 1.0


def test_route_is_owner_only(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T00:00:00Z" for i in range(3)]
workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.get('/v1/event-logs/storage',headers=bearer).status_code in (401,403)
    r=client.get('/v1/event-logs/storage',headers=owner)
    assert r.status_code==200 and r.json()['logs'][0]['name']=='q' and r.headers['cache-control']=='no-store', r.text
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
