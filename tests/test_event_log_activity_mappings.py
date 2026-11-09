"""Activity mappings (event abstraction) applied to every case-centric analysis."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_activity_mappings as mappings
from server import event_log_analytics as analytics
from server import event_log_workspace as workspace

START = datetime(2024, 3, 1, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    paths = {"a": ["Open form", "Type name", "Type address", "Submit", "Approve"],
             "b": ["Open form", "Type address", "Submit", "Reject"],
             "c": ["Open form", "Type name", "Submit", "Approve"]}
    for case, steps in paths.items():
        for k, step in enumerate(steps):
            w.writerow([case, step, (START + timedelta(hours=k)).isoformat()])
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="forms", source_format="csv", mapping=MAPPING)["log_id"]


ENTER = {"name": "Data entry", "groups": [{"label": "Enter data", "activities": ["Type name", "Type address"]}],
         "collapse_repeats": True}


def test_an_applied_mapping_reaches_every_analysis_and_is_reported():
    log = _log()
    before = {tuple(v["path"]) for v in workspace.variants(log)["variants"]}
    assert len(before) == 3
    created = mappings.create(log, ENTER)
    assert created["applied"] is False and created["mapped_activities"] == 2 and created["activities_not_in_log"] == []
    assert "activity_mapping" not in workspace.variants(log), "saving a mapping does not apply it"
    mappings.apply_mapping(log, created["mapping_id"])
    result = workspace.variants(log)
    paths = {tuple(v["path"]): v["cases"] for v in result["variants"]}
    assert paths == {("Open form", "Enter data", "Submit", "Approve"): 2, ("Open form", "Enter data", "Submit", "Reject"): 1}
    m = result["activity_mapping"]
    assert m["name"] == "Data entry" and m["events_relabeled"] == 4 and m["repeats_collapsed"] == 1
    assert m["original_events_unchanged"] is True
    kpis = analytics.kpis(log)
    assert kpis["activity_mapping"]["mapping_id"] == created["mapping_id"]
    # Removing the mapping restores the original view; the events were never changed.
    mappings.apply_mapping(log, None)
    assert {tuple(v["path"]) for v in workspace.variants(log)["variants"]} == before
    listed = mappings.listing(log)
    assert [x["applied"] for x in listed["mappings"]] == [False]
    assert {a["activity"] for a in listed["original_activities"]} >= {"Type name", "Type address"}


def test_without_collapsing_repeats_each_event_keeps_its_place():
    log = _log()
    keep = mappings.create(log, {**ENTER, "collapse_repeats": False, "name": "Keep"})
    mappings.apply_mapping(log, keep["mapping_id"])
    paths = {tuple(v["path"]) for v in workspace.variants(log)["variants"]}
    assert ("Open form", "Enter data", "Enter data", "Submit", "Approve") in paths


def test_validation_one_applied_at_a_time_delete_and_erase():
    log = _log()
    with pytest.raises(workspace.EventLogWorkspaceError, match="two groups"):
        mappings.create(log, {"name": "x", "groups": [{"label": "A", "activities": ["Submit"]},
                                                     {"label": "B", "activities": ["Submit"]}]})
    for bad in ({"name": "x", "groups": []}, {"name": "x", "groups": [{"label": "A", "activities": []}]},
                {"name": "x", "groups": [{"label": "A", "activities": ["Submit"]}], "extra": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            mappings.create(log, bad)
    odd = mappings.create(log, {"name": "Typo", "groups": [{"label": "A", "activities": ["Sbumit"]}]})
    assert odd["activities_not_in_log"] == ["Sbumit"]
    first, second = odd, mappings.create(log, ENTER)
    mappings.apply_mapping(log, first["mapping_id"])
    state = mappings.apply_mapping(log, second["mapping_id"])
    assert [m["applied"] for m in state["mappings"]] == [False, True]
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown activity mapping"):
        mappings.apply_mapping(log, "amap_nope")
    mappings.delete(log, second["mapping_id"])
    assert "activity_mapping" not in workspace.variants(log)
    workspace.delete_log(log)
    with pytest.raises(workspace.EventLogWorkspaceError):
        mappings.listing(log)


def test_routes_require_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},{a},2024-03-01T0{k}:00:00Z" for i in range(3) for k, a in enumerate(["A", "B1", "B2", "C"])]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    log = imported.json()["log_id"]
    base = f"/v1/event-logs/{log}/activity-mappings"
    body = {"name": "B steps", "groups": [{"label": "B", "activities": ["B1", "B2"]}], "collapse_repeats": True}
    assert client.post(base, json=body, headers=bearer).status_code == 403
    made = client.post(base, json=body, headers=owner)
    assert made.status_code == 200, made.text
    mid = made.json()["mapping_id"]
    assert client.post(base + "/applied", json={"mapping_id": mid}, headers=bearer).status_code == 403
    assert client.post(base + "/applied", json={"mapping_id": 5}, headers=owner).status_code == 422
    assert client.post(base + "/applied", json={"mapping_id": "amap_x"}, headers=owner).status_code == 404
    assert client.post(base + "/applied", json={"mapping_id": mid}, headers=owner).status_code == 200
    variants = client.get(f"/v1/event-logs/{log}/variants", headers=owner).json()
    assert [v["path"] for v in variants["variants"]] == [["A", "B", "C"]] and variants["activity_mapping"]["mapping_id"] == mid
    assert client.get(base, headers=bearer).status_code == 403
    assert client.post(base + "/applied", json={"mapping_id": None}, headers=owner).status_code == 200
    assert "activity_mapping" not in client.get(f"/v1/event-logs/{log}/variants", headers=owner).json()
    assert client.post(f"{base}/{mid}/delete", headers=owner).status_code == 200
    assert client.post(f"{base}/{mid}/delete", headers=owner).status_code == 404
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
