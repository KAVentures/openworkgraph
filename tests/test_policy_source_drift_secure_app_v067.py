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


def _manifest(version: str) -> dict:
    return {
        "schema_version": "1.0",
        "policies": [{
            "policy_id": "email-approval",
            "version": version,
            "status": "active",
            "family_key": "human:email.send",
            "source_type": "repository_policy",
            "source_ref": "file:///private/company-sop.json",
            "rules": [{
                "rule_id": "approval-before-submit",
                "type": "required_predecessor",
                "required_before": "approval_received:success",
                "trigger_step": "action:submit",
            }],
        }],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_production_drift_read_uses_api_credential_and_is_non_mutating(tmp_path):
    port = _free_port()
    data_dir = tmp_path / "data"
    auth_dir = tmp_path / "auth"
    api_token = ensure_api_token(directory=auth_dir)
    agent_token = ensure_agent_ingest_token(directory=auth_dir)
    assert api_token != agent_token

    active = tmp_path / "policy" / "declared.json"
    sources = tmp_path / "policy-sources.json"
    source = tmp_path / "authoritative" / "policy.json"
    proposals = tmp_path / "proposals"
    receipts = tmp_path / "receipts"
    state = tmp_path / "source-state.json"
    history = tmp_path / "history"
    _write_json(active, _manifest("1"))
    _write_json(source, _manifest("2"))
    _write_json(sources, {
        "schema_version": "1.0",
        "sources": [{"source_id": "email-policy", "type": "local_file", "path": str(source)}],
    })
    active_before = active.read_bytes()

    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data_dir),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth_dir),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-25T00:00:00+00:00",
        "WORKFLOW_OBSERVER_POLICY_FILE": str(active),
        "WORKFLOW_OBSERVER_POLICY_SOURCES_FILE": str(sources),
        "WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR": str(proposals),
        "WORKFLOW_OBSERVER_POLICY_SOURCE_RECEIPT_DIR": str(receipts),
        "WORKFLOW_OBSERVER_POLICY_SOURCE_STATE": str(state),
        "WORKFLOW_OBSERVER_POLICY_HISTORY_DIR": str(history),
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
        url = base + "/v1/declared-policies/source-drift"
        api_headers = {"Authorization": f"Bearer {api_token}"}
        agent_headers = {"Authorization": f"Bearer {agent_token}"}

        assert httpx.get(url).status_code == 401
        assert httpx.get(url, headers=agent_headers).status_code == 401
        response = httpx.get(url, headers=api_headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["read_only"] is True
        assert payload["automatic_activation"] is False
        assert payload["sources"][0]["status"] == "drifted_no_proposal"
        assert payload["sources"][0]["proposal_available"] is False
        serialized = json.dumps(payload)
        assert str(source) not in serialized
        assert "file:///private/company-sop.json" not in serialized

        assert active.read_bytes() == active_before
        assert not proposals.exists()
        assert not receipts.exists()
        assert not state.exists()
    finally:
        _stop(process)
