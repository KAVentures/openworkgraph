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
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import db as server_db
from server.agent_auth import ensure_agent_ingest_token
from server.agent_ingest import MAX_AGENT_BATCH_BYTES
from server.agent_routes import AGENT_EVENT_PATH, router as agent_router
from shared.capture_control import initialize_run, set_state
from shared.evidence_deletion import add_tombstone


ROOT = Path(__file__).resolve().parents[1]


def _isolated_app() -> FastAPI:
    server_db.init_db()
    app = FastAPI()
    app.include_router(agent_router)
    return app


def _agent_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ensure_agent_ingest_token()}"}


def _event(event_id: str, observed_at: datetime | str | None = None, **overrides):
    if isinstance(observed_at, datetime):
        observed = observed_at.isoformat()
    else:
        observed = str(observed_at or datetime.now(timezone.utc).isoformat())
    payload = {
        "event_id": event_id,
        "observed_at": observed,
        "organization_id": "test-org",
        "actor_id": "agent:test-agent",
        "device_id": "agent-device",
        "sensor_id": "agent:test-adapter",
        "agent_name": "Test Agent",
        "provider": "test-provider",
        "framework": "custom",
        "model": "test-model",
        "operation": "tool_call",
        "status": "success",
        "observation_level": "instrumented_tools",
        "run_id": "run-v084",
        "trace_id": "trace-v084",
        "span_id": "span-v084",
        "parent_span_id": "root-v084",
        "workflow_id": "workflow-v084",
        "tool_name": "repository_search",
        "tool_category": "search",
        "duration_seconds": 0.1,
    }
    payload.update(overrides)
    return payload


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


def test_dashboard_session_can_read_agent_reports_while_agent_token_remains_write_only(tmp_path):
    port = _free_port()
    data_dir = tmp_path / "server-data"
    auth_dir = tmp_path / "server-auth"
    bootstrap = "agent-dashboard-v084-bootstrap"
    agent_token = ensure_agent_ingest_token(directory=auth_dir)
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data_dir),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth_dir),
        "WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP": bootstrap,
        "WORKFLOW_OBSERVER_MODE": "observe",
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
        exchanged = httpx.post(base + "/v1/dashboard-session", json={"bootstrap": bootstrap}, timeout=5)
        assert exchanged.status_code == 200, exchanged.text
        dashboard_session = exchanged.json()["session"]
        dashboard_headers = {"Authorization": f"OWG-Session {dashboard_session}"}
        agent_headers = {"Authorization": f"Bearer {agent_token}"}

        reports = httpx.get(base + "/v1/agent-execution-traces", headers=dashboard_headers, timeout=5)
        assert reports.status_code == 200, reports.text
        assert reports.json()["read_only"] is True
        assert httpx.get(base + "/v1/agent-workflows", headers=dashboard_headers, timeout=5).status_code == 200

        # The least-privilege agent credential remains unable to read either view.
        assert httpx.get(base + "/v1/agent-execution-traces", headers=agent_headers, timeout=5).status_code == 401
        assert httpx.get(base + "/v1/agent-workflows", headers=agent_headers, timeout=5).status_code == 401
    finally:
        _stop(process)


def test_pause_and_stop_suppress_late_agent_events(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "capture-control"))
    now = datetime.now(timezone.utc)
    initialize_run((now - timedelta(seconds=5)).isoformat())
    set_state("pause", at=(now - timedelta(seconds=2)).isoformat())

    with TestClient(_isolated_app()) as client:
        paused_id = "v084-paused-agent"
        paused = client.post(
            AGENT_EVENT_PATH,
            headers=_agent_headers(),
            json={"events": [_event(paused_id, now - timedelta(seconds=1))]},
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["inserted"] == 0
        assert server_db.rows("SELECT * FROM events WHERE event_id = ?", (paused_id,)) == []

        set_state("resume", at=now.isoformat())
        accepted_id = "v084-resumed-agent"
        accepted = client.post(
            AGENT_EVENT_PATH,
            headers=_agent_headers(),
            json={"events": [_event(accepted_id, now + timedelta(seconds=1))]},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["inserted"] == 1

        set_state("stop", at=(now + timedelta(seconds=2)).isoformat())
        stopped_id = "v084-stopped-agent"
        stopped = client.post(
            AGENT_EVENT_PATH,
            headers=_agent_headers(),
            json={"events": [_event(stopped_id, now + timedelta(seconds=3))]},
        )
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["inserted"] == 0
        assert server_db.rows("SELECT * FROM events WHERE event_id = ?", (stopped_id,)) == []


def test_deleted_time_range_cannot_be_repopulated_by_agent_delivery(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path / "deletion-control"))
    now = datetime.now(timezone.utc)
    add_tombstone((now - timedelta(seconds=30)).isoformat(), (now + timedelta(seconds=30)).isoformat())
    event_id = "v084-deleted-range-agent"

    with TestClient(_isolated_app()) as client:
        response = client.post(
            AGENT_EVENT_PATH,
            headers=_agent_headers(),
            json={"events": [_event(event_id, now)]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["inserted"] == 0
    assert server_db.rows("SELECT * FROM events WHERE event_id = ?", (event_id,)) == []


def test_structural_identifiers_reject_commands_paths_and_credentials():
    secret = "sk-ant-" + "A" * 40
    unsafe_values = [
        "cat /Users/koyar/patients/anna_svensson_journal.txt " + secret,
        secret,
        "/Users/koyar/patients/anna_svensson_journal.txt",
    ]
    with TestClient(_isolated_app()) as client:
        for index, unsafe in enumerate(unsafe_values):
            event = _event(f"v084-unsafe-{index}")
            if index == 0:
                event["tool_name"] = unsafe
            elif index == 1:
                event["run_id"] = unsafe
            else:
                event["workflow_id"] = unsafe
            response = client.post(AGENT_EVENT_PATH, headers=_agent_headers(), json={"events": [event]})
            assert response.status_code == 422, response.text

    serialized = json.dumps(server_db.rows("SELECT * FROM events"), ensure_ascii=False)
    assert secret not in serialized
    assert "anna_svensson_journal" not in serialized


def test_agent_timestamps_require_timezone_aware_iso_and_reject_far_future():
    future = datetime.now(timezone.utc) + timedelta(days=365)
    invalid = [
        "yesterday afternoon",
        "2026-09-26T12:00:00",
        future.isoformat(),
    ]
    with TestClient(_isolated_app()) as client:
        for index, observed_at in enumerate(invalid):
            response = client.post(
                AGENT_EVENT_PATH,
                headers=_agent_headers(),
                json={"events": [_event(f"v084-bad-time-{index}", observed_at)]},
            )
            assert response.status_code == 422, response.text

        valid_id = "v084-valid-time"
        valid = client.post(
            AGENT_EVENT_PATH,
            headers=_agent_headers(),
            json={"events": [_event(valid_id, datetime.now(timezone.utc) - timedelta(days=7))]},
        )
        assert valid.status_code == 200, valid.text
        stored = server_db.rows("SELECT * FROM events WHERE event_id = ?", (valid_id,))
        assert len(stored) == 1
        assert str(stored[0]["observed_at"]).endswith("Z")


def test_agent_request_auth_and_size_are_checked_before_json_parsing():
    with TestClient(_isolated_app()) as client:
        # Authentication wins even when the declared body is enormous.
        unauthenticated = client.post(
            AGENT_EVENT_PATH,
            content=b"{}",
            headers={"Content-Length": str(60_000_000)},
        )
        assert unauthenticated.status_code == 401

        # Once authenticated, the same request is rejected from Content-Length
        # before the body is parsed or passed to the event validator.
        oversized = client.post(
            AGENT_EVENT_PATH,
            content=b"{}",
            headers={**_agent_headers(), "Content-Length": str(60_000_000)},
        )
        assert oversized.status_code == 413

        actual_large_body = b"{" + b" " * MAX_AGENT_BATCH_BYTES + b"}"
        streamed = client.post(AGENT_EVENT_PATH, content=actual_large_body, headers=_agent_headers())
        assert streamed.status_code == 413
