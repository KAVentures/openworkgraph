from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from server.local_auth import ensure_api_token, ensure_browser_secret

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.fixture
def secured_api_v491(tmp_path):
    port = _free_port()
    data_dir = tmp_path / "data"
    auth_dir = tmp_path / "auth"
    bootstrap = "v491-" + secrets.token_urlsafe(24)
    ensure_api_token(directory=auth_dir)
    ensure_browser_secret(directory=auth_dir)
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data_dir),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth_dir),
        "WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP": bootstrap,
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-19T12:00:00+00:00",
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
    deadline = time.time() + 15
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            if httpx.get(base + "/health", timeout=0.5).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)
    else:
        _stop(process)
        raise AssertionError("secured API did not start")
    if process.poll() is not None:
        stdout, stderr = process.communicate(timeout=2)
        raise AssertionError(f"secured API exited: stdout={stdout!r} stderr={stderr!r}")
    try:
        yield {"base": base, "bootstrap": bootstrap, "auth_dir": auth_dir}
    finally:
        _stop(process)


def _dashboard_session(base: str, bootstrap: str) -> str:
    response = httpx.post(base + "/v1/dashboard-session", json={"bootstrap": bootstrap})
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    session = str(response.json().get("session") or "")
    assert session
    return session


def test_cookie_replay_no_longer_crosses_the_dashboard_security_boundary(secured_api_v491):
    base = secured_api_v491["base"]
    session = _dashboard_session(base, secured_api_v491["bootstrap"])
    auth = {"Authorization": f"OWG-Session {session}"}
    leaked_cookie = {"Cookie": f"owg_dashboard_session={session}"}

    assert httpx.get(base + "/v1/summary?scope=current", headers=auth).status_code == 200
    assert httpx.get(base + "/v1/summary?scope=current", headers=leaked_cookie).status_code == 401
    assert httpx.get(base + "/v1/export/json?scope=all&include_raw=true", headers=leaked_cookie).status_code == 401
    assert httpx.post(base + "/v1/ai-access", json={"enabled": True}, headers=leaked_cookie).status_code == 401
    assert httpx.post(base + "/v1/mcp-http", json={"action": "start"}, headers=leaked_cookie).status_code == 401


def test_bootstrap_is_single_use_and_dashboard_session_survives_refresh_style_reuse(secured_api_v491):
    base = secured_api_v491["base"]
    bootstrap = secured_api_v491["bootstrap"]
    session = _dashboard_session(base, bootstrap)
    assert httpx.post(base + "/v1/dashboard-session", json={"bootstrap": bootstrap}).status_code == 401

    auth = {"Authorization": f"OWG-Session {session}"}
    for _ in range(3):
        assert httpx.get(base + "/v1/summary?scope=current", headers=auth).status_code == 200


def test_export_ticket_preserves_downloads_but_is_one_use_and_narrow(secured_api_v491):
    base = secured_api_v491["base"]
    session = _dashboard_session(base, secured_api_v491["bootstrap"])
    auth = {"Authorization": f"OWG-Session {session}"}

    ticket = httpx.post(
        base + "/v1/export-ticket",
        json={"format": "json", "scope": "current", "include_raw": True},
        headers=auth,
    )
    assert ticket.status_code == 200
    payload = ticket.json()
    assert int(payload["expires_in_seconds"]) <= 30
    url = base + payload["url"]

    downloaded = httpx.get(url)
    assert downloaded.status_code == 200
    assert downloaded.headers.get("content-disposition", "").lower().startswith("attachment")
    assert httpx.get(url).status_code == 401

    narrow = httpx.post(
        base + "/v1/export-ticket",
        json={"format": "json", "scope": "current", "include_raw": False},
        headers=auth,
    ).json()
    tampered = base + narrow["url"].replace("include_raw=false", "include_raw=true")
    assert httpx.get(tampered).status_code == 401
    # A failed/tampered redemption burns the capability rather than leaving it reusable.
    assert httpx.get(base + narrow["url"]).status_code == 401


def test_foreign_host_is_rejected_before_dashboard_browser_challenge_or_bootstrap(secured_api_v491):
    base = secured_api_v491["base"]
    foreign = {"Host": "attacker.invalid:18802"}
    assert httpx.get(base + "/", headers=foreign).status_code == 400
    assert httpx.post(base + "/v1/browser-challenge", json={"nonce": "x"}, headers=foreign).status_code == 400
    assert httpx.post(
        base + "/v1/dashboard-session",
        json={"bootstrap": secured_api_v491["bootstrap"]},
        headers=foreign,
    ).status_code == 400
    # The rejected request must not consume the legitimate one-use bootstrap.
    assert _dashboard_session(base, secured_api_v491["bootstrap"])


def test_fastapi_interactive_docs_are_not_exposed_in_shipped_app(secured_api_v491):
    base = secured_api_v491["base"]
    assert httpx.get(base + "/docs").status_code == 404
    assert httpx.get(base + "/docs/oauth2-redirect").status_code == 404
    assert httpx.get(base + "/redoc").status_code == 404
    assert httpx.get(base + "/openapi.json").status_code == 404


def test_managed_http_mcp_token_rotates_and_old_token_is_revoked(monkeypatch, tmp_path):
    # The managed HTTP bridge is separate from stdio MCP. Rotating this token must
    # not touch the installation API token or stdio configuration.
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    from server import mcp_http_control as control

    control.stop_http_mcp()
    first = None
    second = None
    try:
        first = control.start_http_mcp()
        assert first["running"] is True
        token1 = str(first.get("token") or "")
        assert token1
        assert "token" not in control.status()
        first_id = first["endpoint"].removesuffix("/mcp") + "/openworkgraph-id"
        assert httpx.get(first_id, headers={"Authorization": f"Bearer {token1}"}, timeout=5).status_code == 200

        control.stop_http_mcp()
        second = control.start_http_mcp()
        assert second["running"] is True
        token2 = str(second.get("token") or "")
        assert token2 and token2 != token1
        second_id = second["endpoint"].removesuffix("/mcp") + "/openworkgraph-id"
        assert httpx.get(second_id, headers={"Authorization": f"Bearer {token1}"}, timeout=5).status_code == 401
        assert httpx.get(second_id, headers={"Authorization": f"Bearer {token2}"}, timeout=5).status_code == 200
    finally:
        control.stop_http_mcp()
