"""Agents can see the overview of a filtered set of cases (at least 5) next to the whole log."""
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
           "attribute_columns": ["region"]}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log():
    """8 EU cases (2 h, Escalate in 6 of them), 4 US cases (1 h), 1 rare-region case."""
    rows = ["case,activity,time,region"]
    for i in range(13):
        region = "EU" if i < 8 else ("US" if i < 12 else "Rare")
        steps = ["Open"] + (["Escalate"] if i < 6 else []) + ["Close"]
        hours = 2 if region == "EU" else 1
        for j, a in enumerate(steps):
            minute = 0 if j == 0 else (hours * 60 if a == "Close" else 30)
            rows.append(f"c{i},{a},2024-05-{1 + i:02d}T{8 + minute // 60:02d}:{minute % 60:02d}:00Z,{region if j == 0 else ''}")
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="o", source_format="csv", mapping=MAPPING)["log_id"]


def test_filtered_overview_next_to_whole_log():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.overview_for_cases(log, {"event_attribute": "region=EU"})
    sharing.set_share(log, "aggregates")
    r = sharing.overview_for_cases(log, {"event_attribute": "region=EU"})
    assert (r["cases"], r["cases_in_log"], r["share_of_log"]) == (8, 13, round(8 / 13, 4))
    assert r["case_duration_seconds"]["median"] == 7200 and r["whole_log_case_duration_seconds"]["median"] == 7200
    assert r["case_filter"] == {"event_attribute": "region=EU"}
    assert r["top_variants"][0] == {"path": ["Open", "Escalate", "Close"], "cases": 6, "share": 0.75}
    assert "rework_rate" in r["kpis"] and "rework_rate" in r["whole_log_kpis"]
    sla = sharing.overview_for_cases(log, {"event_attribute": "region=EU"}, sla_hours=1.5)
    assert sla["kpis"]["sla"]["breached_cases"] == 8 and sla["whole_log_kpis"]["sla"]["breached_cases"] == 8


def test_floor_and_validation():
    log = _log()
    sharing.set_share(log, "aggregates")
    for narrow in ({"event_attribute": "region=US"}, {"event_attribute": "region=Rare"}, {"activity": "Nope"}):
        with pytest.raises(EventLogWorkspaceError, match="fewer than 5 cases"):
            sharing.overview_for_cases(log, narrow)
    for bad in (None, {}, {"label": "x"}, {"bogus": 1}, "region=EU"):
        with pytest.raises(EventLogWorkspaceError):
            sharing.overview_for_cases(log, bad)
    assert sharing.overview_for_cases(log, {"activity": "Escalate"})["cases"] == 6


def test_agent_route(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-{1+i:02d}T0{j}:00:00Z" for i in range(8) for j, a in enumerate("AB" + ("C" if i < 5 else ""))]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/process-intelligence/'+log+'/overview-for-cases'
    body={"case_filter": {"activity": "C"}}
    assert client.post(url,json=body,headers=bearer).status_code in (403,404)
    sharing.set_share(log,'aggregates')
    r=client.post(url,json=body,headers=bearer)
    assert r.status_code==200 and r.json()['cases']==5 and r.json()['cases_in_log']==8, r.text
    assert client.post(url,json={"case_filter": {"activity": "C"}, "sla_hours": 2},headers=bearer).json()['kpis']['sla']['seconds']==7200
    assert client.post(url,json={"case_filter": {"activity": "A"}, "extra": 1},headers=bearer).status_code==422
    assert client.post(url,json={"case_filter": {"activity": "B"}, "sla_hours": -1},headers=bearer).status_code==422
    assert client.post(url,json={"case_filter": {"started_from": "2024-03-07T00:00:00Z"}},headers=bearer).status_code==422
    assert client.post(url,json=body).status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
