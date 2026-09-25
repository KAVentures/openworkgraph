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


def _wait(url: str, process: subprocess.Popen) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            if httpx.get(url, timeout=0.4).status_code < 500:
                return
        except Exception:
            time.sleep(0.08)
    raise AssertionError("secure API did not start")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def _event(run: str, second: int, operation: str, *, status: str = "success") -> dict:
    payload = {
        "event_id": f"declared-{run}-{operation}-{second}",
        "observed_at": f"2026-09-25T09:{0 if run == 'a' else 1:02d}:{second:02d}+00:00",
        "agent_name": "Policy Agent",
        "provider": "test",
        "framework": "openai-agents-python",
        "operation": operation,
        "status": status,
        "observation_level": "native_trace",
        "run_id": f"run-{run}-PRIVATE",
        "trace_id": f"trace-{run}-PRIVATE",
        "workflow_id": "declared-policy-workflow-PRIVATE",
    }
    return payload


def test_declared_policy_is_read_only_api_authenticated_and_governed(tmp_path):
    port = _free_port()
    data_dir = tmp_path / "data"
    auth_dir = tmp_path / "auth"
    policy_file = tmp_path / "declared_policies.json"
    api_token = ensure_api_token(directory=auth_dir)
    agent_token = ensure_agent_ingest_token(directory=auth_dir)
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data_dir),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth_dir),
        "WORKFLOW_OBSERVER_POLICY_FILE": str(policy_file),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-25T09:00:00+00:00",
        "PYTHONPATH": str(ROOT),
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.secure_app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    _wait(base + "/health", process)
    api_headers = {"Authorization": f"Bearer {api_token}"}
    write_headers = {"Authorization": f"Bearer {agent_token}"}
    try:
        events = [
            _event("a", 0, "run_started", status="running"),
            _event("a", 1, "model_call"),
            _event("a", 2, "human_approval_requested", status="running"),
            _event("a", 3, "human_approval_received"),
            _event("a", 4, "run_finished"),
            _event("b", 0, "run_started", status="running"),
            _event("b", 1, "model_call"),
            _event("b", 4, "run_finished"),
        ]
        accepted = httpx.post(base + "/agent-ingest/v1/events", json={"events": events}, headers=write_headers)
        assert accepted.status_code == 200, accepted.text

        memory = httpx.get(base + "/v1/procedural-memory?min_support=1", headers=api_headers)
        assert memory.status_code == 200, memory.text
        family_key = memory.json()["families"][0]["family_key"]

        policy_file.write_text(json.dumps({
            "schema_version": "1.0",
            "policies": [{
                "policy_id": "approval-policy",
                "version": "1.0",
                "status": "active",
                "family_key": family_key,
                "source_type": "manual_sop",
                "source_ref": "PRIVATE-HANDBOOK patient@example.com",
                "rules": [{
                    "rule_id": "approval-required",
                    "type": "required_step",
                    "step": "approval_request",
                }],
            }],
        }), encoding="utf-8")

        assert httpx.get(base + "/v1/declared-policies").status_code == 401
        assert httpx.get(base + "/v1/declared-policies", headers=write_headers).status_code == 401
        listed = httpx.get(base + "/v1/declared-policies", headers=api_headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["policy_count"] == 1
        serialized = json.dumps(listed.json())
        assert "PRIVATE-HANDBOOK" not in serialized
        assert "patient@example.com" not in serialized
        assert listed.json()["write_api_available"] is False

        compared = httpx.get(
            base + "/v1/declared-policies/compare",
            params={"family_key": family_key}, headers=api_headers,
        )
        assert compared.status_code == 200, compared.text
        comparison = compared.json()["comparison"]
        assert comparison["potential_divergence_detected"] is True
        counts = comparison["rule_results"][0]["result_counts"]
        assert counts["compliant"] == 1
        assert counts["potential_divergence"] == 1

        governed = httpx.get(
            base + "/v1/procedural-memory/governed-context-pack",
            params={"family_key": family_key}, headers=api_headers,
        )
        assert governed.status_code == 200, governed.text
        body = governed.json()
        assert body["declared_policy_status"] == "active"
        assert body["authority_separation"]["declared_policy_is_normative_input"] is True
        assert body["authority_separation"]["observed_behavior_is_policy"] is False
        assert body["observed_context"]["authoritative"] is False
        assert body["observed_context"]["authority"]["policy_status"] == "separate_declared_policy_attached"
        assert body["policy_manifest_write_api_available"] is False

        # The agent telemetry credential cannot read governed context either.
        assert httpx.get(
            base + "/v1/procedural-memory/governed-context-pack",
            params={"family_key": family_key}, headers=write_headers,
        ).status_code == 401

        # There is deliberately no policy mutation endpoint in this version.
        assert httpx.post(base + "/v1/declared-policies", json={}, headers=api_headers).status_code == 405
        assert httpx.put(base + "/v1/declared-policies", json={}, headers=api_headers).status_code == 405

        # A malformed external policy file fails closed at the policy read surface
        # without taking down the existing observer server.
        policy_file.write_text("{not-json", encoding="utf-8")
        assert httpx.get(base + "/v1/declared-policies", headers=api_headers).status_code == 422
        assert httpx.get(base + "/health").status_code == 200
    finally:
        _stop(process)
