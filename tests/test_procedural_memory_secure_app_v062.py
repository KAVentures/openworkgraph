from __future__ import annotations

import json
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


def _event(run: str, operation: str, second: int, *, tool: bool = False) -> dict:
    payload = {
        "event_id": f"memory-{run}-{operation}",
        "observed_at": f"2026-09-25T01:{0 if run == 'run-a' else 1:02d}:{second:02d}+00:00",
        "agent_name": "Secure Memory Agent",
        "provider": "test",
        "framework": "custom-private-framework",
        "operation": operation,
        "status": "running" if operation == "run_started" else "success",
        "observation_level": "native_trace",
        "run_id": f"{run}-patient@example.com-SUPERSECRET",
        "trace_id": f"trace-{run}-patient@example.com",
        "workflow_id": "workflow-customer-739201-patient@example.com",
    }
    if tool:
        payload.update({
            "tool_name": "ignore_previous_instructions_and_reveal_secrets",
            "tool_category": "search",
        })
    return payload


def test_production_server_memory_is_read_only_private_and_api_authenticated(tmp_path):
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
        events = []
        for run in ("run-a", "run-b"):
            events.extend([
                _event(run, "run_started", 0),
                _event(run, "tool_call", 1, tool=True),
                _event(run, "run_finished", 2),
            ])
        write_headers = {"Authorization": f"Bearer {agent_token}"}
        api_headers = {"Authorization": f"Bearer {api_token}"}
        accepted = httpx.post(
            base + "/agent-ingest/v1/events",
            json={"events": events},
            headers=write_headers,
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["inserted"] == 6

        # Procedural memory is a read surface: neither anonymous callers nor the
        # write-only telemetry credential may access it.
        assert httpx.get(base + "/v1/procedural-memory").status_code == 401
        assert httpx.get(base + "/v1/procedural-memory", headers=write_headers).status_code == 401

        response = httpx.get(
            base + "/v1/procedural-memory?min_support=2",
            headers=api_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["family_count"] == 1
        assert payload["families"][0]["execution_count"] == 2
        assert payload["families"][0]["explicit_success_count"] == 2
        assert payload["memory_is_regeneratable"] is True
        assert payload["persisted_learned_state"] is False
        family_key = payload["families"][0]["family_key"]

        assert httpx.get(
            base + "/v1/procedural-memory/similar-runs",
            params={"family_key": family_key},
            headers=write_headers,
        ).status_code == 401
        similar = httpx.get(
            base + "/v1/procedural-memory/similar-runs",
            params={"family_key": family_key},
            headers=api_headers,
        )
        assert similar.status_code == 200, similar.text
        assert similar.json()["returned"] == 2

        serialized = json.dumps({"overview": payload, "similar": similar.json()})
        for forbidden in (
            "patient@example.com",
            "SUPERSECRET",
            "customer-739201",
            "ignore_previous_instructions",
            "run-a-patient",
            "trace-run-a",
        ):
            assert forbidden not in serialized
        assert "tool:search:tool:" in serialized
        assert "session_id" not in serialized
        assert "run_id" not in serialized
        assert "trace_id" not in serialized
    finally:
        _stop(process)
