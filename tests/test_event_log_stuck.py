"""Stuck open cases: idle far longer than usual after their latest activity."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_stuck as stuck
from server import event_log_workspace as workspace

START = datetime(2024, 3, 1, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    rows = []
    # 30 finished cases: Check follows Receive after 1-3 hours, Close follows Check after 2 hours.
    for i in range(30):
        t = START + timedelta(days=i)
        rows += [(f"c{i}", "Receive", t), (f"c{i}", "Check", t + timedelta(hours=1 + i % 3)),
                 (f"c{i}", "Close", t + timedelta(hours=3 + i % 3))]
    end = START + timedelta(days=40)
    rows += [("late", "Receive", end - timedelta(hours=30)),    # idle 30 h after Receive: stuck
             ("fresh", "Receive", end - timedelta(hours=1)),    # idle 1 h: fine
             ("rare", "Escalate", end - timedelta(days=5)),     # no history after Escalate: not judged
             ("last", "Receive", end)]                          # defines the end of the log
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    w.writerows((c, a, t.isoformat()) for c, a, t in rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]


def test_stuck_cases_against_the_usual_wait():
    r = stuck.stuck_cases(_log(), {"end_activities": ["Close"]})
    assert r["open_cases"] == 4 and r["not_judged_open_cases"] == 1 and r["judged_open_cases"] == 3
    assert r["stuck_cases"] == 1
    case = r["cases"][0]
    assert case["case_id"] == "late" and case["latest_activity"] == "Receive" and case["idle_seconds"] == 30 * 3600
    assert case["threshold_seconds"] <= 3 * 3600 and case["times_usual_threshold"] > 9
    receive = next(a for a in r["by_activity"] if a["activity"] == "Receive")
    assert receive["open_cases"] == 3 and receive["stuck_cases"] == 1 and receive["judged"] is True
    escalate = next(a for a in r["by_activity"] if a["activity"] == "Escalate")
    assert escalate["judged"] is False and escalate["observed_waits"] == 0
    assert "not a finding that anyone did anything wrong" in r["interpretation"]


def test_settings_and_validation():
    log = _log()
    loose = stuck.stuck_cases(log, {"end_activities": ["Close"], "min_support": 5000})
    assert loose["judged_open_cases"] == 0 and loose["stuck_cases"] == 0
    # Without end activities, finished cases ending at Close (never followed) are not open.
    noend = stuck.stuck_cases(log)
    assert {c["case_id"] for c in noend["cases"]} == {"late"} and noend["open_cases"] == 3
    for bad in ({"percentile": 0.2}, {"end_activities": [" "]}, {"unknown": 1}, {"limit": 0}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            stuck.stuck_cases(log, bad)


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-{1+i:02d}T0{k}:00:00Z" for i in range(6) for k, a in enumerate(["A", "B"])]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    path = f"/v1/event-logs/{imported.json()['log_id']}/stuck-cases"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    r = client.post(path, json={"end_activities": ["B"], "min_support": 5}, headers=owner)
    assert r.status_code == 200 and r.json()["open_cases"] == 0, r.text
    assert client.post(path, json={"percentile": 2}, headers=owner).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
