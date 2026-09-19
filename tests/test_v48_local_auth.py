from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from server.local_auth import ensure_api_token, ensure_browser_secret, ensure_mcp_token

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, process: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            response = httpx.get(url, timeout=0.5)
            if response.status_code < 500:
                return
        except Exception:
            time.sleep(0.1)
    stdout, stderr = process.communicate(timeout=2) if process.poll() is not None else ("", "")
    raise AssertionError(f"process did not become ready at {url}; stdout={stdout!r} stderr={stderr!r}")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=5)


def _signed_browser_header(secret: str, method: str, path: str, body: str, nonce: str = "test-nonce") -> str:
    ts = int(time.time())
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical = f"{ts}\n{nonce}\n{method.upper()}\n{path}\n{body_hash}".encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return f"OWG-HMAC {ts}.{nonce}.{mac}"


def _event_payload() -> dict:
    return {
        "event_id": "v48-test-event", "observed_at": "2026-09-18T16:00:00+00:00",
        "schema_version": "1.0", "organization_id": "", "actor_id": "",
        "device_id": "test-device", "sensor_id": "desktop:test", "source": "desktop",
        "session_id": "test-session", "app": "TestApp", "window_title": "Test window",
        "event_type": "focus_span", "duration_seconds": 1.0, "screenshot_path": None,
        "metadata": {"source": "desktop", "activity": {"keypress_count": 0}},
    }


def _browser_payload() -> dict:
    return {
        "event_id": "v48-browser-event", "observed_at": "2026-09-18T16:00:01+00:00",
        "sensor_id": "browser:test", "sensor_version": "1.9.0-v48-local-auth",
        "browser_session_id": "browser-session", "organization_id": "", "actor_id": "",
        "device_id": "test-device", "work_session_id": "test-session", "action": "click",
        "page": {"origin": "https://mail.google.com", "hostname": "mail.google.com", "pathname": "/mail/u/0", "title": "Inbox"},
        "target": {"tag": "button", "role": "button", "label": "Send"}, "metadata": {},
    }


@pytest.fixture
def secured_api(tmp_path):
    api_port = _free_port()
    data_dir = tmp_path / "data"; auth_dir = tmp_path / "auth"
    bootstrap = "v48-dashboard-bootstrap-test"
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data_dir), "WORKFLOW_OBSERVER_AUTH_DIR": str(auth_dir),
        "WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP": bootstrap, "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-18T15:00:00+00:00",
    })
    api_token = ensure_api_token(directory=auth_dir)
    browser_secret = ensure_browser_secret(directory=auth_dir)
    mcp_token = ensure_mcp_token(directory=auth_dir)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.secure_app:app", "--host", "127.0.0.1", "--port", str(api_port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    base = f"http://127.0.0.1:{api_port}"
    _wait_http(base + "/health", process)
    try:
        yield {"base": base, "process": process, "env": env, "api_token": api_token, "browser_secret": browser_secret, "mcp_token": mcp_token, "bootstrap": bootstrap, "auth_dir": auth_dir}
    finally:
        _stop(process)


def test_unauthenticated_reads_and_collector_writes_fail_closed(secured_api):
    base = secured_api["base"]
    assert httpx.get(f"{base}/v1/export/json?scope=all&include_raw=true").status_code == 401
    assert httpx.get(f"{base}/v1/events").status_code == 401
    assert httpx.post(f"{base}/v1/events", json={"events": [_event_payload()]}).status_code == 401
    auth = {"Authorization": f"Bearer {secured_api['api_token']}"}
    inserted = httpx.post(f"{base}/v1/events", json={"events": [_event_payload()]}, headers=auth)
    assert inserted.status_code == 200 and inserted.json()["inserted"] == 1
    readback = httpx.get(f"{base}/v1/events", headers=auth)
    assert readback.status_code == 200
    assert any(event.get("event_id") == "v48-test-event" for event in readback.json().get("events", []))


def test_dashboard_bootstrap_uses_cookie_not_master_token(secured_api):
    base = secured_api["base"]
    shell = httpx.get(base + "/")
    assert shell.status_code == 200
    assert secured_api["api_token"] not in shell.text
    assert secured_api["mcp_token"] not in shell.text
    assert "WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP" not in shell.text
    with httpx.Client(base_url=base) as client:
        assert client.get("/v1/summary?scope=current").status_code == 401
        login = client.post("/v1/dashboard-session", json={"bootstrap": secured_api["bootstrap"]})
        assert login.status_code == 200
        assert "httponly" in login.headers.get("set-cookie", "").lower()
        assert client.get("/v1/summary?scope=current").status_code == 200
        config = client.get("/v1/mcp-connection-config")
        assert config.status_code == 200
        cfg = config.json()
        assert cfg["transport"] == "stdio"
        assert cfg["args"] == ["-m", "mcp_server.secure_stdio"]
        assert "token" not in cfg
        assert secured_api["mcp_token"] not in json.dumps(cfg)


def test_browser_requires_server_proof_and_signed_request(secured_api):
    base = secured_api["base"]; origin = "chrome-extension://openworkgraphtest"
    preflight = httpx.options(f"{base}/v1/browser-events", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,authorization"})
    assert preflight.status_code in {200, 204}
    assert preflight.headers.get("access-control-allow-origin") == origin
    challenge_nonce = "challenge-test"
    challenge = httpx.post(f"{base}/v1/browser-challenge", json={"nonce": challenge_nonce}, headers={"Origin": origin})
    assert challenge.status_code == 200
    expected = hmac.new(secured_api["browser_secret"].encode("utf-8"), f"openworkgraph-server-proof:{challenge_nonce}".encode("utf-8"), hashlib.sha256).hexdigest()
    assert challenge.json()["proof"] == expected
    payload = _browser_payload()
    assert httpx.post(f"{base}/v1/browser-events", json=payload, headers={"Origin": origin}).status_code == 401
    body = json.dumps(payload, separators=(",", ":"))
    auth = _signed_browser_header(secured_api["browser_secret"], "POST", "/v1/browser-events", body)
    accepted = httpx.post(f"{base}/v1/browser-events", content=body, headers={"Origin": origin, "Content-Type": "application/json", "Authorization": auth})
    assert accepted.status_code == 200 and accepted.json()["status"] == "ok"
    replay = httpx.post(f"{base}/v1/browser-events", content=body, headers={"Origin": origin, "Content-Type": "application/json", "Authorization": auth})
    assert replay.status_code == 401


def test_mcp_http_endpoint_rejects_unauthenticated_clients(secured_api):
    mcp_port = _free_port(); env = dict(secured_api["env"]); env["WORKFLOW_OBSERVER_API"] = secured_api["base"]
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "mcp_server.http_app:app", "--host", "127.0.0.1", "--port", str(mcp_port)], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    endpoint = f"http://127.0.0.1:{mcp_port}/mcp"
    try:
        deadline = time.time() + 12
        while time.time() < deadline:
            if process.poll() is not None: break
            try:
                with socket.create_connection(("127.0.0.1", mcp_port), timeout=0.2): break
            except OSError: time.sleep(0.1)
        else: raise AssertionError("MCP server did not start")
        payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "v48-test", "version": "1"}}}
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        assert httpx.post(endpoint, json=payload, headers=headers, timeout=5).status_code == 401
        assert httpx.post(endpoint, json=payload, headers=dict(headers, Authorization="Bearer wrong"), timeout=5).status_code == 401
        response = httpx.post(endpoint, json=payload, headers=dict(headers, Authorization=f"Bearer {secured_api['mcp_token']}"), timeout=5)
        assert response.status_code != 401
    finally: _stop(process)


def test_browser_pairing_bundle_is_runtime_only_and_launcher_preserves_core_paths():
    start = (ROOT / "start.py").read_text(encoding="utf-8")
    optional = (ROOT / "server" / "mcp_http_control.py").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "server.secure_app:app" in start
    assert "collector.secure_main" in start
    assert "mcp_server.http_app:app" not in start
    assert "mcp_server.http_app:app" in optional
    assert "write_browser_pairing_bundle" in start
    assert "browser_extension/pairing.json" in gitignore


def test_local_secret_permissions_are_private_on_posix(tmp_path):
    if os.name == "nt": pytest.skip("POSIX mode bits are not the Windows ACL model")
    auth_dir = tmp_path / "auth"; ensure_api_token(directory=auth_dir)
    assert (auth_dir / ".api_token").stat().st_mode & 0o777 == 0o600
