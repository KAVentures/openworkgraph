"""Appending newer exports to a saved event log (roadmap #326 F5)."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}
FIRST = b"""case,activity,time,who
1,Receive,2024-01-01T08:00:00Z,ann
1,Check,2024-01-01T09:00:00Z,bob
2,Receive,2024-01-02T08:00:00Z,ann
"""
# Overlapping newer export: two known events, one finished case, one new case.
SECOND = b"""case,activity,time,who
1,Check,2024-01-01T09:00:00Z,bob
2,Receive,2024-01-02T08:00:00Z,ann
1,Close,2024-01-01T10:00:00Z,bob
3,Receive,2024-01-03T08:00:00Z,ann
3,Receive,not a time,ann
"""


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    return workspace.import_log(BytesIO(FIRST), name="append", source_format="csv", mapping=MAPPING)["log_id"]


def test_overlapping_exports_add_only_new_events_and_cases():
    log = _log()
    result = workspace.append_log(log, BytesIO(SECOND), source_format="csv", mapping=MAPPING)
    assert (result["added_events"], result["duplicate_events_skipped"], result["rejected_events"]) == (2, 2, 1)
    assert result["added_objects"] == 1 and result["log"]["events"] == 5 and result["log"]["objects"] == 3
    variants = {tuple(v["path"]): v["cases"] for v in workspace.variants(log)["variants"]}
    assert variants == {("Receive", "Check", "Close"): 1, ("Receive",): 2}
    again = workspace.append_log(log, BytesIO(SECOND), source_format="csv", mapping=MAPPING)
    assert again["added_events"] == 0 and again["duplicate_events_skipped"] == 4 and again["batch"] == 2
    history = workspace.append_history(log)["appends"]
    assert [(h["batch"], h["added_events"], h["duplicate_events_skipped"]) for h in history] == [(1, 2, 2), (2, 0, 4)]


def test_appended_event_ids_never_collide_and_signals_see_new_cases():
    from server import event_log_signals as signals
    log = _log()
    signals.add_rule(log, {"name": "Not closed", "when": {"kind": "cases", "condition": "lacks_activity", "activity": "Close"}})
    assert signals.evaluate(log)["signals"][0]["matching_cases"] == 2
    workspace.append_log(log, BytesIO(SECOND), source_format="csv", mapping=MAPPING)
    after = signals.evaluate(log)["signals"][0]
    assert after["matching_cases"] == 2 and after["new_cases_since_last_evaluation"] == 1  # case 1 closed, case 3 new
    conn = workspace._connect()
    try:
        ids = [r[0] for r in conn.execute("SELECT event_id FROM events WHERE log_id = ? ORDER BY event_id", [log]).fetchall()]
        assert len(ids) == len(set(ids)) and sum(i.startswith("a1:") for i in ids) == 2
        assert conn.execute("SELECT count(*) FROM events WHERE log_id LIKE 'stg_%'").fetchone()[0] == 0
    finally:
        conn.close()


def test_object_centric_append_keeps_existing_attributes_and_refuses_type_conflicts():
    base = {"objectTypes": [{"name": "order", "attributes": []}, {"name": "item", "attributes": []}],
            "eventTypes": [{"name": "Place", "attributes": []}, {"name": "Ship", "attributes": []}],
            "objects": [{"id": "o1", "type": "order", "attributes": [{"name": "region", "time": "1970-01-01T00:00:00Z", "value": "EU"}]}],
            "events": [{"id": "p1", "type": "Place", "time": "2024-01-01T00:00:00Z", "attributes": [],
                        "relationships": [{"objectId": "o1", "qualifier": ""}]}]}
    log = workspace.import_log(BytesIO(json.dumps(base).encode()), name="ocel", source_format="ocel")["log_id"]
    newer = json.loads(json.dumps(base))
    newer["objects"][0]["attributes"][0]["value"] = "US"
    newer["events"].append({"id": "s1", "type": "Ship", "time": "2024-01-02T00:00:00Z", "attributes": [],
                            "relationships": [{"objectId": "o1", "qualifier": ""}]})
    result = workspace.append_log(log, BytesIO(json.dumps(newer).encode()), source_format="ocel")
    assert result["added_events"] == 1 and result["duplicate_events_skipped"] == 1 and result["added_objects"] == 0
    conn = workspace._connect()
    try:
        assert "EU" in conn.execute("SELECT attributes FROM objects WHERE log_id = ? AND object_id = 'o1'", [log]).fetchone()[0]
    finally:
        conn.close()
    clash = json.loads(json.dumps(newer))
    clash["objects"][0]["type"] = "item"
    for event in clash["events"]:
        event["time"] = "2024-02-01T00:00:00Z"
    before = workspace.summary(log)["events"]
    with pytest.raises(workspace.EventLogWorkspaceError, match="conflict"):
        workspace.append_log(log, BytesIO(json.dumps(clash).encode()), source_format="ocel")
    assert workspace.summary(log)["events"] == before and len(workspace.append_history(log)["appends"]) == 1


@pytest.mark.parametrize("payload,fmt,message", [
    (b"case,activity,time,who\n1,A,not a time,ann\n", "csv", "no events"),
    (b"<log", "xes", ""),
    (FIRST, "pdf", "format"),
])
def test_bad_appends_leave_the_log_unchanged(payload, fmt, message):
    log = _log()
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        workspace.append_log(log, BytesIO(payload), source_format=fmt, mapping=MAPPING)
    assert workspace.summary(log)["events"] == 3 and workspace.append_history(log)["appends"] == []
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown event log"):
        workspace.append_log("log_" + "0" * 16, BytesIO(FIRST), source_format="csv", mapping=MAPPING)


def test_append_history_is_deleted_with_the_log():
    log = _log()
    workspace.append_log(log, BytesIO(SECOND), source_format="csv", mapping=MAPPING)
    workspace.delete_log(log)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM log_appends").fetchone()[0] == 0
    finally:
        conn.close()


def test_real_http_append_requires_session_and_storage_confirmation(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
first = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n"
second = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-02T00:00:00Z\n"
query = "format=csv&case_column=case&activity_column=activity&timestamp_column=time"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    stored = {**headers, "x-owg-local-storage-confirmed": "true"}
    made = client.post("/v1/event-logs/import?name=x&" + query, content=first, headers=stored)
    path = "/v1/event-logs/" + made.json()["log_id"]
    assert client.post(path + "/append?" + query, content=second, headers=headers).status_code == 403
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token(), "x-owg-local-storage-confirmed": "true"}
    assert client.post(path + "/append?" + query, content=second, headers=bearer).status_code in (401, 403)
    result = client.post(path + "/append?" + query, content=second, headers=stored)
    assert result.status_code == 200, result.text
    assert result.json()["added_events"] == 1 and result.json()["duplicate_events_skipped"] == 1
    assert client.get(path + "/appends", headers=headers).json()["appends"][0]["batch"] == 1
    bad = client.post(path + "/append?" + query, content=b"case,activity,time\n", headers=stored)
    assert bad.status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_appending_a_million_overlapping_events_stays_interactive():
    import time
    from datetime import datetime, timedelta, timezone

    def export(first, cases):
        rows = ["case,activity,time,who"]
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows += [f"c{c},S{s},{(base + timedelta(seconds=c * 10 + s)).isoformat()},r{c % 50}"
                 for c in range(first, first + cases) for s in range(5)]
        return ("\n".join(rows) + "\n").encode()
    log = workspace.import_log(BytesIO(export(0, 200_000)), name="big", source_format="csv", mapping=MAPPING)["log_id"]
    started = time.perf_counter()
    result = workspace.append_log(log, BytesIO(export(100_000, 200_000)), source_format="csv", mapping=MAPPING)
    assert time.perf_counter() - started < 60
    assert (result["added_events"], result["duplicate_events_skipped"]) == (500_000, 500_000)
