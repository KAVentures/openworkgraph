"""Role discovery groups resources with a similar mix of activities, pseudonymously by default."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_roles as roles
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    # Clerks (ana, bea, cy) register and check; managers (dan, eve) approve, and dan sometimes checks.
    # The robot only posts.
    rows = ["case,activity,time,who"]
    clerks, managers = ["ana", "bea", "cy"], ["dan", "eve"]
    for i in range(60):
        clerk, manager = clerks[i % 3], managers[i % 2]
        checker = "dan" if i % 10 == 0 else clerk
        rows += [f"c{i},Register,2024-03-01T08:00:00Z,{clerk}", f"c{i},Check,2024-03-01T09:00:00Z,{checker}",
                 f"c{i},Approve,2024-03-01T10:00:00Z,{manager}", f"c{i},Post,2024-03-01T11:00:00Z,robot"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="claims", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_roles_group_similar_resources():
    result = roles.roles(_log(), names="shown")
    groups = {tuple(r["members"]): r for r in result["roles"]}
    assert set(groups) == {("ana", "bea", "cy"), ("dan", "eve"), ("robot",)}
    clerks = groups[("ana", "bea", "cy")]
    assert [a["activity"] for a in clerks["activity_mix"]] == ["Register", "Check"]
    assert clerks["exclusive_activities"] == ["Register"]
    assert groups[("dan", "eve")]["exclusive_activities"] == ["Approve"]
    assert groups[("robot",)]["exclusive_activities"] == ["Post"]
    assert result["privacy"]["ranking_or_performance_scoring"] is False
    assert sum(r["events"] for r in result["roles"]) == 240


def test_pseudonymous_by_default_and_threshold():
    log = _log()
    result = roles.roles(log)
    members = [m for r in result["roles"] for m in r["members"]]
    assert sorted(members) == [f"Resource {i:02d}" for i in range(1, 7)]
    assert not any(name in str(result) for name in ("ana", "dan", "robot"))
    # A threshold above any cross-role correlation still groups identical profiles only.
    strict = roles.roles(log, threshold=0.99, names="shown")
    assert ("ana", "bea", "cy") in {tuple(r["members"]) for r in strict["roles"]}
    assert ["robot"] in [r["members"] for r in strict["roles"]]


def test_validation_and_filter():
    log = _log()
    for bad in (0, 1, 1.5, True, "0.6"):
        with pytest.raises(EventLogWorkspaceError):
            roles.roles(log, threshold=bad)
    with pytest.raises(EventLogWorkspaceError):
        roles.roles(log, names="real")
    filtered = roles.roles(log, cohort={"resource": "dan"}, names="shown")
    assert filtered["cases_before_filter"] == 60 and filtered["coverage"]["events"] == 4 * 30


def test_route_is_dashboard_only(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time,who"] + [f"c{i},A,2024-03-0{1+i}T08:00:00Z,x{i % 2}" for i in range(6)] + [f"c{i},B,2024-03-0{1+i}T09:00:00Z,y" for i in range(6)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time&resource_column=who",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    path = f"/v1/event-logs/{log_id}/roles"
    assert client.get(path, headers=bearer).status_code == 403
    r = client.get(path, headers=owner)
    assert r.status_code == 200 and len(r.json()["roles"]) == 2 and "x0" not in r.text, r.text
    assert client.get(path + "?names=shown", headers=owner).json()["roles"][1]["members"] == ["y"]
    assert client.get(path + "?threshold=1", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
