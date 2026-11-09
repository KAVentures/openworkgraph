"""Saving the cases that match a filter as a new event log."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_derive as derive
from server import event_log_workspace as workspace

START = datetime(2024, 3, 4, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who",
           "attribute_columns": ["region"]}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _csv_log():
    rows = []
    for i in range(10):
        path = ["Receive", "Escalate", "Pay"] if i % 3 == 0 else ["Receive", "Pay"]
        for k, activity in enumerate(path):
            rows.append([f"c{i}", activity, (START + timedelta(days=i, hours=k)).isoformat(), "ann",
                         "south" if i >= 6 else "north"])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time", "who", "region"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="all", source_format="csv", mapping=MAPPING)["log_id"]


def test_matching_cases_become_a_new_log_and_the_source_is_unchanged():
    log = _csv_log()
    before = workspace.summary(log)
    r = derive.derive(log, {"name": "escalated", "cohort": {"activity": "Escalate"}})
    assert r["format"] == derive.FORMAT and r["source_unchanged"] is True
    assert r["derived_from"] == {"log_id": log, "case_notion": "case", "cohort": {"activity": "Escalate"},
                                 "cases_matched": 4, "cases_in_source": 10}
    assert r["copied"] == {"events": 12, "objects": 4}
    new = r["log"]
    assert new["name"] == "escalated" and new["source_format"] == "csv" and new["events"] == 12
    assert "activity mapping" in r["not_copied"]
    summary = workspace.summary(new["log_id"])
    assert summary["cases"] == 4 and summary["events"] == 12
    # Every event of a matching case is kept, with its attributes.
    variants = workspace.variants(new["log_id"])
    assert [v["path"] for v in variants["variants"]] == [["Receive", "Escalate", "Pay"]]
    cases = dict(workspace.case_events(new["log_id"]))
    assert cases["c0"][0]["attributes"] == {"region": "north"} and cases["c9"][0]["attributes"] == {"region": "south"}
    assert workspace.summary(log) == before
    # The derived log behaves like any other: export and delete.
    out = StringIO()
    workspace.export_log(new["log_id"], "csv", out)
    assert out.getvalue().count("Escalate") == 4
    workspace.delete_log(new["log_id"])
    assert [entry["log_id"] for entry in workspace.list_logs()["logs"]] == [log]


def test_object_centric_logs_keep_linked_objects_and_their_history():
    objects = [{"id": f"o{i}", "type": "order",
                "attributes": [{"name": "status", "value": "new", "time": "2024-01-01T00:00:00Z"},
                               {"name": "status", "value": "paid", "time": "2024-01-05T00:00:00Z"}]} for i in range(4)]
    objects += [{"id": "i1", "type": "item"}, {"id": "i2", "type": "item"}]
    events = [{"id": f"e{i}", "type": "pay" if i % 2 else "place", "time": f"2024-01-0{1 + i}T09:00:00Z",
               "relationships": [{"objectId": f"o{i}", "qualifier": "order"},
                                 {"objectId": "i1" if i < 2 else "i2", "qualifier": "item"}]} for i in range(4)]
    ocel = {"objectTypes": [{"name": "order", "attributes": [{"name": "status", "type": "string"}]},
                            {"name": "item", "attributes": []}],
            "eventTypes": [], "objects": objects, "events": events}
    log = workspace.import_log(BytesIO(json.dumps(ocel).encode()), name="o", source_format="ocel")["log_id"]
    r = derive.derive(log, {"name": "paid orders", "case_type": "order", "cohort": {"activity": "pay"}})
    assert r["derived_from"]["cases_matched"] == 2 and r["copied"] == {"events": 2, "objects": 4}
    types = {t["object_type"]: t["objects"] for t in workspace.object_types(r["log"]["log_id"])["object_types"]}
    assert types == {"order": 2, "item": 2}
    out = StringIO()
    workspace.export_log(r["log"]["log_id"], "ocel", out)
    exported = json.loads(out.getvalue())
    statuses = [o for o in exported["objects"] if o["id"] == "o1"][0]["attributes"]
    assert sorted(a["value"] for a in statuses) == ["new", "paid"]


def test_invalid_requests():
    log = _csv_log()
    for bad in (None, {}, {"name": "x"}, {"name": "x", "cohort": {}}, {"name": "x", "cohort": {"label": "only"}},
                {"name": "", "cohort": {"activity": "Pay"}}, {"name": "a<b", "cohort": {"activity": "Pay"}},
                {"name": "x", "cohort": {"activity": "Nowhere"}}, {"name": "x", "cohort": {"nope": 1}},
                {"name": "x", "cohort": {"activity": "Pay"}, "extra": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            derive.derive(log, bad)
    with pytest.raises(workspace.EventLogWorkspaceError):
        derive.derive("log_" + "0" * 16, {"name": "x", "cohort": {"activity": "Pay"}})
    assert len(workspace.list_logs()["logs"]) == 1


def test_log_limit(monkeypatch):
    log = _csv_log()
    monkeypatch.setattr(workspace, "MAX_LOGS", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="delete an event log"):
        derive.derive(log, {"name": "x", "cohort": {"activity": "Pay"}})


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{'A' if i else 'B'},2024-03-04T08:00:0{i}Z" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    path = f"/v1/event-logs/{imported.json()['log_id']}/derive"
    body = {"name": "only A", "cohort": {"activity": "A"}}
    assert client.post(path, json=body, headers=bearer).status_code == 403
    made = client.post(path, json=body, headers=owner)
    assert made.status_code == 200, made.text
    assert made.json()["log"]["events"] == 2
    assert len(client.get("/v1/event-logs", headers=owner).json()["logs"]) == 2
    assert client.post(path, json={"name": "all", "cohort": {}}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
