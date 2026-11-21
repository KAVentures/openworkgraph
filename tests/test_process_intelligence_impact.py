"""Agents can measure what changed after an intervention, without small periods describing single cases."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
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
    """60 cases, one every 12 hours from 2024-01-01: Open > Close, 4 h before 2024-01-16, 2 h from then on."""
    rows = ["case,activity,time,region"]
    start = datetime(2024, 1, 1, 8)
    for i in range(60):
        t0 = start + timedelta(hours=12 * i)
        hours = 4 if t0 < datetime(2024, 1, 16) else 2
        stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        rows += [f"c{i},Open,{stamp(t0)},{'EU' if i % 2 else 'US'}", f"c{i},Close,{stamp(t0 + timedelta(hours=hours))},"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="i", source_format="csv", mapping=MAPPING)["log_id"]


def test_before_after_change():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.impact(log, {"intervention_at": "2024-01-16T00:00:00Z"})
    sharing.set_share(log, "aggregates")
    r = sharing.impact(log, {"intervention_at": "2024-01-16T00:00:00Z"})
    base, follow = r["groups"]["all"]["baseline"], r["groups"]["all"]["follow_up"]
    assert (base["cases"], follow["cases"]) == (30, 30) and base["median"] == 4 * 3600 and follow["median"] == 2 * 3600
    assert r["change"]["estimated"] and r["change"]["median_change"] == -2 * 3600
    assert r["format"] == sharing.FORMAT and r["withheld"]["period_summaries"] == 0 and "log" not in r
    sla = sharing.impact(log, {"intervention_at": "2024-01-16T00:00:00Z", "metric": "sla_breach", "sla_hours": 3})
    assert sla["groups"]["all"]["baseline"]["mean"] == 1.0 and sla["groups"]["all"]["follow_up"]["mean"] == 0.0


def test_small_periods_show_only_counts():
    log = _log()
    sharing.set_share(log, "aggregates")
    # Only the last three cases start on or after this date.
    late = sharing.impact(log, {"intervention_at": "2024-01-29T12:00:00Z"})
    follow = late["groups"]["all"]["follow_up"]
    assert follow == {"cases": 3, "mean": None, "median": None} and late["withheld"]["period_summaries"] == 1
    assert late["change"]["estimated"] is False
    for bad in ({}, {"intervention_at": "2024-01-16T00:00:00Z", "rounds": 2000},
                {"intervention_at": "2024-01-16T00:00:00Z", "metric": "sla_breach", "sla_hours": -1}, "x"):
        with pytest.raises(EventLogWorkspaceError):
            sharing.impact(log, bad)


def test_agent_route(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_agent_sharing as sharing
from tests.test_process_intelligence_impact import _log
from server import event_log_workspace as workspace
log = _log()
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/process-intelligence/'+log+'/impact'
    body={"intervention_at":"2024-01-16T00:00:00Z","case_type":"case"}
    assert client.post(url,json=body,headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    r=client.post(url,json=body,headers=bearer)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert r.json()['change']['median_change']==-7200
    assert client.post(url,json={"intervention_at":"2024-01-16T00:00:00Z","seed":1},headers=bearer).status_code==422
    assert client.post(url,json=body).status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
