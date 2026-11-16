"""Folder feeds append new CSV exports from a local folder to a saved log."""
from __future__ import annotations

import os
import time
from io import BytesIO
from pathlib import Path

import pytest

from server import event_log_folder_feeds as feeds
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler

MAPPING = {"case_column": "Order", "activity_column": "Step", "timestamp_column": "When", "delimiter": ";",
           "timestamp_format": "%d/%m/%Y %H:%M", "naive_timestamp_offset": "+01:00"}
HEADER = "Order;Step;When\n"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(scheduler, "_path", lambda: tmp_path / "data" / "schedules.json")


def _log():
    text = HEADER + "o1;Create;01/03/2024 09:00\no1;Ship;02/03/2024 09:00\n"
    return workspace.import_log(BytesIO(text.encode()), name="orders", source_format="csv", mapping=MAPPING)["log_id"]


def _drop(folder: Path, name: str, text: str, age: float = 600):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def test_feed_appends_new_settled_files_once(tmp_path):
    log = _log()
    folder = tmp_path / "exports"
    folder.mkdir()
    with pytest.raises(workspace.EventLogWorkspaceError, match="confirmation"):
        feeds.create(log, {"folder": str(folder)})
    with pytest.raises(workspace.EventLogWorkspaceError, match="absolute"):
        feeds.create(log, {"folder": "exports", "confirm_folder_reads": True})
    with pytest.raises(workspace.EventLogWorkspaceError, match="pattern"):
        feeds.create(log, {"folder": str(folder), "pattern": "../*.csv", "confirm_folder_reads": True})
    feed = feeds.create(log, {"folder": str(folder), "pattern": "orders-*.csv", "confirm_folder_reads": True})
    assert feed["mapping"]["timestamp_format"] == "%d/%m/%Y %H:%M" and feed["mapping"]["delimiter"] == ";"
    with pytest.raises(workspace.EventLogWorkspaceError, match="already has a feed"):
        feeds.create(log, {"folder": str(folder), "pattern": "orders-*.csv", "confirm_folder_reads": True})

    # Overlapping export: o1/Ship is already in the log and skipped; o2 is new.
    _drop(folder, "orders-0301.csv", HEADER + "o1;Ship;02/03/2024 09:00\no2;Create;03/03/2024 10:00\n")
    _drop(folder, "orders-0302.csv", HEADER + "o2;Ship;04/03/2024 10:00\n", age=5)  # still being written
    _drop(folder, "notes.csv", HEADER + "x;Create;03/03/2024 10:00\n")  # does not match the pattern
    first = feeds.run(feed["feed_id"])
    assert (first["files_appended"], first["events_added"], first["files_still_being_written"]) == (1, 1, 1)
    assert first["files"][0]["duplicate_events_skipped"] == 1
    # Later the second file has settled; the first is not appended again, even renamed.
    later = time.time() + 120
    (folder / "orders-0301.csv").rename(folder / "orders-0301-copy.csv")
    second = feeds.run(feed["feed_id"], now=later)
    assert (second["files_appended"], second["events_added"], second["files_already_appended"]) == (1, 1, 1)
    assert workspace.summary(log)["events"] == 4
    # A broken file is recorded as failed and not retried with the same content.
    _drop(folder, "orders-0303.csv", "Wrong;Header\n1;2\n")
    third = feeds.run(feed["feed_id"], now=later)
    assert third["files_failed"] == 1 and "missing required columns" in third["files"][0]["error"]
    assert feeds.run(feed["feed_id"], now=later)["files_failed"] == 0
    listed = feeds.feeds(log)["feeds"][0]
    assert (listed["files_appended"], listed["files_failed"]) == (2, 1)
    assert [f["outcome"] for f in listed["recent_files"]].count("appended") == 2
    assert (folder / "orders-0303.csv").exists(), "files in the folder are never removed"


def test_feed_needs_a_csv_mapping_and_scheduler_runs_it(tmp_path):
    log = _log()
    folder = tmp_path / "exports"
    folder.mkdir()
    feed = feeds.create(log, {"folder": str(folder), "confirm_folder_reads": True})
    _drop(folder, "a.csv", HEADER + "o3;Create;05/03/2024 10:00\n")
    assert feeds.run_scheduled(feed["feed_id"]) == ("succeeded:1_files_appended", 0.0)
    assert feeds.run_scheduled(feed["feed_id"]) == ("succeeded:0_files_appended", 0.0)
    schedule = scheduler.create("event_log_folder_feed", feed["feed_id"], interval_minutes=60, confirm_scheduled_runs=True)
    assert schedule["kind"] == "event_log_folder_feed"
    with pytest.raises(scheduler.ScheduleError, match="unknown event log folder feed"):
        scheduler.create("event_log_folder_feed", "feed_" + "0" * 16, interval_minutes=60, confirm_scheduled_runs=True)
    # Deleting the log deletes its feeds, their records and the schedule.
    workspace.delete_log(log)
    assert scheduler.status()["schedules"] == []
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown feed"):
        feeds.get(feed["feed_id"])


def test_ocel_logs_have_no_mapping_to_reuse(tmp_path):
    doc = ('{"objectTypes":[{"name":"order","attributes":[]}],"eventTypes":[{"name":"A","attributes":[]}],'
           '"objects":[{"id":"o1","type":"order","attributes":[]}],"events":[{"id":"e1","type":"A",'
           '"time":"2024-01-01T00:00:00Z","attributes":[],"relationships":[{"objectId":"o1","qualifier":""}]}]}')
    log = workspace.import_log(BytesIO(doc.encode()), name="o", source_format="ocel")["log_id"]
    folder = tmp_path / "exports"
    folder.mkdir()
    with pytest.raises(workspace.EventLogWorkspaceError, match="no CSV import"):
        feeds.create(log, {"folder": str(folder), "confirm_folder_reads": True})


def test_routes(tmp_path):
    import json
    import subprocess
    import sys
    folder = tmp_path / "exports"
    folder.mkdir()
    _drop(folder, "a.csv", "case,activity,time\nc9,A,2024-03-05T08:00:00Z\n")
    code = r'''
import json, sys
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    store = {**owner, "X-OWG-Local-Storage-Confirmed": "true"}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content=b"case,activity,time\nc1,A,2024-03-04T08:00:00Z\n", headers=store).json()["log_id"]
    spec = {"folder": sys.argv[1], "confirm_folder_reads": True}
    assert client.post(f"/v1/event-logs/{log_id}/feeds", json=spec, headers=bearer).status_code == 403
    r = client.post(f"/v1/event-logs/{log_id}/feeds", json=spec, headers=owner)
    assert r.status_code == 200, r.text
    feed_id = r.json()["feed_id"]
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/run", headers=owner).status_code == 403
    r = client.post(f"/v1/event-logs/feeds/{feed_id}/run", headers=store)
    assert r.status_code == 200 and r.json()["events_added"] == 1, r.text
    assert client.get(f"/v1/event-logs/{log_id}/feeds", headers=owner).json()["feeds"][0]["files_appended"] == 1
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/delete", headers=owner).status_code == 200
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/run", headers=store).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code, str(folder)], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
