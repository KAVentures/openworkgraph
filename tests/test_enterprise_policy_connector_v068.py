from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

from connector.config import GatewaySyncSettings
from connector.declared_policy import ManagedDeclaredPolicyError, refresh_managed_declared_policy
from connector.state import SyncState
from server.declared_policy import load_declared_policy_manifest
from shared.policy_bundle import generate_ed25519_keypair, inspect_policy_bundle, sign_policy_bundle


def _manifest(version: str) -> dict:
    return {
        "schema_version": "1.0",
        "policies": [{
            "policy_id": "approval-policy",
            "version": version,
            "status": "active",
            "family_key": "human:email.send",
            "source_type": "repository_policy",
            "source_ref": "repo://policies/approval.json",
            "rules": [{
                "rule_id": "approval-before-submit",
                "type": "required_predecessor",
                "required_before": "approval_received:success",
                "trigger_step": "action:submit",
            }],
        }],
    }


def _settings(tmp_path: Path, public_key: str, *, enabled: bool = True) -> GatewaySyncSettings:
    return GatewaySyncSettings(
        enabled=True,
        url="https://gateway.example.invalid",
        verify_tls=True,
        batch_size=100,
        poll_seconds=2,
        policy_refresh_seconds=60,
        local_policy={},
        token_file=tmp_path / "token",
        managed_declared_policy_enabled=enabled,
        managed_declared_policy_organization_id="acme",
        managed_declared_policy_trusted_keys={"root": public_key},
        managed_declared_policy_refresh_seconds=60,
        managed_declared_policy_target_file=tmp_path / "declared.json",
    )


def _client_for(bundle: dict | None, *, organization_id: str = "acme") -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/device-declared-policy"
        if bundle is None:
            return httpx.Response(200, json={"organization_id": organization_id, "present": False, "bundle": None})
        inspected = inspect_policy_bundle(bundle)
        return httpx.Response(200, json={
            "organization_id": organization_id,
            "present": True,
            "policy_revision": inspected["policy_revision"],
            "bundle_sha256": inspected["bundle_sha256"],
            "manifest_sha256": inspected["manifest_sha256"],
            "bundle": bundle,
        })
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_verified_bundle_applies_and_local_tamper_is_repaired(tmp_path):
    private_pem, public_key = generate_ed25519_keypair()
    bundle = sign_policy_bundle(
        _manifest("2"), organization_id="acme", policy_revision=2, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T10:00:00Z",
    )
    settings = _settings(tmp_path, public_key)
    state = SyncState(tmp_path / "state.db")
    with _client_for(bundle) as client:
        first = refresh_managed_declared_policy(
            client, settings.url, settings=settings, state=state, data_dir=tmp_path,
        )
        assert first["status"] == "applied"
        assert first["signature_verified"] is True
        assert first["policy_revision"] == 2
        assert first["hardware_backed_rollback_protection"] is False

        loaded = load_declared_policy_manifest(settings.managed_declared_policy_target_file)
        assert loaded["policies"][0]["version"] == "2"
        assert state.get_int("managed_declared_policy_revision") == 2

        # Managed mode is authoritative only because it was explicitly enabled.
        # If the active file drifts locally, the same verified signed bundle repairs it.
        settings.managed_declared_policy_target_file.write_text(
            json.dumps(_manifest("local-edit")), encoding="utf-8"
        )
        repaired = refresh_managed_declared_policy(
            client, settings.url, settings=settings, state=state, data_dir=tmp_path,
        )
        assert repaired["status"] == "repaired"
        assert repaired["repaired_local_drift"] is True
        assert load_declared_policy_manifest(settings.managed_declared_policy_target_file)["policies"][0]["version"] == "2"


def test_endpoint_rejects_rollback_and_same_revision_equivocation(tmp_path):
    private_pem, public_key = generate_ed25519_keypair()
    rev2 = sign_policy_bundle(
        _manifest("2"), organization_id="acme", policy_revision=2, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T10:00:00Z",
    )
    rev1 = sign_policy_bundle(
        _manifest("1"), organization_id="acme", policy_revision=1, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T09:00:00Z",
    )
    rev2_other = sign_policy_bundle(
        _manifest("2b"), organization_id="acme", policy_revision=2, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T10:01:00Z",
    )
    settings = _settings(tmp_path, public_key)
    state = SyncState(tmp_path / "state.db")

    with _client_for(rev2) as client:
        refresh_managed_declared_policy(client, settings.url, settings=settings, state=state, data_dir=tmp_path)
    before = settings.managed_declared_policy_target_file.read_bytes()

    with _client_for(rev1) as client, pytest.raises(ManagedDeclaredPolicyError, match="rollback"):
        refresh_managed_declared_policy(client, settings.url, settings=settings, state=state, data_dir=tmp_path)
    assert settings.managed_declared_policy_target_file.read_bytes() == before
    assert state.get_int("managed_declared_policy_revision") == 2

    with _client_for(rev2_other) as client, pytest.raises(ManagedDeclaredPolicyError, match="changed signed content"):
        refresh_managed_declared_policy(client, settings.url, settings=settings, state=state, data_dir=tmp_path)
    assert settings.managed_declared_policy_target_file.read_bytes() == before


def test_invalid_signature_wrong_org_and_disabled_mode_never_mutate_target(tmp_path):
    private_pem, public_key = generate_ed25519_keypair()
    bundle = sign_policy_bundle(
        _manifest("1"), organization_id="acme", policy_revision=1, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T10:00:00Z",
    )
    bad = copy.deepcopy(bundle)
    bad["signature"] = ("A" if bad["signature"][0] != "A" else "B") + bad["signature"][1:]
    settings = _settings(tmp_path, public_key)
    state = SyncState(tmp_path / "state.db")

    with _client_for(bad) as client, pytest.raises(ManagedDeclaredPolicyError, match="signature"):
        refresh_managed_declared_policy(client, settings.url, settings=settings, state=state, data_dir=tmp_path)
    assert not settings.managed_declared_policy_target_file.exists()

    with _client_for(bundle, organization_id="other") as client, pytest.raises(ManagedDeclaredPolicyError, match="another organization"):
        refresh_managed_declared_policy(client, settings.url, settings=settings, state=state, data_dir=tmp_path)
    assert not settings.managed_declared_policy_target_file.exists()

    disabled = _settings(tmp_path, public_key, enabled=False)
    with _client_for(bundle) as client:
        result = refresh_managed_declared_policy(client, disabled.url, settings=disabled, state=state, data_dir=tmp_path)
    assert result == {"status": "disabled", "changed": False}
    assert not disabled.managed_declared_policy_target_file.exists()


def test_not_published_keeps_existing_verified_policy(tmp_path):
    private_pem, public_key = generate_ed25519_keypair()
    bundle = sign_policy_bundle(
        _manifest("1"), organization_id="acme", policy_revision=1, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T10:00:00Z",
    )
    settings = _settings(tmp_path, public_key)
    state = SyncState(tmp_path / "state.db")
    with _client_for(bundle) as client:
        refresh_managed_declared_policy(client, settings.url, settings=settings, state=state, data_dir=tmp_path)
    before = settings.managed_declared_policy_target_file.read_bytes()
    with _client_for(None) as client:
        result = refresh_managed_declared_policy(client, settings.url, settings=settings, state=state, data_dir=tmp_path)
    assert result["status"] == "not_published"
    assert settings.managed_declared_policy_target_file.read_bytes() == before
    assert state.get_int("managed_declared_policy_revision") == 1
