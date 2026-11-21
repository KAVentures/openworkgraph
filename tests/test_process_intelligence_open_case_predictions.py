"""Agents can read predictions for open cases: summary and backtest at the aggregates level, per-case
estimates with case IDs only at the cases level."""
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
    """40 completed cases Open > Check > Close over January and February (10 of them also Escalate before
    Close), then 4 open cases that have only Open > Check."""
    rows = ["case,activity,time"]
    for i in range(40):
        day = f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}"
        steps = ["Open", "Check"] + (["Escalate"] if i % 4 == 0 else []) + ["Close"]
        rows += [f"c{i},{a},{day}T{8 + 2 * j:02d}:00:00Z" for j, a in enumerate(steps)]
    for k in range(4):
        rows += [f"o{k},Open,2024-03-0{1 + k}T08:00:00Z", f"o{k},Check,2024-03-0{1 + k}T10:00:00Z"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="p", source_format="csv", mapping=MAPPING)["log_id"]


def test_remaining_time_summary_then_per_case():
    log = _log()
    with pytest.raises(sharing.NotShared):
        sharing.open_case_predictions(log, ["Close"])
    sharing.set_share(log, "aggregates")
    r = sharing.open_case_predictions(log, ["Close"], outcome="contains_activity", outcome_activity="Escalate")
    assert (r["completed_cases"], r["open_cases"], r["predicted_open_cases"]) == (40, 4, 4)
    assert r["case_ids_returned"] is False and "predictions" not in r and "o0" not in repr(r)
    assert "backtest" in r and r["format"] == sharing.FORMAT
    sharing.set_share(log, "cases")
    full = sharing.open_case_predictions(log, ["Close"], outcome="duration_over", outcome_hours=5, limit=2)
    assert full["case_ids_returned"] is True and len(full["predictions"]) == 2 and full["predictions_truncated"]
    first = full["predictions"][0]
    assert first["case_id"].startswith("o") and first["last_activity"] == "Check"
    assert first["median_remaining_seconds"] == 2 * 3600 and 0 < first["outcome_probability"] < 1


def test_next_activity_summary_then_per_case():
    log = _log()
    sharing.set_share(log, "aggregates")
    r = sharing.open_case_next_activity(log, ["Close"], history=1)
    assert r["open_cases"] == 4 and "predictions" not in r and r["case_ids_returned"] is False
    expected = {e["activity"]: e["expected_cases"] for e in r["expected_next_across_open_cases"]}
    assert expected["Close"] == 3.0 and expected["Escalate"] == 1.0
    sharing.set_share(log, "cases")
    full = sharing.open_case_next_activity(log, ["Close"], history=1)
    assert {p["case_id"] for p in full["predictions"]} == {"o0", "o1", "o2", "o3"}
    assert full["predictions"][0]["next"][0]["activity"] == "Close"


def test_validation():
    log = _log()
    sharing.set_share(log, "aggregates")
    for kwargs in ({"end_activities": []}, {"end_activities": "Close"}, {"end_activities": ["Close"], "outcome": "late"},
                   {"end_activities": ["Close"], "outcome": "duration_over"},
                   {"end_activities": ["Close"], "outcome": "contains_activity"}):
        with pytest.raises(EventLogWorkspaceError):
            sharing.open_case_predictions(log, **kwargs)
    with pytest.raises(EventLogWorkspaceError):
        sharing.open_case_next_activity(log, ["Close"], history=5)


def test_agent_routes(tmp_path):
    code = r'''
from io import BytesIO
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, event_log_workspace as workspace, event_log_agent_sharing as sharing
rows = ["case,activity,time"] + [f"c{i},{a},2024-01-{1+i:02d}T0{2*j}:00:00Z" for i in range(25) for j, a in enumerate(["A","B","C"])]
rows += ["o1,A,2024-02-01T00:00:00Z", "o1,B,2024-02-01T02:00:00Z"]
log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="q", source_format="csv",
    mapping={"case_column":"case","activity_column":"activity","timestamp_column":"time"})["log_id"]
with TestClient(app) as client:
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    base='/v1/process-intelligence/'+log
    assert client.get(base+'/open-case-predictions?end_activity=C',headers=bearer).status_code==404
    sharing.set_share(log,'aggregates')
    r=client.get(base+'/open-case-predictions?end_activity=C',headers=bearer)
    assert r.status_code==200 and r.headers['cache-control']=='no-store', r.text
    assert r.json()['open_cases']==1 and 'predictions' not in r.json()
    sharing.set_share(log,'cases')
    p=client.get(base+'/open-case-predictions?end_activity=C&outcome=duration_over&outcome_hours=3',headers=bearer).json()
    assert p['predictions'][0]['case_id']=='o1', p
    n=client.get(base+'/open-case-next-activity?end_activity=C&history=1',headers=bearer).json()
    assert n['predictions'][0]['next'][0]['activity']=='C', n
    assert client.get(base+'/open-case-predictions',headers=bearer).status_code==422
    assert client.get(base+'/open-case-predictions?end_activity=C&outcome=late',headers=bearer).status_code==422
    assert client.get(base+'/open-case-next-activity?end_activity=C').status_code in (401,403)
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
