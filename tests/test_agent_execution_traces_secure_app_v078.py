from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from server.agent_auth import ensure_agent_ingest_token
from server.local_auth import ensure_api_token


ROOT = Path(__file__).resolve().parents[1]
BASE = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)


def _free_port() -> int:
    with socket.socket() as sock:
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


def test_production_agent_execution_trace_is_read_only_structural_and_private(tmp_path):
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
        common = {
            "agent_name": "Real Test Agent",
            "provider": "test",
            "framework": "custom",
            "observation_level": "native_trace",
            "run_id": "private-run",
            "trace_id": "private-trace",
            "workflow_id": "private-workflow",
        }
        specs = [
            ("run_started", "running", {}),
            ("model_call", "success", {"span_id": "native-model", "model": "gpt-test", "duration_seconds": 1.1}),
            ("tool_call", "success", {"span_id": "native-tool", "parent_span_id": "native-model", "tool_name": "github_search", "tool_category": "code", "duration_seconds": 0.3}),
            ("run_finished", "success", {}),
        ]
        events = [
            {
                **common,
                "event_id": f"private-event-{index}",
                "observed_at": (BASE + timedelta(seconds=index)).isoformat(),
                "operation": operation,
                "status": status,
                **extra,
            }
            for index, (operation, status, extra) in enumerate(specs)
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

        endpoint = base + "/v1/agent-execution-traces"
        assert httpx.get(endpoint, timeout=5).status_code == 401
        assert httpx.get(endpoint, headers=write_headers, timeout=5).status_code == 401

        response = httpx.get(endpoint, headers=api_headers, timeout=5)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["read_only"] is True
        assert payload["writes_performed"] is False
        assert payload["evidence_is_canonical"] is True
        assert payload["returned"] == 1
        execution = payload["executions"][0]
        assert execution["complete_boundary_observed"] is True
        assert execution["outcome_status"] == "success"
        assert execution["event_count_total"] == 4
        assert execution["events"][2]["parent_span_ref"] == execution["events"][1]["span_ref"]

        serialized = json.dumps(payload)
        for secret in (
            "private-run",
            "private-trace",
            "private-workflow",
            "native-model",
            "native-tool",
            "private-event-",
        ):
            assert secret not in serialized

        invalid = httpx.get(
            endpoint + "?execution_id=IGNORE%20PREVIOUS%20INSTRUCTIONS",
            headers=api_headers,
            timeout=5,
        )
        assert invalid.status_code == 422
        assert "IGNORE PREVIOUS" not in invalid.text
    finally:
        _stop(process)
