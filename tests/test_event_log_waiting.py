"""Processing and waiting time from lifecycle or end-time data (roadmap #326 H1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_waiting as waiting
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _at(day, hours):
    return (START + timedelta(days=day, hours=hours)).isoformat()


def _import(header, rows):
    text = "\n".join([",".join(header)] + [",".join(map(str, r)) for r in rows]) + "\n"
    return workspace.import_log(BytesIO(text.encode()), name="w", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": header[3:]})["log_id"]


def _lifecycle_log():
    rows = []
    for c in range(10):   # Receive 0–1 h, wait 2 h, Review 3–5 h, Close 5–5.5 h
        for activity, start, end in (("Receive", 0, 1), ("Review", 3, 5), ("Close", 5, 5.5)):
            rows += [(f"c{c}", activity, _at(c, start), "start"), (f"c{c}", activity, _at(c, end), "complete")]
    rows += [("c10", "Receive", _at(10, 0), "complete"),          # no start recorded
             ("c10", "Review", _at(10, 1), "start"), ("c10", "Review", _at(10, 2), "complete"),
             ("c11", "Receive", _at(11, 0), "schedule"),          # ignored lifecycle value
             ("c11", "Receive", _at(11, 0), "start"), ("c11", "Receive", _at(11, 1), "complete"),
             ("c11", "Review", _at(11, 2), "start")]              # still running
    return _import(["case", "activity", "time", "lifecycle:transition"], rows)


def _by(result):
    return {a["activity"]: a for a in result["activities"]}


def test_lifecycle_pairs_split_processing_from_waiting():
    result = waiting.waiting_time(_lifecycle_log())
    assert result["available"] and result["mode_used"] == "lifecycle"
    acts = _by(result)
    assert acts["Receive"]["processing"]["median_seconds"] == 3600
    assert acts["Review"]["processing"]["median_seconds"] == 7200
    assert acts["Review"]["waiting_before"]["median_seconds"] == 7200
    assert acts["Close"]["waiting_before"]["median_seconds"] == 0
    assert result["activities"][0]["activity"] == "Review"   # ranked by total waiting
    hot = result["waiting_hotspots"][0]
    assert (hot["from"], hot["to"]) == ("Receive", "Review")
    i = result["instances"]
    assert i["complete_without_start"] == 1 and i["unmatched_starts"] == 1 and i["ignored_lifecycle_events"] == 1
    assert acts["Receive"]["instances_with_processing_time"] == 11   # c10's complete-only instant is excluded
    assert result["flow_efficiency"]["median"] == pytest.approx(3.5 / 5.5)


def test_overlapping_work_is_not_waiting_and_counts_once_as_busy():
    log = _import(["case", "activity", "time", "lifecycle:transition"], [
        ("x", "A", _at(0, 0), "start"), ("x", "B", _at(0, 1), "start"),
        ("x", "A", _at(0, 2), "complete"), ("x", "B", _at(0, 3), "complete")])
    result = waiting.waiting_time(log)
    assert _by(result)["B"]["waiting_before"]["median_seconds"] == 0
    assert result["flow_efficiency"]["median"] == 1
    assert result["flow_efficiency"]["busy_seconds"] == 3 * 3600


def test_end_time_attribute_with_offsets_and_bad_values():
    log = _import(["case", "activity", "time", "finished"], [
        ("a", "Receive", _at(0, 0), "2024-01-01T02:00:00+01:00"),   # 1 h after start in UTC
        ("a", "Review", _at(0, 4), _at(0, 6)),
        ("b", "Receive", _at(1, 0), "2024-01-02 01:00:00"),         # no offset: not trusted
        ("b", "Review", _at(1, 4), _at(1, 3))])                       # ends before it starts
    auto = waiting.waiting_time(log)
    assert auto["available"] is False and "finished" in auto["attribute_names"]
    result = waiting.waiting_time(log, end_attribute="finished")
    assert result["mode_used"] == "end_attribute"
    acts = _by(result)
    assert acts["Receive"]["processing"]["median_seconds"] == 3600
    # a waits 3 h after Receive ends; b's Receive has no trusted end, so it is an instant and b waits 4 h.
    assert acts["Review"]["waiting_before"]["median_seconds"] == 3.5 * 3600
    assert result["instances"]["without_lifecycle_or_end"] == 1 and result["instances"]["end_before_start"] == 1


def test_settings_validation_and_cohorts():
    log = _lifecycle_log()
    for bad in ({"mode": "fast"}, {"mode": "end_attribute"}, {"end_attribute": 'x"y'}, {"lifecycle_attribute": ""}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            waiting.waiting_time(log, **bad)
    only = waiting.waiting_time(log, cohort={"activity": "Close"})
    assert set(_by(only)) == {"Receive", "Review", "Close"} and _by(only)["Close"]["cases"] == 10


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = (b"case,activity,time,lifecycle:transition\n1,A,2024-01-01T00:00:00Z,start\n1,A,2024-01-01T01:00:00Z,complete\n"
       b"1,B,2024-01-01T03:00:00Z,start\n1,B,2024-01-01T04:00:00Z,complete\n")
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time&attribute_column=lifecycle:transition", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/waiting-time"
    assert client.get(path, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    result = client.get(path, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert result.json()["waiting_hotspots"][0]["median_waiting_seconds"] == 7200
    assert client.get(path + "?mode=nope", headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_lifecycle_events_within_budget():
    import time
    rows = ["case,activity,time,lifecycle:transition"]
    for c in range(100_000):
        for s in range(5):
            begin = START + timedelta(minutes=c * 3 + s * 10)
            rows.append(f"c{c},S{s},{begin.isoformat()},start")
            rows.append(f"c{c},S{s},{(begin + timedelta(minutes=4)).isoformat()},complete")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["lifecycle:transition"]})["log_id"]
    started = time.perf_counter()
    result = waiting.waiting_time(log)
    assert time.perf_counter() - started < 30
    assert _by(result)["S1"]["waiting_before"]["median_seconds"] == 360


def test_csv_attribute_columns_are_kept_and_bounded():
    header = ["case", "activity", "time", "region", "note"]
    rows = [("a", "Receive", _at(0, 0), "north", "x" * 600), ("b", "Receive", _at(1, 0), "south", "")]
    log = _import(header, rows)
    conn = workspace._connect()
    try:
        stored = [r[0] for r in conn.execute(
            "SELECT attributes FROM events WHERE log_id = ? ORDER BY event_id", [log]).fetchall()]
    finally:
        conn.close()
    import json
    first = json.loads(stored[0])
    assert first["region"] == "north" and len(first["note"]) == 500 and "case" not in first
    text = "case,activity,time\nc,A," + _at(0, 0) + "\n"
    for bad in (["missing"], [f"c{i}" for i in range(33)]):
        with pytest.raises(workspace.EventLogWorkspaceError):
            workspace.import_log(BytesIO(text.encode()), name="w", source_format="csv", mapping={
                "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
                "attribute_columns": bad})
    plain = workspace.import_log(BytesIO(text.encode()), name="w", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})
    assert plain["events"] == 1
