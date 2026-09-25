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


def test_context_execution_linkage_uses_normal_read_auth_and_never_exposes_native_run_id(tmp_path):
    port = _free_port()
    data_dir = tmp_path / "data"
    auth_dir = tmp_path / "auth"
    api_token = ensure_api_token(directory=auth_dir)
    agent_token = ensure_agent_ingest_token(directory=auth_dir)
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
        native_run = "PRIVATE-RUN-ID-DO-NOT-RETURN"
        context_sha = "d" * 64
        policy_sha = "e" * 64
        events = [
            {
                "event_id": "ctx-link-start",
                "observed_at": "2026-09-25T13:00:00+00:00",
                "agent_name": "SecureLinkAgent",
                "provider": "test",
                "framework": "autogen",
                "operation": "run_started",
                "status": "running",
                "observation_level": "native_trace",
                "run_id": native_run,
                "trace_id": native_run,
                "task_context": {
                    "preflight_attempted": True,
                    "available": True,
                    "resolved": True,
                    "context_sha256": context_sha,
                    "policy_manifest_sha256": policy_sha,
                    "family_key": "agent:structure:0123456789abcdef",
                },
            },
            {
                "event_id": "ctx-link-tool",
                "observed_at": "2026-09-25T13:00:01+00:00",
                "agent_name": "SecureLinkAgent",
                "provider": "test",
                "framework": "autogen",
                "operation": "tool_call",
                "status": "success",
                "observation_level": "native_trace",
                "run_id": native_run,
                "trace_id": native_run,
                "tool_name": "repository_search",
                "tool_category": "search",
            },
            {
                "event_id": "ctx-link-finish",
                "observed_at": "2026-09-25T13:00:02+00:00",
                "agent_name": "SecureLinkAgent",
                "provider": "test",
                "framework": "autogen",
                "operation": "run_finished",
                "status": "success",
                "observation_level": "native_trace",
                "run_id": native_run,
                "trace_id": native_run,
            },
        ]
        write_headers = {"Authorization": f"Bearer {agent_token}"}
        api_headers = {"Authorization": f"Bearer {api_token}"}
        accepted = httpx.post(
            base + "/agent-ingest/v1/events",
            json={"events": events},
            headers=write_headers,
            timeout=5,
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["inserted"] == 3

        endpoint = base + "/v1/task-context/executions?include_unlinked=false"
        assert httpx.get(endpoint, timeout=5).status_code == 401
        assert httpx.get(endpoint, headers=write_headers, timeout=5).status_code == 401

        response = httpx.get(endpoint, headers=api_headers, timeout=5)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["read_only"] is True
        assert payload["writes_performed"] is False
        assert payload["preflight_attempted_count"] == 1
        assert payload["context_resolved_count"] == 1
        assert payload["executions"][0]["outcome_status"] == "success"
        assert payload["executions"][0]["context_sha256"] == context_sha
        assert payload["executions"][0]["policy_manifest_sha256"] == policy_sha
        assert payload["executions"][0]["context_snapshot_verified_by_server"] is False
        assert payload["causal_interpretation"] is False

        serialized = json.dumps(payload)
        assert native_run not in serialized
        assert "prompt" not in serialized.lower()
        assert "reasoning" not in serialized.lower()
    finally:
        _stop(process)
