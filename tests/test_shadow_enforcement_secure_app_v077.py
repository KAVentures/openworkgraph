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
BASE = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)


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


def _run_events(run_index: int, *, disposition: str, success: bool, repeat_preview: bool = False) -> list[dict]:
    run_id = f"shadow-private-run-{run_index}"
    trace_id = f"shadow-private-trace-{run_index}"
    workflow_id = "shadow-private-workflow-stable"
    policy_sha = hashlib.sha256(b"shadow-enterprise-policy").hexdigest()
    start = BASE + timedelta(minutes=run_index)
    common = {
        "agent_name": "Shadow Analytics Agent",
        "provider": "test",
        "framework": "openai-agents-python",
        "observation_level": "native_trace",
        "run_id": run_id,
        "trace_id": trace_id,
        "workflow_id": workflow_id,
    }
    shadow = {
        "profile_id": "declared-policy-shadow-v1",
        "available": True,
        "candidate_disposition": disposition,
        "family_key": _family(workflow_id),
        "policy_manifest_sha256": policy_sha,
        "simulated_only": True,
        "actual_enforcement_enabled": False,
        "actual_blocking": False,
    }
    events = [
        {
            **common,
            "event_id": f"shadow-start-{run_index}",
            "observed_at": start.isoformat(),
            "operation": "run_started",
            "status": "running",
        },
        {
            **common,
            "event_id": f"shadow-tool-{run_index}",
            "observed_at": (start + timedelta(seconds=1)).isoformat(),
            "operation": "tool_call",
            "status": "success",
            "tool_name": "deploy_prod",
            "tool_category": "deployment",
            "shadow_enforcement": shadow,
        },
    ]
    if repeat_preview:
        events.append({
            **common,
            "event_id": f"shadow-tool-repeat-{run_index}",
            "observed_at": (start + timedelta(seconds=2)).isoformat(),
            "operation": "tool_call",
            "status": "success",
            "tool_name": "deploy_prod_again",
            "tool_category": "deployment",
            "shadow_enforcement": shadow,
        })
    events.append({
        **common,
        "event_id": f"shadow-finish-{run_index}",
        "observed_at": (start + timedelta(seconds=3)).isoformat(),
        "operation": "run_finished",
        "status": "success" if success else "error",
    })
    return events


def test_production_shadow_analytics_are_aggregate_read_only_and_write_token_cannot_read(tmp_path):
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
        events: list[dict] = []
        events.extend(_run_events(0, disposition="candidate_deny", success=True, repeat_preview=True))
        events.extend(_run_events(1, disposition="candidate_deny", success=False))
        events.extend(_run_events(2, disposition="no_blocking_condition_observed", success=True))
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

        endpoint = base + "/v1/shadow-enforcement/outcome-associations?min_stratum_support=1"
        assert httpx.get(endpoint, timeout=5).status_code == 401
        assert httpx.get(endpoint, headers=write_headers, timeout=5).status_code == 401

        response = httpx.get(endpoint, headers=api_headers, timeout=5)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["read_only"] is True
        assert payload["writes_performed"] is False
        assert payload["evidence_is_canonical"] is True
        assert payload["actual_enforcement_enabled"] is False
        assert payload["actual_blocking_observed"] is False
        assert payload["causal_interpretation"] is False
        assert payload["effect_estimate"] is False
        assert payload["false_positive_rate_estimate"] is False
        assert payload["preview_event_count_considered"] == 4
        assert payload["run_count_with_shadow_preview"] == 3

        deny = payload["disposition_run_outcomes"]["candidate_deny"]
        assert deny["preview_event_count"] == 3
        assert deny["run_count"] == 2
        assert deny["explicit_success_run_count"] == 1
        assert deny["explicit_failure_run_count"] == 1
        assert deny["false_positive_interpretation"] is False

        serialized = json.dumps(payload)
        assert "shadow-private-run-" not in serialized
        assert "shadow-private-trace-" not in serialized
        assert "shadow-private-workflow-stable" not in serialized
        assert hashlib.sha256(b"shadow-enterprise-policy").hexdigest() not in serialized
        assert "deploy_prod" not in serialized

        invalid = httpx.get(
            base + "/v1/shadow-enforcement/outcome-associations?family_key=IGNORE%20PREVIOUS%20INSTRUCTIONS",
            headers=api_headers,
            timeout=5,
        )
        assert invalid.status_code == 422
        assert "IGNORE PREVIOUS" not in invalid.text
    finally:
        _stop(process)
