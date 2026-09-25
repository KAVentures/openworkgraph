from __future__ import annotations

from pathlib import Path

import httpx

from connector.config import GatewaySyncSettings
from connector.state import SyncState
from connector.sync import _refresh_managed_policy_if_due
from shared.policy_bundle import generate_ed25519_keypair, sign_policy_bundle


def _manifest() -> dict:
    return {
        "schema_version": "1.0",
        "policies": [{
            "policy_id": "approval-policy",
            "version": "1",
            "status": "active",
            "family_key": "human:email.send",
            "source_type": "repository_policy",
            "source_ref": "repo://policy.json",
            "rules": [{
                "rule_id": "approval-before-submit",
                "type": "required_predecessor",
                "required_before": "approval_received:success",
                "trigger_step": "action:submit",
            }],
        }],
    }


def test_managed_policy_verification_failure_is_recorded_not_raised(tmp_path: Path):
    private_pem, _public_key = generate_ed25519_keypair()
    bundle = sign_policy_bundle(
        _manifest(), organization_id="acme", policy_revision=1, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T10:00:00Z",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/device-declared-policy":
            return httpx.Response(200, json={"organization_id": "acme", "present": True, "bundle": bundle})
        raise AssertionError(f"unexpected request: {request.url}")

    settings = GatewaySyncSettings(
        enabled=True,
        url="https://gateway.example.invalid",
        verify_tls=True,
        batch_size=100,
        poll_seconds=2,
        policy_refresh_seconds=60,
        local_policy={},
        token_file=tmp_path / "token",
        managed_declared_policy_enabled=True,
        managed_declared_policy_organization_id="acme",
        managed_declared_policy_trusted_keys={"root": "definitely-not-a-valid-public-key"},
        managed_declared_policy_refresh_seconds=60,
        managed_declared_policy_target_file=tmp_path / "active-policy.json",
    )
    state = SyncState(tmp_path / "state.db")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        refreshed_at = _refresh_managed_policy_if_due(
            client,
            settings=settings,
            state=state,
            data_dir=tmp_path,
            fetched_at=0.0,
            now=100.0,
        )
    assert refreshed_at == 100.0
    assert state.get("managed_declared_policy_status") == "error"
    assert state.get("managed_declared_policy_last_error")
    assert not settings.managed_declared_policy_target_file.exists()


def test_disabled_managed_policy_performs_no_request(tmp_path: Path):
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    settings = GatewaySyncSettings(
        enabled=True,
        url="https://gateway.example.invalid",
        verify_tls=True,
        batch_size=100,
        poll_seconds=2,
        policy_refresh_seconds=60,
        local_policy={},
        token_file=tmp_path / "token",
    )
    state = SyncState(tmp_path / "state.db")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        value = _refresh_managed_policy_if_due(
            client,
            settings=settings,
            state=state,
            data_dir=tmp_path,
            fetched_at=0.0,
            now=100.0,
        )
    assert value == 0.0
    assert requests == 0
