"""Saved reusable case-filter sets and filtered case drill-down (#370 M5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_cases as cases
from server import event_log_filter_sets as filter_sets
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
FIX = {"follows": [{"from": "Check", "to": "Fix", "mode": "direct"}]}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _csv(paths, day=0):
    rows = ["case,activity,time"]
    for n, (case, path) in enumerate(paths.items()):
        rows += [f"{case},{a},{(START + timedelta(days=day + n, hours=k)).isoformat()}" for k, a in enumerate(path)]
    return BytesIO(("\n".join(rows) + "\n").encode())


def _log():
    paths = {f"c{i}": ["Receive", "Check", *(["Fix", "Check"] if i % 3 == 0 else []), "Close"] for i in range(9)}
    return workspace.import_log(_csv(paths), name="s", source_format="csv", mapping=MAPPING)["log_id"]


def test_save_list_apply_and_delete_a_filter_set():
    log = _log()
    saved = filter_sets.save(log, {"name": " Rework ", "cohort": FIX})
    assert saved["name"] == "Rework" and saved["set_id"].startswith("fs_")
    listed = filter_sets.list_sets(log)
    assert [s["name"] for s in listed["filter_sets"]] == ["Rework"]
    entry = listed["filter_sets"][0]
    assert entry["cohort"] == {"follows": [{"from": "Check", "to": "Fix", "mode": "direct", "negate": False}]}
    assert entry["activities_not_in_log"] == [] and entry["log_appended_since_saved"] is False
    # Applying the stored cohort selects the same cases in a view and in the case drill-down.
    assert workspace.summary(log, cohort=entry["cohort"])["cases"] == 3
    page = cases.cases(log, {"cohort": entry["cohort"], "limit": 100})
    assert page["selected_cases"] == 3 and {c["case_id"] for c in page["cases"]} == {"c0", "c3", "c6"}
    assert filter_sets.delete(log, saved["set_id"])["deleted"] is True
    assert filter_sets.list_sets(log)["filter_sets"] == []
    with pytest.raises(workspace.EventLogWorkspaceError):
        filter_sets.delete(log, saved["set_id"])


def test_filter_sets_are_validated():
    log = _log()
    filter_sets.save(log, {"name": "Rework", "cohort": FIX})
    for bad in ({"name": "", "cohort": FIX}, {"name": "x" * 121, "cohort": FIX}, {"name": "rework", "cohort": FIX},
                {"name": "Empty", "cohort": {}}, {"name": "Label only", "cohort": {"label": "x"}},
                {"name": "Bad", "cohort": {"follows": [{"from": "A"}]}}, {"name": "Extra", "cohort": FIX, "x": 1},
                {"name": "Unknown key", "cohort": {"stage": "x"}}, "not an object"):
        with pytest.raises(workspace.EventLogWorkspaceError):
            filter_sets.save(log, bad)
    for n in range(filter_sets.MAX_SETS - 1):
        filter_sets.save(log, {"name": f"set {n}", "cohort": FIX})
    with pytest.raises(workspace.EventLogWorkspaceError, match="at most"):
        filter_sets.save(log, {"name": "one more", "cohort": FIX})


def test_stale_sets_are_disclosed_and_deleted_with_the_log():
    log = _log()
    filter_sets.save(log, {"name": "Missing", "cohort": {"follows": [{"from": "Check", "to": "Escalate"}],
                                                           "started_from": "2024-01-01T00:00:00Z"}})
    workspace.append_log(log, _csv({"n1": ["Receive", "Check", "Fix", "Close"]}, day=30), source_format="csv",
                         mapping=MAPPING)
    entry = filter_sets.list_sets(log)["filter_sets"][0]
    assert entry["activities_not_in_log"] == ["Escalate"] and entry["log_appended_since_saved"] is True
    assert entry["cohort"]["started_from"] == "2024-01-01T00:00:00Z"
    workspace.delete_log(log)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM filter_sets").fetchone()[0] == 0
    finally:
        conn.close()


def test_real_http_routes_require_a_dashboard_session(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-01T01:00:00Z\n2,B,2024-01-01T00:00:00Z\n2,A,2024-01-01T01:00:00Z\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    base = "/v1/event-logs/" + made.json()["log_id"]
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    body = {"name": "A then B", "cohort": {"follows": [{"from": "A", "to": "B", "mode": "direct"}]}}
    assert client.post(base + "/filter-sets", json=body, headers=bearer).status_code in (401, 403)
    assert client.get(base + "/filter-sets", headers=bearer).status_code in (401, 403)
    saved = client.post(base + "/filter-sets", json=body, headers=headers)
    assert saved.status_code == 200 and saved.headers["cache-control"] == "no-store", saved.text
    assert client.post(base + "/filter-sets", json=body, headers=headers).status_code == 422
    listed = client.get(base + "/filter-sets", headers=headers).json()["filter_sets"]
    follows = json.dumps(listed[0]["cohort"]["follows"])
    assert client.get(base + "/summary", params={"follows": follows}, headers=headers).json()["cases"] == 1
    page = client.post(base + "/cases/query", json={"cohort": listed[0]["cohort"]}, headers=headers).json()
    assert page["selected_cases"] == 1 and page["cases"][0]["case_id"] == "1"
    gone = client.post(base + "/filter-sets/" + listed[0]["set_id"] + "/delete", headers=headers)
    assert gone.status_code == 200 and client.get(base + "/filter-sets", headers=headers).json()["filter_sets"] == []
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
