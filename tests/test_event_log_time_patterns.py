"""When work happens: weekday x hour grid in a chosen time zone."""
from __future__ import annotations

import csv
from io import BytesIO, StringIO

import pytest

from server import event_log_time_patterns as patterns
from server import event_log_workspace as workspace

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    rows = [
        ("a", "Receive", "2024-03-04T08:00:00Z"),  # Monday 09:00 Stockholm (CET, UTC+1)
        ("a", "Approve", "2024-03-04T16:30:00Z"),  # Monday 17:30
        ("b", "Receive", "2024-04-01T08:00:00Z"),  # Monday 10:00 Stockholm (CEST, UTC+2)
        ("b", "Approve", "2024-04-06T21:00:00Z"),  # Saturday 23:00: outside working hours
        ("c", "Receive", "2024-03-10T23:30:00Z"),  # Monday 00:30 Stockholm, Sunday in UTC
    ]
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]


def test_local_grid_with_daylight_saving_and_working_hours():
    log = _log()
    r = patterns.time_patterns(log, {"time_zone": "Europe/Stockholm"})
    ev, ar = r["events"], r["case_arrivals"]
    assert ev[0][9] == 1 and ev[0][17] == 1 and ev[0][10] == 1 and ev[5][23] == 1 and ev[0][0] == 1
    assert r["total_events"] == 5 and r["total_case_arrivals"] == 3
    assert ar[0][9] == 1 and ar[0][10] == 1 and ar[0][0] == 1
    assert r["outside_working_hours_share"] == 0.4  # Saturday 23:00 and Monday 00:30
    utc = patterns.time_patterns(log)
    assert utc["events"][6][23] == 1 and utc["events"][0][8] == 2, "in UTC, case c starts on Sunday"
    only = patterns.time_patterns(log, {"time_zone": "Europe/Stockholm", "activity": "Approve"})
    assert only["total_events"] == 2 and only["total_case_arrivals"] == 3
    weekend = patterns.time_patterns(log, {"time_zone": "Europe/Stockholm", "work_days": [1, 2, 3, 4, 5, 6, 7],
                                           "work_start_hour": 0, "work_end_hour": 24})
    assert weekend["outside_working_hours_share"] == 0
    assert "not proof of when people worked" in r["interpretation"]


def test_validation():
    log = _log()
    for bad in ({"time_zone": "Mars/Base"}, {"work_start_hour": 18, "work_end_hour": 8}, {"work_days": [0]},
                {"work_days": [1, 1]}, {"unknown": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            patterns.time_patterns(log, bad)


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time", "a,Receive,2024-03-04T08:00:00Z", "a,Close,2024-03-04T09:00:00Z"]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    path = f"/v1/event-logs/{imported.json()['log_id']}/time-patterns"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    r = client.post(path, json={"time_zone": "Europe/Stockholm"}, headers=owner)
    assert r.status_code == 200 and r.json()["events"][0][9] == 1, r.text
    assert client.post(path, json={"time_zone": "Mars/Base"}, headers=owner).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
