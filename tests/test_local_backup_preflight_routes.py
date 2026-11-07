"""Local backup preflight is an authenticated, read-only operator action (#370 M7).

This test uses the actual dashboard ASGI app and its session/bearer gate; no
recorder, external source, customer data or browser restore is used.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_selected_backup_preflight_is_dashboard_only_and_never_restores(tmp_path):
    script = r'''
import json
from pathlib import Path
from fastapi.testclient import TestClient
from server.secure_app import app
import server.basic_mode_routes  # noqa: F401
from server import local_backup, local_auth

data = local_backup.data_dir()
data.mkdir(parents=True, exist_ok=True)
(data / "fixture.txt").write_text("keep recorded data", encoding="utf-8")
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    created = local_backup.create_in_folder()
    name = Path(created["archive"]).name
    endpoint = "/v1/local-backups/preflight"
    body = {"name": name}

    # The ordinary AI API token must never be able to inspect disk backups.
    assert client.post(endpoint, json=body).status_code in (401, 403)
    assert client.post(endpoint, json=body, headers=bearer).status_code in (401, 403)
    methods = client.get(endpoint, headers=owner)
    assert methods.status_code == 405
    selected = client.post(endpoint, json=body, headers=owner)
    assert selected.status_code == 200, selected.text
    assert selected.headers["cache-control"] == "no-store"
    report = selected.json()
    assert report["verified"] is True
    assert report["operation"] == "offline_restore_preflight_only"
    assert report["writes_performed"] is False
    assert report["file_count"] >= 1
    assert "data_target" in report and "auth_target" in report
    assert report["requires_replace"] is True
    assert report["ready"] is False
    assert any("--replace" in reason for reason in report["blockers"])
    assert (data / "fixture.txt").read_text() == "keep recorded data"
    assert not list(data.parent.glob("data.pre-restore-*"))
    assert not list(data.parent.glob("data.failed-restore-*"))

    # No arbitrary filesystem paths, duplicate/extra fields or missing paths.
    cases = [
        ({"name": "../" + name}, 422),
        ({"name": "owg-backup-..\\\\..\\\\escape.zip"}, 422),
        ({"name": "/tmp/" + name}, 422),
        ({"name": name, "replace": True}, 422),
        ({"name": 123}, 422),
        ({"name": ""}, 422),
        ({"name": "owg-backup-does-not-exist.zip"}, 404),
    ]
    for input_body, expected in cases:
        response = client.post(endpoint, json=input_body, headers=owner)
        assert response.status_code == expected, (input_body, response.status_code, response.text)
    assert client.post(endpoint, content="not-json",
                       headers={**owner, "Content-Type": "text/plain"}).status_code == 415
    too_large = client.post(endpoint, json={"name": "owg-backup-" + "x" * 1000 + ".zip"}, headers=owner)
    assert too_large.status_code == 413

    # Passwords never pass through this operation, even if the file is encrypted.
    passphrase = "synthetic encrypted recovery passphrase"
    encrypted = local_backup.create_in_folder(passphrase=passphrase)
    encrypted_name = Path(encrypted["archive"]).name
    encrypted_response = client.post(endpoint, json={"name": encrypted_name}, headers=owner)
    assert encrypted_response.status_code == 422
    assert "offline CLI" in encrypted_response.text
    assert passphrase not in encrypted_response.text
    assert "passphrase" not in json.dumps(report).lower()
    assert (data / "fixture.txt").read_text() == "keep recorded data"
    assert not list(data.parent.glob("data.pre-restore-*"))
    assert not list(data.parent.glob("data.failed-restore-*"))
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "data" / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "OWG_BACKUP_DIR": str(tmp_path / "backups"),
    })
    completed = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"


def test_dashboard_backup_inspection_is_cli_only_and_safe_text_rendering():
    routes = (ROOT / "server" / "basic_mode_routes.py").read_text(encoding="utf-8")
    ui = (ROOT / "dashboard" / "basic_mode.js").read_text(encoding="utf-8")
    assert '"/v1/local-backups/preflight", post_local_backup_preflight' in routes
    assert "_require_dashboard_session(request)" in routes.split("async def post_local_backup_preflight")[1].split(
        "def _require_dashboard_session")[0]
    assert "local_backup.plan_restore" in routes
    assert "id=\"pvBackupChoice\"" in ui and "id=\"pvBackupInspect\"" in ui
    assert "backupPlan.textContent =" in ui
    assert "api('/v1/local-backups/preflight', send('POST', {name}))" in ui
    assert "offline CLI" in ui
    assert "passphrase: pass.value" in ui  # existing backup creation is explicitly separate
    assert "No restore was attempted." in ui
