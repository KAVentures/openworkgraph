from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from adapters.action_guard import ActionPolicyClient, OptInActionGuard
from adapters.sdk import BufferedAgentEventSink
from server.agent_auth import ensure_agent_ingest_token
from server.local_auth import ensure_api_token
from server.policy_guard_auth import ensure_policy_guard_token


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
    stdout, stderr = process.communicate(timeout=2) if process.poll() is not None else ("", "")
    raise AssertionError(f"secure server did not become ready; stdout={stdout!r} stderr={stderr!r}")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_real_policy_guard_token_is_narrow_and_approval_gate_executes_only_after_recheck(tmp_path, monkeypatch):
    port = _free_port()
    data = tmp_path / "data"
    auth = tmp_path / "auth"
    policy_file = tmp_path / "declared-policies.json"
    policy_file.write_text(json.dumps({
        "schema_version": "1.0",
        "policies": [
            {
                "policy_id": "change-control",
                "version": "4",
                "status": "active",
                "family_key": FAMILY,
                "source_type": "manual_sop",
                "source_ref": "company-sop/change-control",
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
    original_policy = policy_file.read_bytes()

    api_token = ensure_api_token(directory=auth)
    agent_token = ensure_agent_ingest_token(directory=auth)
    guard_token = ensure_policy_guard_token(directory=auth)
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

    api_headers = {"Authorization": f"Bearer {api_token}"}
    agent_headers = {"Authorization": f"Bearer {agent_token}"}
    guard_headers = {"Authorization": f"Bearer {guard_token}"}
    body = {"family_key": FAMILY, "proposed_step": DEPLOY, "completed_steps": []}
    narrow = base + "/policy-guard/v1/action-advisory"
    broad = base + "/v1/declared-policies/action-advisory"

    try:
        # The narrow capability is not interchangeable with either existing token.
        assert httpx.post(narrow, json=body, timeout=5).status_code == 401
        assert httpx.post(narrow, json=body, headers=api_headers, timeout=5).status_code == 401
        assert httpx.post(narrow, json=body, headers=agent_headers, timeout=5).status_code == 401
        scoped = httpx.post(narrow, json=body, headers=guard_headers, timeout=5)
        assert scoped.status_code == 200, scoped.text
        scoped_payload = scoped.json()
        assert scoped_payload["capability_scope"] == "policy_action_advisory_only"
        assert scoped_payload["approval_prerequisite_missing"] is True
        assert scoped_payload["authorization_decision"] == "not_made"
        assert scoped_payload["action_allowed"] is None

        # Policy-guard bearer cannot read observed work or use the write ingress.
        assert httpx.get(base + "/v1/workflow-trace", headers=guard_headers, timeout=5).status_code == 401
        assert httpx.get(base + "/v1/declared-policies", headers=guard_headers, timeout=5).status_code == 401
        assert httpx.get(base + "/v1/agent-workflows", headers=guard_headers, timeout=5).status_code == 401
        assert httpx.post(base + "/agent-ingest/v1/events", json={"events": []}, headers=guard_headers, timeout=5).status_code == 401

        # Existing credentials retain their old contracts too.
        assert httpx.post(broad, json=body, headers=api_headers, timeout=5).status_code == 200
        assert httpx.post(broad, json=body, headers=guard_headers, timeout=5).status_code == 401
        empty_agent_write = httpx.post(
            base + "/agent-ingest/v1/events",
            json={"events": []},
            headers=agent_headers,
            timeout=5,
        )
        assert empty_agent_write.status_code == 200

        monkeypatch.setenv("WORKFLOW_OBSERVER_API", base)
        monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
        monkeypatch.delenv("OWG_API_TOKEN", raising=False)
        monkeypatch.delenv("OWG_POLICY_GUARD_TOKEN", raising=False)
        monkeypatch.delenv("OWG_AGENT_INGEST_TOKEN", raising=False)

        client = ActionPolicyClient(timeout=2)
        initial = client.advisory(family_key=FAMILY, proposed_step=DEPLOY)
        assert initial.approval_prerequisite_missing is True
        assert initial.policy_id == "change-control"

        sink = BufferedAgentEventSink(flush_interval=0.02)
        approvals = []
        executed = []
        guard = OptInActionGuard(
            mode="approval_gate",
            client=client,
            human_approval=lambda request: approvals.append(request) is None or True,
            event_sink=sink,
            event_context={
                "agent_name": "Production Guard Test Agent",
                "provider": "test",
                "framework": "custom",
                "run_id": "private-guard-run-1",
                "trace_id": "private-guard-trace-1",
            },
        )
        result = guard.run(
            lambda: executed.append("ran") or "VERY_SECRET_ACTION_RESULT",
            family_key=FAMILY,
            proposed_step=DEPLOY,
        )
        assert result.executed is True
        assert result.decision.status == "approval_granted"
        assert result.decision.rechecked_advisory is not None
        assert result.decision.rechecked_advisory.approval_prerequisite_missing is False
        assert executed == ["ran"]
        assert len(approvals) == 1
        assert sink.force_flush(timeout=5) is True
        sink.shutdown(timeout=2)

        trace = httpx.get(
            base + "/v1/workflow-trace",
            params={"scope": "all", "limit": 100},
            headers=api_headers,
            timeout=5,
        )
        assert trace.status_code == 200, trace.text
        rows = trace.json().get("rows") or []
        event_types = [row.get("event_type") for row in rows]
        assert "agent_human_approval_requested" in event_types
        assert "agent_human_approval_received" in event_types
        serialized = json.dumps(rows).lower()
        assert "very_secret_action_result" not in serialized
        assert "prompt_content" not in serialized

        # The advisory/gate never edits the declared policy source.
        assert policy_file.read_bytes() == original_policy
    finally:
        _stop(process)
