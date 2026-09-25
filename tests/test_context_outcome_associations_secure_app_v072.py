from __future__ import annotations

import hashlib
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
BASE = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


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


def _family(workflow_id: str) -> str:
    return "agent:workflow:" + hashlib.sha256(workflow_id.encode("utf-8")).hexdigest()[:16]


def _run_events(run_index: int, *, resolved: bool, success: bool) -> list[dict]:
    run_id = f"private-run-{run_index}"
    trace_id = f"private-trace-{run_index}"
    workflow_id = "private-workflow-stable"
    start = BASE + timedelta(minutes=run_index)
    task_context = {
        "preflight_attempted": True,
        "available": resolved,
        "resolved": resolved,
    }
    if resolved:
        task_context.update({
            "context_sha256": hashlib.sha256(f"context-{run_index}".encode()).hexdigest(),
            "policy_manifest_sha256": hashlib.sha256(b"policy").hexdigest(),
            "family_key": _family(workflow_id),
        })

    common = {
        "agent_name": "Analytics Test Agent",
        "provider": "test",
        "framework": "openai-agents-python",
        "observation_level": "native_trace",
        "run_id": run_id,
        "trace_id": trace_id,
        "workflow_id": workflow_id,
    }
    return [
        {
            **common,
            "event_id": f"start-{run_index}",
            "observed_at": start.isoformat(),
            "operation": "run_started",
            "status": "running",
            "task_context": task_context,
        },
        {
            **common,
            "event_id": f"tool-{run_index}",
            "observed_at": (start + timedelta(seconds=1)).isoformat(),
            "operation": "tool_call",
            "status": "success",
            "tool_name": "repository_search",
            "tool_category": "search",
        },
        {
            **common,
            "event_id": f"finish-{run_index}",
            "observed_at": (start + timedelta(seconds=2)).isoformat(),
            "operation": "run_finished",
            "status": "success" if success else "error",
        },
    ]


def test_production_server_exposes_aggregate_noncausal_analytics_only_to_api_reader(tmp_path):
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
        events = []
        events.extend(_run_events(0, resolved=True, success=True))
        events.extend(_run_events(1, resolved=True, success=True))
        events.extend(_run_events(2, resolved=False, success=False))
        events.extend(_run_events(3, resolved=False, success=False))
        write_headers = {"Authorization": f"Bearer {agent_token}"}
        api_headers = {"Authorization": f"Bearer {api_token}"}

        accepted = httpx.post(
            base + "/agent-ingest/v1/events",
            json={"events": events},
            headers=write_headers,
            timeout=5,
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["inserted"] == len(events)

        endpoint = (
            base
            + "/v1/task-context/outcome-associations"
            + "?min_group_support=2&min_known_outcomes=1&min_stratum_support=1"
        )
        assert httpx.get(endpoint, timeout=5).status_code == 401
        assert httpx.get(endpoint, headers=write_headers, timeout=5).status_code == 401

        response = httpx.get(endpoint, headers=api_headers, timeout=5)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["read_only"] is True
        assert payload["writes_performed"] is False
        assert payload["causal_interpretation"] is False
        assert payload["effect_estimate"] is False
        assert payload["pooled_group_difference_produced"] is False
        assert payload["evidence_is_canonical"] is True
        assert len(payload["strata"]) == 1

        stratum = payload["strata"][0]
        assert stratum["groups"]["context_resolved"]["run_count"] == 2
        assert stratum["groups"]["preflight_unavailable"]["run_count"] == 2
        comparison = next(
            item for item in stratum["comparisons"]
            if item["comparator_group"] == "preflight_unavailable"
        )
        assert comparison["comparison_status"] == "descriptive_only"
        assert comparison["observed_explicit_failure_fraction_difference_pp"] == -100.0

        serialized = json.dumps(payload)
        assert "private-run-" not in serialized
        assert "private-trace-" not in serialized
        assert "private-workflow-stable" not in serialized
        assert hashlib.sha256(b"policy").hexdigest() not in serialized
        assert hashlib.sha256(b"context-0").hexdigest() not in serialized
    finally:
        _stop(process)
