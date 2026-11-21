"""Agent access to the breakdown table (aggregates, small cells withheld) and similar cases (cases level)."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "attribute_columns": ["region"]}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log():
    """Six north cases, six south cases and one case from a rare region; every 3rd case reworks B."""
    rows = ["case,activity,time,region"]
    regions = ["north"] * 6 + ["south"] * 6 + ["Rare Town"]
    for i, region in enumerate(regions):
        path = ["A", "B"] + (["B"] if i % 3 == 0 else []) + ["C"]
        for j, a in enumerate(path):
            rows.append(f"c{i},{a},2024-03-{1 + i:02d}T{8 + j:02d}:00:00Z,{region if j == 0 else ''}")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="r", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_breakdown_needs_aggregates_and_withholds_small_cells():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.breakdown(log, "event_attribute:region")
    sharing.set_share(log, "aggregates")
    r = sharing.breakdown(log, "event_attribute:region")
    assert r["row_values"] == ["north", "south"] and "Rare Town" not in str(r)
    assert {c["row"]: c["cases"] for c in r["cells"]} == {"north": 6, "south": 6}
    assert r["withheld"] == {"cells": 1, "row_values": 1, "column_values": 0, "min_cases": 5}
    assert r["cases"] == 13 and r["overall"]["cases"] == 13 and "small" not in r["overall"]
    two = sharing.breakdown(log, "event_attribute:region", "end_activity", with_activity="B")
    assert two["column_values"] == ["C"] and all(c["cases"] >= 5 for c in two["cells"])


def test_similar_cases_needs_the_cases_level():
    log = _log()
    sharing.set_share(log, "aggregates")
    with pytest.raises(sharing.NotShared):
        sharing.similar_cases(log, "c1")
    sharing.set_share(log, "cases")
    r = sharing.similar_cases(log, "c1", limit=3)
    assert r["log_id"] == log and len(r["similar"]) == 3
    assert all(c["distance"] == 0 for c in r["similar"])   # c1 has the common path A,B,C


def test_agent_routes(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-{1+i:02d}T0{j}:00:00Z" for i in range(8) for j, a in enumerate("ABC")]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    base='/v1/process-intelligence/'+log
    assert client.get(base+'/breakdown?rows=start_month',headers=bearer).status_code in (403,404)
    sharing.set_share(log,'aggregates')
    r=client.get(base+'/breakdown?rows=start_month&columns=end_activity',headers=bearer)
    assert r.status_code==200, r.text
    assert r.json()['cells'][0]['cases']==8
    assert client.get(base+'/breakdown?rows=nope',headers=bearer).status_code==422
    assert client.get(base+'/similar-cases?case_id=c1',headers=bearer).status_code in (403,404)
    sharing.set_share(log,'cases')
    s=client.get(base+'/similar-cases?case_id=c1&limit=2',headers=bearer)
    assert s.status_code==200 and len(s.json()['similar'])==2, s.text
    assert client.get(base+'/similar-cases?case_id=c1').status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
