"""Profile of the event attributes in a saved log."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_attribute_profile as attribute_profile
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "attribute_columns": ["region", "amount", "note", "ticket"]}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    """Ten cases. Region is case-level (6 north taking 1 h, 4 south taking 5 h); amount changes in
    south cases; note is mostly blank; ticket is unique per event."""
    rows, n = [], 0
    for i in range(10):
        south = i >= 6
        for k, activity in enumerate(["Receive", "Pay"]):
            n += 1
            amount = str(100 + 10 * i + (5 * k if south else 0))
            note = "urgent" if (i == 0 and k == 0) else ""
            rows.append([f"c{i}", activity, (START + timedelta(days=i, hours=k * (5 if south else 1))).isoformat(),
                         "south" if south else "north", amount, note, f"T{n:03d}"])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time", "region", "amount", "note", "ticket"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="attrs", source_format="csv", mapping=MAPPING)["log_id"]


def test_profile_of_case_and_event_attributes():
    log = _log()
    r = attribute_profile.profile(log)
    assert r["format"] == attribute_profile.FORMAT and r["cases"] == 10 and r["events"] == 20
    by = {a["attribute"]: a for a in r["attributes"]}
    region = by["region"]
    assert region["kind"] == "categorical" and region["level"] == "case" and region["case_coverage"] == 1.0
    assert [(v["value"], v["cases"], v["median_case_duration_seconds"]) for v in region["top_values"]] == [
        ("north", 6, 3600), ("south", 4, 5 * 3600)]
    amount = by["amount"]
    assert amount["kind"] == "numeric" and amount["level"] == "event" and amount["cases_where_value_changes"] == 4
    assert amount["numeric"]["min"] == 100 and amount["numeric"]["max"] == 195
    note = by["note"]
    assert note["events"] == 1 and note["case_coverage"] == 0.1 and note["event_coverage"] == 0.05
    assert by["ticket"]["unique_per_event"] is True and by["ticket"]["distinct_values"] == 20
    assert not r["attributes_truncated"]
    assert "not evidence that the value causes" in r["interpretation"]


def test_filter_top_values_and_errors():
    log = _log()
    r = attribute_profile.profile(log, {"cohort": {"started_from": "2024-03-10T00:00:00Z"}, "top_values": 1})
    assert r["cases"] == 4
    by = {a["attribute"]: a for a in r["attributes"]}
    assert len(by["amount"]["top_values"]) == 1 and "note" not in by
    for bad in ({"top_values": 0}, {"top_values": True}, {"x": 1}, {"cohort": {"nope": 1}},
                {"cohort": {"activity": "Nowhere"}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            attribute_profile.profile(log, bad)


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time,team"] + [f"c{i},A,2024-03-04T08:00:0{i}Z,{'x' if i else 'y'}" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time&attribute_column=team",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    path = f"/v1/event-logs/{imported.json()['log_id']}/attribute-profile"
    assert client.post(path, json={}, headers=bearer).status_code == 403
    found = client.post(path, json={}, headers=owner)
    assert found.status_code == 200, found.text
    assert [a["attribute"] for a in found.json()["attributes"]] == ["team"]
    assert client.post(path, json={"top_values": 99}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
