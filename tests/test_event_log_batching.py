"""Batch processing detected from a saved event log (R14)."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_batching as batching
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    """Invoices arrive through the day; Ana approves them in batches of 5 at 17:00 (sequential, 10 s apart).
    Posting is done one by one by Ben. A nightly job exports 5 at the same instant."""
    rows = []
    for day in range(2):
        base = START + timedelta(days=day)
        for i in range(5):
            case = f"d{day}i{i}"
            rows.append([case, "Receive", (base + timedelta(hours=i)).isoformat(), "clerk"])
            rows.append([case, "Approve", (base.replace(hour=17) + timedelta(seconds=10 * i)).isoformat(), "Ana"])
            rows.append([case, "Post", (base.replace(hour=18) + timedelta(minutes=20 * i)).isoformat(), "Ben"])
            rows.append([case, "Export", base.replace(hour=23).isoformat(), "job"])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time", "who"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="invoices", source_format="csv", mapping=MAPPING)["log_id"]


def test_sequential_and_simultaneous_batches_are_found_with_a_proposal():
    log = _log()
    r = batching.batching(log)
    by = {a["activity"]: a for a in r["activities"]}
    approve, export, post = by["Approve"], by["Export"], by["Post"]
    assert approve["batches"] == 2 and approve["batched_share"] == 1.0 and approve["sequential_batches"] == 2
    assert approve["batch_size"] == {"median": 5, "p90": 5, "max": 5} and approve["median_batch_span_seconds"] == 40
    assert export["simultaneous_batches"] == 2 and export["batched_events"] == 10
    assert post["batches"] == 0 and post["batched_events"] == 0 and post["simulation_batch_proposal"] is None
    # Approve waits since Receive: 17:00 minus 08:00..12:00, so batched cases waited hours.
    assert approve["median_wait_before_batched_seconds"] > 4 * 3600
    assert approve["simulation_batch_proposal"]["size"] == 5 and approve["simulation_batch_proposal"]["max_wait_minutes"] > 60
    assert r["resources_recorded"] is True and r["proposals_applied"] is False
    assert "not proof that work was deliberately batched" in r["interpretation"]


def test_gap_size_and_resource_settings():
    log = _log()
    tight = batching.batching(log, {"gap_seconds": 5})
    assert {a["activity"]: a["batches"] for a in tight["activities"]}["Approve"] == 0
    loose = batching.batching(log, {"gap_seconds": 1800, "min_batch_size": 5})
    assert {a["activity"]: a["batches"] for a in loose["activities"]}["Post"] == 2
    # Ignoring resources, Receive (one per hour) is still not batched with a one-minute gap.
    pooled = batching.batching(log, {"by_resource": False})
    assert {a["activity"]: a["batches"] for a in pooled["activities"]}["Receive"] == 0 and pooled["grouping"] == "activity only"
    filtered = batching.batching(log, {"cohort": {"activity": "Approve"}})
    assert filtered["events"] == 40
    for bad in ({"gap_seconds": -1}, {"min_batch_size": 1}, {"unknown": 1}, {"cohort": {"nope": 1}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            batching.batching(log, bad)


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time,who"] + [f"c{i},Approve,2024-03-04T17:00:0{i}Z,Ana" for i in range(4)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time&resource_column=who",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    path = f"/v1/event-logs/{imported.json()['log_id']}/batching"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    found = client.post(path, json={}, headers=owner)
    assert found.status_code == 200, found.text
    assert found.json()["activities"][0]["batches"] == 1
    assert client.post(path, json={"gap_seconds": -5}, headers=owner).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
