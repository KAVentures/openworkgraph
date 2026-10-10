from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_real_asgi_privacy_auth_is_isolated_from_legacy_in_process_tests(tmp_path):
    # Importing secure_app/browser_signal_routes in the pytest process would
    # install global middleware on main.app and contaminate unrelated legacy
    # tests. Run the *real* route/middleware assertions in their own process.
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
import server.browser_signal_routes  # noqa: F401
from server import local_auth, work_text_capture as wt

api = local_auth.ensure_api_token()
mcp = local_auth.ensure_mcp_token()
native = local_auth.ensure_dashboard_reopen_token()
assert len({api, mcp, native}) == 3
bearer = {"Authorization": f"Bearer {api}"}
dashboard = {"Authorization": f"OWG-Session {local_auth.create_dashboard_session()}"}

with TestClient(app) as client:
    for headers in (bearer, {**bearer, "X-OpenWorkGraph-Context": "ai"}):
        response = client.post("/v1/work-text/policy", json={
            "capture_enabled": True, "ai_read_enabled": True
        }, headers=headers)
        assert response.status_code == 403, response.text
        response = client.post("/v1/ai-access", json={"enabled": True}, headers=headers)
        assert response.status_code == 403, response.text
    assert wt.get_policy()["capture_enabled"] is False
    assert wt.get_policy()["ai_read_enabled"] is False
    for token in (api, mcp):
        assert client.post("/v1/dashboard-session/reopen", headers={
            "Authorization": f"Bearer {token}"
        }).status_code == 401
    reopened = client.post("/v1/dashboard-session/reopen", headers={
        "Authorization": f"Bearer {native}"
    })
    assert reopened.status_code == 200
    assert local_auth.dashboard_session_valid(reopened.json()["session"])
    assert client.post("/v1/work-text/policy", json={"capture_enabled": True}).status_code == 401

    enabled = client.post("/v1/work-text/policy", json={"capture_enabled": True}, headers=dashboard)
    assert enabled.status_code == 200 and enabled.json()["capture_enabled"] is True, enabled.text
    assert enabled.json()["ai_read_enabled"] is False
    enabled = client.post("/v1/work-text/policy", json={"ai_read_enabled": True}, headers=dashboard)
    assert enabled.status_code == 200 and enabled.json()["ai_read_enabled"] is True, enabled.text
    disabled = client.post("/v1/work-text/policy", json={"capture_enabled": False}, headers=dashboard)
    assert disabled.status_code == 200 and disabled.json()["ai_read_enabled"] is False, disabled.text
'''
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
    })
    process = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert process.returncode == 0, f"stdout={process.stdout}\nstderr={process.stderr}"
