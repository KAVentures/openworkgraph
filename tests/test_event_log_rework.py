"""Rework and loop analysis."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_rework as rework
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    paths = {}
    for i in range(10):
        paths[f"ok{i}"] = (["Receive", "Check", "Approve"], 1)
    for i in range(6):
        paths[f"fix{i}"] = (["Receive", "Check", "Correct", "Check", "Approve"], 5)  # short loop on Check
    for i in range(4):
        paths[f"dup{i}"] = (["Receive", "Receive", "Check", "Approve"], 2)       # self-loop on Receive
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    for n, (case, (steps, hours)) in enumerate(paths.items()):
        for k, step in enumerate(steps):
            w.writerow([case, step, (START + timedelta(days=n, hours=k * hours)).isoformat()])
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="t", source_format="csv", mapping=MAPPING)["log_id"]


def test_repeats_loop_patterns_and_comparison():
    r = rework.rework(_log())
    assert r["cases"] == 20 and r["cases_with_rework"] == 10 and r["rework_rate"] == 0.5
    by = {a["activity"]: a for a in r["activities"]}
    assert by["Check"]["cases_repeating"] == 6 and by["Check"]["extra_occurrences"] == 6
    assert by["Receive"]["cases_repeating"] == 4 and by["Receive"]["repeat_share_of_cases_with_activity"] == 0.2
    assert "Approve" not in by
    assert r["loop_kinds"] == {"self_loop": 4, "short_loop": 6, "longer_loop": 0}
    pats = {tuple(p["pattern"]): p["cases"] for p in r["loop_patterns"]}
    assert pats == {("Check", "Correct", "Check"): 6, ("Receive", "Receive"): 4}
    c = r["comparison"]
    assert c["cases_with_rework"] == 10 and c["median_throughput_without_rework_seconds"] == 2 * 3600
    assert c["median_difference_seconds"] > 0 and c["median_difference_ci95_seconds"][0] > 0
    assert c["mean_events_with_rework"] == 4.6 and c["mean_events_without_rework"] == 3
    assert "does not show that the rework caused" in r["interpretation"]


def test_limits_cohort_and_validation():
    log = _log()
    assert len(rework.rework(log, {"limit": 1})["activities"]) == 1
    filtered = rework.rework(log, {"cohort": {"activity": "Correct"}})
    assert filtered["cases"] == 6 and filtered["comparison"] is None
    for bad in ({"limit": 0}, {"unknown": 1}, {"cohort": {"nope": 1}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            rework.rework(log, bad)


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-0{1+i}T0{k}:00:00Z" for i in range(3) for k, a in enumerate(["A", "B", "A"])]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    path = f"/v1/event-logs/{imported.json()['log_id']}/rework"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    r = client.post(path, json={}, headers=owner)
    assert r.status_code == 200 and r.json()["loop_patterns"] == [{"pattern": ["A", "B", "A"], "cases": 3}], r.text
    assert client.post(path, json={"limit": 0}, headers=owner).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
