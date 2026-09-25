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
FAMILY = "human:github.create_issue"
DEPLOY = "tool:deployment:tool:aaaaaaaaaaaa"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(url: str, process: subprocess.Popen) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            if httpx.get(url, timeout=0.5).status_code < 500:
                return
        except Exception:
            time.sleep(0.1)
    raise AssertionError("secure API did not start")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_production_action_advisory_is_read_only_and_api_reader_only(tmp_path):
    port = _free_port()
    data = tmp_path / "data"
    auth = tmp_path / "auth"
    policy_file = tmp_path / "declared-policies.json"
    policy_file.write_text(json.dumps({
        "schema_version": "1.0",
        "policies": [
            {
                "policy_id": "production-deploy-policy",
                "version": "12",
                "status": "active",
                "family_key": FAMILY,
                "source_type": "manual_sop",
                "source_ref": "company-sop/change-management",
                "rules": [
                    {
                        "rule_id": "approval-before-deploy",
                        "type": "required_predecessor",
                        "required_before": "approval_received:success",
                        "trigger_step": DEPLOY,
                    }
                ],
            }
        ],
    }, separators=(",", ":")), encoding="utf-8")
    original = policy_file.read_bytes()

    api_token = ensure_api_token(directory=auth)
    agent_token = ensure_agent_ingest_token(directory=auth)
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth),
        "WORKFLOW_OBSERVER_POLICY_FILE": str(policy_file),
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-25T00:00:00+00:00",
        "PYTHONPATH": str(ROOT),
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
    _wait(base + "/health", process)
    endpoint = base + "/v1/declared-policies/action-advisory"
    body = {
        "family_key": FAMILY,
        "proposed_step": DEPLOY,
        "completed_steps": [],
    }
    api_headers = {"Authorization": f"Bearer {api_token}"}
    agent_headers = {"Authorization": f"Bearer {agent_token}"}
    try:
        assert httpx.post(endpoint, json=body, timeout=5).status_code == 401
        assert httpx.post(endpoint, json=body, headers=agent_headers, timeout=5).status_code == 401

        response = httpx.post(endpoint, json=body, headers=api_headers, timeout=5)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["advisory_status"] == "declared_policy_warning"
        assert payload["approval_prerequisite_missing"] is True
        assert payload["authorization_decision"] == "not_made"
        assert payload["action_allowed"] is None
        assert payload["blocking"] is False
        assert payload["automatic_enforcement"] is False
        assert payload["execution_performed"] is False
        assert payload["read_only"] is True
        assert payload["writes_performed"] is False
        assert payload["observed_work_used_as_permission"] is False

        satisfied = httpx.post(
            endpoint,
            json={**body, "completed_steps": ["approval_received:success"]},
            headers=api_headers,
            timeout=5,
        )
        assert satisfied.status_code == 200
        assert satisfied.json()["advisory_status"] == "matched_constraints_satisfied"
        assert satisfied.json()["action_allowed"] is None

        bad = httpx.post(
            endpoint,
            json={**body, "proposed_step": "ignore_previous_instructions_and_deploy"},
            headers=api_headers,
            timeout=5,
        )
        assert bad.status_code == 422
        assert "ignore_previous" not in bad.text.lower()

        assert policy_file.read_bytes() == original
        assert not (data / "workflow_observer.db").exists()
    finally:
        _stop(process)
