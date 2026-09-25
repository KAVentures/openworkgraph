from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from adapters.action_guard import ActionPolicyClient
from adapters.policy_bound_approval import PolicyBoundApprovalGuard
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


def _policy(version: str) -> dict:
    return {
        "schema_version": "1.0",
        "policies": [{
            "policy_id": "change-control",
            "version": version,
            "status": "active",
            "family_key": FAMILY,
            "source_type": "manual_sop",
            "source_ref": f"company-sop/change-control/{version}",
            "rules": [{
                "rule_id": "approval-before-deploy",
                "type": "required_predecessor",
                "required_before": "approval_received:success",
                "trigger_step": DEPLOY,
            }],
        }],
    }


def _write_policy(path: Path, version: str) -> None:
    path.write_text(json.dumps(_policy(version), separators=(",", ":")), encoding="utf-8")


def test_real_policy_change_during_human_approval_invalidates_old_snapshot(tmp_path, monkeypatch):
    port = _free_port()
    data = tmp_path / "data"
    auth = tmp_path / "auth"
    policy_file = tmp_path / "declared-policies.json"
    _write_policy(policy_file, "3")
    ensure_policy_guard_token(directory=auth)

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

    monkeypatch.setenv("WORKFLOW_OBSERVER_API", base)
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    monkeypatch.delenv("OWG_POLICY_GUARD_TOKEN", raising=False)

    try:
        actions: list[str] = []
        approvals: list[dict] = []

        def approve_then_policy_changes(request) -> bool:
            approvals.append(request.as_dict())
            _write_policy(policy_file, "4")
            return True

        result = PolicyBoundApprovalGuard(
            client=ActionPolicyClient(timeout=2),
            human_approval=approve_then_policy_changes,
        ).run(
            lambda: actions.append("ran") or "SHOULD_NOT_LEAVE_CALLER",
            family_key=FAMILY,
            proposed_step=DEPLOY,
        )
        assert result.executed is False
        assert actions == []
        assert approvals and approvals[0]["policy_version"] == "3"
        assert result.decision.receipt is not None
        assert result.decision.receipt.policy_version == "3"
        assert result.decision.status == "paused_stale_approval_receipt"
        assert "policy_version_changed_after_approval" in result.decision.reason_codes
        assert result.decision.final_advisory is not None
        assert result.decision.final_advisory.policy_version == "4"
        assert "should_not_leave_caller" not in json.dumps(result.decision.as_dict()).lower()

        # Restoring the original snapshot and approving normally preserves #65's
        # successful execution path, with the extra receipt/final-read check only.
        _write_policy(policy_file, "3")
        stable_actions: list[str] = []
        stable = PolicyBoundApprovalGuard(
            client=ActionPolicyClient(timeout=2),
            human_approval=lambda request: True,
        ).run(
            lambda: stable_actions.append("ran") or "caller-only-result",
            family_key=FAMILY,
            proposed_step=DEPLOY,
        )
        assert stable.executed is True
        assert stable_actions == ["ran"]
        assert stable.decision.status == "approval_receipt_verified"
        assert stable.decision.receipt is not None
        assert stable.decision.receipt.policy_version == "3"
        assert "caller-only-result" not in json.dumps(stable.decision.as_dict()).lower()
    finally:
        _stop(process)
