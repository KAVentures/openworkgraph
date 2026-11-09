"""API acceptance for explicit local backup scheduling and owner-only controls."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_actual_schedule_router_with_isolated_local_authorization(tmp_path):
    source = r'''
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import local_scheduler_routes, local_auth, local_scheduler

app = FastAPI()
app.include_router(local_scheduler_routes.router)
session = "OWG-Session " + local_auth.create_dashboard_session()
with TestClient(app) as client:
    owner = {"Authorization": session}
    assert client.get("/v1/schedules").status_code == 403
    assert client.get("/v1/schedules", headers={"Authorization": "Bearer synthetic-AI"}).status_code == 403
    state = client.get("/v1/schedules", headers=owner)
    assert state.status_code == 200
    assert "local_backup" in state.json()["kinds"]
    assert state.json()["schedules"] == []
    body = {"kind": "local_backup", "target_id": "local", "interval_minutes": 1440,
            "confirm_scheduled_runs": True}
    assert client.post("/v1/schedules", json=body, headers=owner).status_code == 409
    assert client.post("/v1/schedules", json={**body, "confirm_unencrypted_local_backup": "true"},
                       headers=owner).status_code == 409
    assert client.post("/v1/schedules", json={**body, "interval_minutes": 60,
                       "confirm_unencrypted_local_backup": True}, headers=owner).status_code == 409
    assert client.post("/v1/schedules", json={**body, "target_id": "../files",
                       "confirm_unencrypted_local_backup": True}, headers=owner).status_code == 409
    created = client.post("/v1/schedules",
                          json={**body, "confirm_unencrypted_local_backup": True}, headers=owner)
    assert created.status_code == 200, created.text
    item = created.json()
    assert item["enabled"] and item["unencrypted_local_backup_acknowledged"] is True
    assert item["kind"] == "local_backup" and item["target_id"] == "local"
    backups = local_scheduler.DATA_DIR.parent / "backups"
    assert not backups.exists(), "Creation must not start a backup"
    route = "/v1/schedules/update"
    assert client.post(route, json={"schedule_id": item["schedule_id"], "interval_minutes": 15},
                       headers=owner).status_code == 409
    disabled = client.post(route, json={"schedule_id": item["schedule_id"], "enabled": False},
                           headers=owner)
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    assert not backups.exists()
    assert client.post("/v1/schedules/delete", json={"schedule_id": item["schedule_id"]},
                       headers={"Authorization": "Bearer synthetic-AI"}).status_code == 403
    assert client.post("/v1/schedules/delete", json={"schedule_id": item["schedule_id"]},
                       headers=owner).json()["removed"] is True
    assert client.get("/v1/schedules", headers=owner).json()["schedules"] == []
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "OWG_BACKUP_DIR": str(tmp_path / "backups"),
    })
    result = subprocess.run([sys.executable, "-c", source], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_privacy_schedule_controls_have_separate_acknowledgement():
    js = (ROOT / "dashboard/basic_mode.js").read_text(encoding="utf-8")
    assert 'value="local_backup">Local backup (unencrypted)' in js
    assert 'id="pvScheduleBackupAck"' in js
    assert 'scheduleInterval.min = local ? \'1440\'' in js
    assert "confirm_unencrypted_local_backup: isBackup && scheduleBackupAck.checked" in js
    assert "kind, target_id: isBackup ? 'local'" in js
    assert "scheduleBackupAckRow.hidden = !local" in js
    assert "Scheduled jobs" in js
    routes = (ROOT / "server/local_scheduler_routes.py").read_text(encoding="utf-8")
    assert 'confirm_unencrypted_local_backup=data.get("confirm_unencrypted_local_backup")' in routes
