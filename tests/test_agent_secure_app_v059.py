from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from server.agent_auth import ensure_agent_ingest_token
from server.local_auth import ensure_api_token

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
    raise AssertionError(f"secure server did not become ready; stdout={stdout!r} stderr={stderr!r}")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_production_secure_server_accepts_agent_write_token_but_not_for_history_reads(tmp_path):
    port = _free_port()
    data_dir = tmp_path / "data"
    auth_dir = tmp_path / "auth"
    api_token = ensure_api_token(directory=auth_dir)
    agent_token = ensure_agent_ingest_token(directory=auth_dir)
    assert api_token != agent_token

    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data_dir),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth_dir),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-25T00:00:00+00:00",
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
    _wait_http(base + "/health", process)
    try:
        payload = {
            "events": [{
                "event_id": "secure-agent-v059",
                "observed_at": "2026-09-25T00:15:00+00:00",
                "agent_name": "Secure Test Agent",
                "operation": "tool_call",
                "status": "success",
                "observation_level": "native_trace",
                "run_id": "secure-run-v059",
                "trace_id": "secure-trace-v059",
                "span_id": "secure-span-v059",
                "tool_name": "repository_search",
                "tool_category": "search",
            }]
        }
        write_headers = {"Authorization": f"Bearer {agent_token}"}
        api_headers = {"Authorization": f"Bearer {api_token}"}

        assert httpx.post(base + "/agent-ingest/v1/events", json=payload).status_code == 401
        assert httpx.post(base + "/agent-ingest/v1/events", json=payload, headers=api_headers).status_code == 401
        accepted = httpx.post(base + "/agent-ingest/v1/events", json=payload, headers=write_headers)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["inserted"] == 1

        # Least privilege: the agent token cannot use ordinary history/read APIs.
        assert httpx.get(base + "/v1/events", headers=write_headers).status_code == 401
        assert httpx.get(base + "/v1/agent-workflows", headers=write_headers).status_code == 401

        # The normal API credential can search structural run metadata and verify
        # the exact canonical event that the write-only adapter inserted.
        readback = httpx.get(base + "/v1/events?query=secure-run-v059", headers=api_headers)
        assert readback.status_code == 200, readback.text
        events = readback.json().get("events", [])
        assert any(event.get("event_id") == "secure-agent-v059" for event in events)
    finally:
        _stop(process)
