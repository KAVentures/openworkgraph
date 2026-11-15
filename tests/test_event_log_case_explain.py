"""Explaining one case against the rest of the log."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_case_explain as explain
from server import event_log_workspace as workspace

START = datetime(2024, 1, 1, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    """20 ordinary cases (Receive -1h- Check -2h- Approve -1h- Pay) and one slow case c99 that waits 30 h before
    Approve, checks twice and is escalated (an activity no other case has)."""
    rows = []
    for i in range(20):
        for activity, h in (("Receive", 0), ("Check", 1), ("Approve", 3), ("Pay", 4)):
            rows.append([f"c{i:02d}", activity, (START + timedelta(days=i, hours=h)).isoformat(), "ann"])
    for activity, h in (("Receive", 0), ("Check", 1), ("Check", 2), ("Escalate", 3), ("Approve", 33), ("Pay", 34)):
        rows.append(["c99", activity, (START + timedelta(days=30, hours=h)).isoformat(), "bob"])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time", "who"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="x", source_format="csv", mapping=MAPPING)["log_id"]


def test_slow_case_is_explained_step_by_step():
    log = _log()
    r = explain.explain(log, {"case_id": "c99"})
    assert r["format"] == explain.FORMAT and r["events"] == 6 and r["duration_seconds"] == 34 * 3600
    assert r["log_median_seconds"] == 4 * 3600 and r["slower_than_share_of_cases"] == 1.0
    assert r["path"] == ["Receive", "Check", "Check", "Escalate", "Approve", "Pay"]
    top = r["biggest_excess"][0]
    assert (top["from"], top["to"], top["gap_seconds"]) == ("Escalate", "Approve", 30 * 3600)
    # Only this case takes Escalate -> Approve, so there is no typical gap and all 30 h count.
    assert top["unusual_transition"] is True and top["typical_gap_seconds"] is None
    assert top["excess_seconds"] == 30 * 3600 and top["share_of_excess"] > 0.9
    gaps = {(g["from"], g["to"]): g for g in r["gaps"]}
    assert gaps[("Receive", "Check")]["excess_seconds"] == 0 and gaps[("Approve", "Pay")]["excess_seconds"] == 0
    assert r["repeated_activities"] == [{"activity": "Check", "times": 2}]
    assert r["uncommon_activities"] == ["Escalate"]
    assert r["steps"][3] == {"activity": "Escalate", "at": "2024-01-31T11:00:00Z", "resource": "bob"}
    assert "not a target" in r["interpretation"]


def test_ordinary_case_and_slowest_list():
    log = _log()
    ordinary = explain.explain(log, {"case_id": "c05"})
    assert ordinary["time_above_typical_seconds"] == 0 and ordinary["biggest_excess"] == []
    assert ordinary["repeated_activities"] == [] and ordinary["uncommon_activities"] == []
    slow = explain.slowest(log, {"limit": 3})
    assert slow["cases"][0]["case_id"] == "c99" and len(slow["cases"]) == 3
    assert explain.slowest(log, {"cohort": {"activity": "Escalate"}})["cases"] == [
        {"case_id": "c99", "duration_seconds": 34 * 3600, "events": 6, "started_at": "2024-01-31T08:00:00Z"}]
    for bad in ({}, {"case_id": ""}, {"case_id": "nope"}, {"case_id": "c99", "x": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            explain.explain(log, bad)
    for bad in ({"limit": 0}, {"cohort": {"nope": 1}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            explain.slowest(log, bad)


def test_routes_require_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-04T08:00:0{i}Z" for i in range(3)] + [f"c{i},B,2024-03-04T1{i}:00:00Z" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    base = f"/v1/event-logs/{imported.json()['log_id']}/cases"
    assert client.post(base + "/explain", json={"case_id": "c2"}, headers=bearer).status_code == 403
    assert client.post(base + "/slowest", json={}, headers=bearer).status_code == 403
    assert client.post(base + "/slowest", json={}, headers=owner).json()["cases"][0]["case_id"] == "c2"
    got = client.post(base + "/explain", json={"case_id": "c2"}, headers=owner)
    assert got.status_code == 200, got.text
    assert got.json()["path"] == ["A", "B"]
    assert client.post(base + "/explain", json={"case_id": "zz"}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
