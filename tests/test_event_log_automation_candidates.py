"""Automation candidates rank activities by volume, routine routing and the waiting before them."""
from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest

from server import event_log_automation_candidates as automation
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    # 20 invoices over 4 weeks: Receive -> Check -> Post always (routine, long wait before Check);
    # half of them also need "Clarify", reached from different steps (variable).
    rows = ["case,activity,time,who"]
    for i in range(20):
        day = 1 + i
        rows += [f"c{i},Receive,2024-03-{day:02d}T08:00:00Z,mail",
                 f"c{i},Check,2024-03-{day:02d}T14:00:00Z,ana"]
        if i % 2 == 0:
            rows.append(f"c{i},Clarify,2024-03-{day:02d}T14:30:00Z,{'bob' if i % 4 else 'cy'}")
        rows.append(f"c{i},Post,2024-03-{day:02d}T15:00:00Z,bot")
    rows += ["x1,Escalate,2024-03-02T09:00:00Z,dan"]  # too rare to rank
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="invoices", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_ranking_components_and_patterns():
    result = automation.candidates(_log())
    by = {c["activity"]: c for c in result["candidates"]}
    assert set(by) == {"Receive", "Check", "Clarify", "Post"} and result["activities_below_min_cases"] == 1
    check = by["Check"]
    # Always after Receive, but followed by Clarify or Post: rules with exceptions.
    assert (check["same_predecessor_share"], check["same_successor_share"], check["routing_pattern"]) == (1.0, 0.5, "mostly_fixed")
    assert by["Receive"]["routing_pattern"] == "fixed"
    assert check["median_wait_before_seconds"] == 6 * 3600 and check["resources"] == 1
    # Check carries most of the waiting, so it ranks first.
    assert result["candidates"][0]["activity"] == "Check"
    assert check["components"]["time"] > 0.8
    clarify = by["Clarify"]
    assert clarify["cases"] == 10 and clarify["case_share"] == pytest.approx(10 / 21, abs=0.001)
    assert clarify["resources"] == 2 and clarify["top_resource_share"] == 0.5
    post = by["Post"]
    assert post["same_successor_share"] == 1.0 and post["same_predecessor_share"] == 0.5
    assert post["routing_pattern"] == "mostly_fixed"
    assert result["weights"] == {"volume": 0.4, "routine": 0.4, "time": 0.2}
    for item in result["candidates"]:
        c = item["components"]
        assert item["score"] == pytest.approx(100 * (0.4 * c["volume"] + 0.4 * c["routine"] + 0.2 * c["time"]), abs=0.2)


def test_filter_and_validation():
    log = _log()
    filtered = automation.candidates(log, cohort={"resource": "bob"})
    assert filtered["cases"] == 5 and filtered["cases_before_filter"] == 21
    assert automation.candidates(log, limit=2)["candidates"][0]["activity"] == "Check"
    assert len(automation.candidates(log, limit=2)["candidates"]) == 2
    for bad in (0, 51, "3", True):
        with pytest.raises(workspace.EventLogWorkspaceError):
            automation.candidates(log, limit=bad)


def test_route_is_dashboard_only(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T08:00:00Z" for i in range(6)] + [f"c{i},B,2024-03-0{1+i}T09:00:00Z" for i in range(6)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/automation-candidates"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path + "?limit=5&activity=A", headers=owner)
    assert r.status_code == 200 and [c["activity"] for c in r.json()["candidates"]] == ["B", "A"], r.text
    assert client.get(path + "?limit=0", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
