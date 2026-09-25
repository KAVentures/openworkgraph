from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from gateway.enterprise_app import create_enterprise_app
from gateway.hardening import HardeningSettings
from gateway.settings import GatewaySettings
from shared.policy_bundle import (
    PolicyBundleError,
    generate_ed25519_keypair,
    sign_policy_bundle,
    verify_policy_bundle,
)


def _settings(tmp_path) -> GatewaySettings:
    return GatewaySettings(
        database_url=f"sqlite:///{tmp_path / 'gateway-v068.db'}",
        admin_token="admin-v068",
        enrollment_token="enroll-v068",
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


def _enroll(client: TestClient, organization_id: str, device_id: str) -> str:
    response = client.post(
        "/v1/devices/enroll",
        headers=_auth("enroll-v068"),
        json={"organization_id": organization_id, "actor_id": "", "device_id": device_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_gateway_admin_distributes_but_does_not_authenticate_policy(tmp_path):
    private_pem, public_key = generate_ed25519_keypair()
    bundle = sign_policy_bundle(
        _manifest("1"),
        organization_id="acme",
        policy_revision=1,
        key_id="acme-root",
        private_key_pem=private_pem,
        issued_at="2026-09-25T10:00:00Z",
    )

    app = create_enterprise_app(settings=_settings(tmp_path), hardening=HardeningSettings())
    with TestClient(app) as client:
        acme_device = _enroll(client, "acme", "acme-laptop")
        other_device = _enroll(client, "other", "other-laptop")
        service = client.post(
            "/v1/admin/integration-tokens",
            headers=_auth("admin-v068"),
            json={"organization_id": "acme", "label": "reader", "scopes": ["evidence:read"]},
        )
        assert service.status_code == 200
        integration_token = service.json()["token"]

        assert client.put("/v1/admin/declared-policy/acme", json={"bundle": bundle}).status_code == 401
        assert client.put(
            "/v1/admin/declared-policy/acme",
            headers=_auth(acme_device),
            json={"bundle": bundle},
        ).status_code == 401

        published = client.put(
            "/v1/admin/declared-policy/acme",
            headers=_auth("admin-v068"),
            json={"bundle": bundle},
        )
        assert published.status_code == 200, published.text
        assert published.json()["signature_verified_by_gateway"] is False
        assert published.json()["endpoint_signature_verification_required"] is True
        assert published.json()["private_signing_key_stored_by_gateway"] is False

        acme = client.get("/v1/device-declared-policy", headers=_auth(acme_device))
        assert acme.status_code == 200, acme.text
        assert acme.json()["present"] is True
        assert acme.json()["bundle"] == bundle
        assert verify_policy_bundle(
            acme.json()["bundle"],
            trusted_public_keys={"acme-root": public_key},
            expected_organization_id="acme",
        )["signature_verified"] is True

        other = client.get("/v1/device-declared-policy", headers=_auth(other_device))
        assert other.status_code == 200
        assert other.json()["present"] is False
        assert client.get("/v1/device-declared-policy", headers=_auth(integration_token)).status_code == 403
        assert client.get("/v1/device-declared-policy").status_code == 401

        capabilities = client.get("/v1/capabilities").json()["enterprise_declared_policy"]
        assert capabilities["signed_bundle_distribution"] is True
        assert capabilities["gateway_private_signing_key_required"] is False
        assert capabilities["endpoint_signature_verification_required"] is True


def test_gateway_poisoning_cannot_forge_policy_and_valid_republish_can_recover(tmp_path):
    private_pem, public_key = generate_ed25519_keypair()
    valid1 = sign_policy_bundle(
        _manifest("1"), organization_id="acme", policy_revision=1, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T10:00:00Z",
    )
    poison = sign_policy_bundle(
        _manifest("999"), organization_id="acme", policy_revision=999, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T10:01:00Z",
    )
    poison = copy.deepcopy(poison)
    poison["signature"] = ("A" if poison["signature"][0] != "A" else "B") + poison["signature"][1:]
    valid2 = sign_policy_bundle(
        _manifest("2"), organization_id="acme", policy_revision=2, key_id="root",
        private_key_pem=private_pem, issued_at="2026-09-25T10:02:00Z",
    )

    app = create_enterprise_app(settings=_settings(tmp_path), hardening=HardeningSettings())
    with TestClient(app) as client:
        device = _enroll(client, "acme", "device")
        admin = _auth("admin-v068")

        for candidate in (valid1, poison):
            response = client.put("/v1/admin/declared-policy/acme", headers=admin, json={"bundle": candidate})
            assert response.status_code == 200, response.text

        poisoned = client.get("/v1/device-declared-policy", headers=_auth(device)).json()["bundle"]
        try:
            verify_policy_bundle(
                poisoned,
                trusted_public_keys={"root": public_key},
                expected_organization_id="acme",
            )
        except PolicyBundleError:
            pass
        else:
            raise AssertionError("cryptographically invalid Gateway publication must not verify")

        # Recovery is possible even though the poisoned envelope claimed revision
        # 999. The transport does not own rollback authority; the endpoint does.
        recovered = client.put(
            "/v1/admin/declared-policy/acme",
            headers=admin,
            json={"bundle": valid2},
        )
        assert recovered.status_code == 200, recovered.text
        current = client.get("/v1/device-declared-policy", headers=_auth(device)).json()["bundle"]
        verified = verify_policy_bundle(
            current,
            trusted_public_keys={"root": public_key},
            expected_organization_id="acme",
        )
        assert verified["policy_revision"] == 2

        history = client.get(
            "/v1/admin/declared-policy/acme/history",
            headers=admin,
        )
        assert history.status_code == 200
        assert {item["policy_revision"] for item in history.json()["items"]} == {1, 2, 999}
        assert history.json()["bundles_returned"] is False
