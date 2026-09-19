from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from server.local_auth import ensure_api_token, ensure_browser_secret

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_manual_browser_pairing_is_short_lived_one_time_and_extension_scoped(tmp_path):
    port = _free_port()
    auth_dir = tmp_path / "auth"
    data_dir = tmp_path / "data"
    bootstrap = "pairing-recovery-bootstrap"
    api_token = ensure_api_token(directory=auth_dir)
    expected_secret = ensure_browser_secret(directory=auth_dir)
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth_dir),
        "WORKFLOW_OBSERVER_DATA": str(data_dir),
        "WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP": bootstrap,
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.secure_app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 12
        while time.time() < deadline:
            try:
                if httpx.get(base + "/health", timeout=0.4).status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise AssertionError("secured API did not start")

        headers = {"Authorization": f"Bearer {api_token}"}
        issued = httpx.post(base + "/v1/browser-pairing-code", headers=headers)
        assert issued.status_code == 200
        code = issued.json()["code"]
        assert len(code) == 8 and code.isdigit()
        assert issued.json()["expires_in_seconds"] <= 120

        no_origin = httpx.post(base + "/v1/browser-pair", json={"code": code})
        assert no_origin.status_code == 403

        origin = {"Origin": "chrome-extension://openworkgraphtest"}
        paired = httpx.post(base + "/v1/browser-pair", json={"code": code}, headers=origin)
        assert paired.status_code == 200
        assert paired.json()["secret"] == expected_secret

        replay = httpx.post(base + "/v1/browser-pair", json={"code": code}, headers=origin)
        assert replay.status_code == 401
    finally:
        _stop(process)


def test_launcher_refuses_unknown_api_port_and_http_mcp_is_explicit_and_verified():
    source = (ROOT / "start.py").read_text(encoding="utf-8")
    optional = (ROOT / "server" / "mcp_http_control.py").read_text(encoding="utf-8")
    http_app = (ROOT / "mcp_server" / "http_app.py").read_text(encoding="utf-8")

    assert "if port_is_open(API_HOST, API_PORT):" in source
    assert "will not start capture or send evidence" in source
    assert "wait_for_api(api)" in source
    assert "if process.poll() is not None:" in source

    # v0.49 no longer starts or trusts a fixed MCP HTTP port during normal launch.
    assert "MCP_PORT" not in source
    assert "mcp_server.http_app:app" not in source
    assert "HTTP MCP is OFF by default" in source

    # When HTTP MCP is requested explicitly, its child proves a launch nonce
    # before the endpoint is exposed to the dashboard/client.
    assert "WORKFLOW_OBSERVER_MCP_INSTANCE_NONCE" in optional
    assert "_verify_instance" in optional
    assert "/openworkgraph-id" in optional
    assert "/openworkgraph-id" in http_app
    assert "instance_nonce" in http_app
