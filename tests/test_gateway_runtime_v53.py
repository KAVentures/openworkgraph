from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_enterprise_controls_extend_the_established_secure_app():
    start = _read("apps/desktop/start.py")
    runner = _read("server/enterprise_runner.py")
    enterprise = _read("server/enterprise_app.py")

    assert 'SECURE_API_APP = "server.secure_app:app"' in start
    assert '"-m", "server.enterprise_runner"' in start
    assert 'SECURE_APP = "server.secure_app:app"' in runner
    assert "import server.enterprise_app" in runner
    assert "uvicorn.run(SECURE_APP" in runner
    assert "from .secure_app import app" in enterprise


def test_gateway_dashboard_routes_inherit_local_capability_auth():
    # Importing secure_app intentionally installs middleware/routes onto the
    # shared FastAPI app object. Exercise that in a subprocess so this security
    # regression test cannot mutate server.main.app for unrelated legacy tests.
    code = r'''
from fastapi.testclient import TestClient
from server.enterprise_app import app

with TestClient(app) as client:
    response = client.get("/v1/gateway-status")
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "authentication required"

    response2 = client.post("/v1/gateway-sharing", json={"enabled": True})
    assert response2.status_code == 401, response2.text
    assert response2.json()["detail"] == "authentication required"
'''
    env = os.environ.copy()
    env["WORKFLOW_OBSERVER_MODE"] = "demo"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, (
        f"isolated secure-app check failed\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )


def test_desktop_parent_supervises_api_and_collector_siblings(monkeypatch):
    from apps.desktop import start

    class Proc:
        def __init__(self, code):
            self.code = code

        def poll(self):
            return self.code

    stopped = []
    monkeypatch.setattr(start, "stop_process", lambda process: stopped.append(process))

    api = Proc(7)
    collector = Proc(None)
    try:
        start.supervise_runtime(api, collector, poll_interval=0.01)
        assert False, "dead API must fail the parent"
    except RuntimeError as exc:
        assert "local API exited unexpectedly" in str(exc)
    assert stopped == [collector]

    api2 = Proc(None)
    collector2 = Proc(9)
    try:
        start.supervise_runtime(api2, collector2, poll_interval=0.01)
        assert False, "dead collector must fail the parent"
    except RuntimeError as exc:
        assert "collector exited unexpectedly" in str(exc)


def test_desktop_start_text_matches_actual_redacted_ai_default():
    start = _read("apps/desktop/start.py")
    assert "AI access starts ON at Redacted on a new install" in start
    assert "A new install starts ON at Redacted" in start
    assert "AI access starts OFF on a new install" not in start
