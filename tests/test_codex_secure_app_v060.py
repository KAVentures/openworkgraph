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


def _attr(key: str, value):
    if isinstance(value, bool):
        wrapped = {"boolValue": value}
    elif isinstance(value, int):
        wrapped = {"intValue": str(value)}
    else:
        wrapped = {"stringValue": str(value)}
    return {"key": key, "value": wrapped}


def _codex_trace_payload() -> dict:
    attrs = {
        "event.name": "codex.tool_result",
        "event.timestamp": "2026-09-25T01:45:00.000Z",
        "conversation.id": "codex-prod-run-v060",
        "model": "gpt-test",
        "tool_result_seq": 1,
        "tool_name": "exec_command",
        "tool_namespace": "functions",
        "call_id": "prod-call-v060",
        "duration_ms": 73,
        "success": True,
    }
    return {
        "resourceSpans": [{
            "scopeSpans": [{
                "spans": [{
                    "traceId": "otel-trace-v060",
                    "spanId": "otel-span-v060",
                    "name": "sensitive span name patient@example.com",
                    "attributes": [
                        _attr("untrusted.span.attribute", "SUPERSECRET"),
                    ],
                    "events": [{
                        "timeUnixNano": "1790297100000000000",
                        "name": "sensitive native event name SUPERSECRET",
                        "attributes": [_attr(k, v) for k, v in attrs.items()],
                    }],
                }],
            }],
        }],
    }


def test_production_secure_server_accepts_codex_trace_only_ingestion(tmp_path):
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
        write_headers = {"Authorization": f"Bearer {agent_token}"}
        api_headers = {"Authorization": f"Bearer {api_token}"}
        payload = _codex_trace_payload()

        assert httpx.post(base + "/agent-ingest/v1/codex-otel", json=payload).status_code == 401
        assert httpx.post(base + "/agent-ingest/v1/codex-otel", json=payload, headers=api_headers).status_code == 401
        accepted = httpx.post(base + "/agent-ingest/v1/codex-otel", json=payload, headers=write_headers)
        assert accepted.status_code == 202, accepted.text
        assert accepted.content == b""

        # Least privilege remains intact on the fully composed secure app.
        assert httpx.get(base + "/v1/events", headers=write_headers).status_code == 401

        readback = httpx.get(base + "/v1/events?query=codex-prod-run-v060", headers=api_headers)
        assert readback.status_code == 200, readback.text
        events = readback.json().get("events", [])
        assert len(events) == 1
        event = events[0]
        assert event["event_type"] == "agent_tool_call"
        assert event["metadata"]["agent"]["framework"] == "codex"
        assert event["metadata"]["tool"] == {"name": "exec_command", "category": "shell"}
        serialized = str(event)
        assert "SUPERSECRET" not in serialized
        assert "patient@example.com" not in serialized
    finally:
        _stop(process)
