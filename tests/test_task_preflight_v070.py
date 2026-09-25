from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from adapters.task_preflight import (
    TaskPreflightClient,
    TaskPreflightError,
    _base_url,
)
from server.agent_auth import ensure_agent_ingest_token
from server.local_auth import ensure_api_token


ROOT = Path(__file__).resolve().parents[1]


def _payload(*, policy: bool = True) -> dict:
    return {
        "schema_version": "1.0",
        "resolution": {
            "status": "resolved",
            "mode": "explicit_family_key",
            "family_key": "human:github.create_issue",
            "family_known": True,
        },
        "authority_model": {
            "declared_policy": "normative_input_when_active",
            "observed_procedure": "non_authoritative_derived_evidence",
            "policy_observation_comparison": "derived_assessment_not_enforcement",
            "family_resolution": "derived_routing_only",
            "policy_inferred_from_behavior": False,
            "observed_behavior_becomes_policy": False,
            "automatic_execution": False,
            "automatic_policy_enforcement": False,
        },
        "read_only": True,
        "writes_performed": False,
        "context_available": True,
        "task_context": {
            "family_key": "human:github.create_issue",
            "policy": {
                "status": "active" if policy else "not_declared",
                "authority_class": "declared_normative" if policy else "none",
                "authoritative_as_declared_input": policy,
                "manifest_sha256": "a" * 64 if policy else None,
                "item": {"policy_id": "github-review"} if policy else None,
            },
            "observed_procedure": {
                "similar_runs": [{"execution_id": "exec:abc"}],
                "failure_patterns": [{"pattern_id": "failure:1"}, {"pattern_id": "failure:2"}],
                "next_observed_steps": [{"step": "action:submit"}],
                "approval_patterns": [{"pattern_id": "approval:1"}],
                "authority_class": "observed_evidence",
                "authoritative": False,
                "prescriptive": False,
            },
            "policy_observation_comparison": {
                "results": [
                    {"status": "potential_divergence"},
                    {"status": "insufficient_observation"},
                    {"status": "potential_divergence"},
                ]
            },
            "provenance": {"evidence_is_canonical": True},
        },
        "evidence_rows_considered": 8,
        "evidence_is_canonical": True,
    }


def test_preflight_summarizes_without_recommending_or_enforcing():
    client = TaskPreflightClient(fetcher=lambda params: _payload())
    result = client.preflight(family_key="human:github.create_issue")

    assert result.available is True
    assert result.context_resolved is True
    assert result.family_key == "human:github.create_issue"
    assert result.policy_status == "active"
    assert result.policy_present is True
    assert result.policy_authoritative_as_declared_input is True
    assert result.observed_procedure_authoritative is False
    assert result.potential_divergence_count == 2
    assert result.observed_failure_pattern_count == 2
    assert result.approval_pattern_count == 1
    assert result.similar_run_count == 1
    assert result.next_observed_step_count == 1
    assert result.automatic_enforcement is False
    assert result.automatic_execution is False
    assert len(result.context_sha256 or "") == 64
    assert result.policy_manifest_sha256 == "a" * 64

    repeat = client.preflight(family_key="human:github.create_issue")
    assert repeat.context_sha256 == result.context_sha256

    changed = _payload()
    changed["evidence_rows_considered"] = 9
    changed_result = TaskPreflightClient(fetcher=lambda params: changed).preflight(
        family_key="human:github.create_issue"
    )
    assert changed_result.context_sha256 != result.context_sha256

    blob = json.dumps(result.as_dict()).lower()
    assert "recommended_action" not in blob
    assert "allow_action" not in blob
    assert "deny_action" not in blob
    assert "system_prompt" not in blob


def test_try_preflight_fails_open_only_for_observer_unavailability():
    def unavailable(_params):
        raise OSError("SUPERSECRET internal network failure patient@example.com")

    result = TaskPreflightClient(fetcher=unavailable).try_preflight(
        family_key="human:github.create_issue"
    )
    assert result.available is False
    assert result.context is None
    assert result.context_sha256 is None
    assert result.policy_manifest_sha256 is None
    assert result.error_code == "observer_unavailable"
    assert "SUPERSECRET" not in json.dumps(result.as_dict())


def test_invalid_input_does_not_silently_fail_open():
    client = TaskPreflightClient(fetcher=lambda params: _payload())
    with pytest.raises(TaskPreflightError):
        client.try_preflight(
            family_key="human:github.create_issue",
            current_steps=["surface:github"] * 49,
        )
    with pytest.raises(TaskPreflightError):
        client.try_preflight(
            family_key="human:github.create_issue",
            current_steps=["surface:github,action:click"],
        )


def test_preflight_rejects_authority_contract_regression():
    payload = _payload()
    payload["authority_model"]["automatic_policy_enforcement"] = True
    with pytest.raises(TaskPreflightError):
        TaskPreflightClient(fetcher=lambda params: payload).preflight(
            family_key="human:github.create_issue"
        )

    payload = _payload()
    payload["task_context"]["observed_procedure"]["authoritative"] = True
    with pytest.raises(TaskPreflightError):
        TaskPreflightClient(fetcher=lambda params: payload).preflight(
            family_key="human:github.create_issue"
        )


def test_preflight_query_is_bounded_before_custom_transport():
    seen = {}

    def fetcher(params):
        seen.update(params)
        return _payload(policy=False)

    result = TaskPreflightClient(fetcher=fetcher).preflight(
        family_key="human:github.create_issue",
        min_support=999,
        run_limit=999,
        section_limit=999,
        max_steps_per_run=999,
        max_evidence_refs_per_item=999,
    )
    assert result.policy_status == "not_declared"
    assert result.policy_manifest_sha256 is None
    assert seen["min_support"] == 100
    assert seen["run_limit"] == 5
    assert seen["section_limit"] == 5
    assert seen["max_steps_per_run"] == 24
    assert seen["max_evidence_refs_per_item"] == 4


def test_remote_preflight_is_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_API", "https://example.com:8443")
    monkeypatch.delenv("OWG_PREFLIGHT_ALLOW_REMOTE", raising=False)
    with pytest.raises(TaskPreflightError):
        _base_url()
    monkeypatch.setenv("OWG_PREFLIGHT_ALLOW_REMOTE", "1")
    base, local = _base_url()
    assert base == "https://example.com:8443"
    assert local is False


def _free_port() -> int:
    with socket.socket() as sock:
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
            process.kill()
            process.wait(timeout=5)


def test_default_client_uses_real_local_read_auth_not_agent_write_token(tmp_path, monkeypatch):
    port = _free_port()
    data = tmp_path / "data"
    auth = tmp_path / "auth"
    api_token = ensure_api_token(directory=auth)
    agent_token = ensure_agent_ingest_token(directory=auth)
    assert api_token != agent_token

    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(data),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(auth),
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-25T12:00:00+00:00",
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
    try:
        monkeypatch.setenv("WORKFLOW_OBSERVER_API", base)
        monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
        monkeypatch.delenv("OWG_API_TOKEN", raising=False)
        result = TaskPreflightClient(timeout=2).preflight(
            family_key="human:github.create_issue"
        )
        assert result.available is True
        assert result.context_resolved is True
        assert result.policy_status == "not_declared"
        assert result.automatic_enforcement is False
        assert len(result.context_sha256 or "") == 64

        monkeypatch.setenv("OWG_API_TOKEN", agent_token)
        with pytest.raises(TaskPreflightError):
            TaskPreflightClient(timeout=2).try_preflight(
                family_key="human:github.create_issue"
            )
    finally:
        _stop(process)
