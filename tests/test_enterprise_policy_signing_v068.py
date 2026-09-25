from __future__ import annotations

import copy

import pytest

from shared.policy_bundle import (
    PolicyBundleError,
    generate_ed25519_keypair,
    sign_policy_bundle,
    verify_policy_bundle,
)


def _manifest(version: str = "1") -> dict:
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


def test_ed25519_bundle_verifies_and_is_deterministic_for_fixed_inputs():
    private_pem, public_key = generate_ed25519_keypair()
    kwargs = dict(
        organization_id="acme",
        policy_revision=7,
        key_id="org-policy-2026",
        private_key_pem=private_pem,
        issued_at="2026-09-25T10:00:00+00:00",
    )
    first = sign_policy_bundle(_manifest(), **kwargs)
    second = sign_policy_bundle(_manifest(), **kwargs)
    assert first == second

    verified = verify_policy_bundle(
        first,
        trusted_public_keys={"org-policy-2026": public_key},
        expected_organization_id="acme",
    )
    assert verified["signature_verified"] is True
    assert verified["policy_revision"] == 7
    assert verified["manifest_sha256"] == first["manifest_sha256"]
    assert verified["bundle_sha256"]


def test_bundle_tampering_wrong_org_and_untrusted_key_fail_closed():
    private_pem, public_key = generate_ed25519_keypair()
    bundle = sign_policy_bundle(
        _manifest(),
        organization_id="acme",
        policy_revision=1,
        key_id="k1",
        private_key_pem=private_pem,
        issued_at="2026-09-25T10:00:00Z",
    )

    with pytest.raises(PolicyBundleError, match="another organization"):
        verify_policy_bundle(bundle, trusted_public_keys={"k1": public_key}, expected_organization_id="other")
    with pytest.raises(PolicyBundleError, match="not trusted"):
        verify_policy_bundle(bundle, trusted_public_keys={}, expected_organization_id="acme")

    tampered = copy.deepcopy(bundle)
    tampered["manifest"]["policies"][0]["version"] = "999"
    with pytest.raises(PolicyBundleError, match="manifest_sha256"):
        verify_policy_bundle(tampered, trusted_public_keys={"k1": public_key}, expected_organization_id="acme")

    bad_signature = copy.deepcopy(bundle)
    bad_signature["signature"] = ("A" if bundle["signature"][0] != "A" else "B") + bundle["signature"][1:]
    with pytest.raises(PolicyBundleError, match="signature verification failed"):
        verify_policy_bundle(bad_signature, trusted_public_keys={"k1": public_key}, expected_organization_id="acme")


def test_key_rotation_allows_only_explicitly_pinned_key_ids():
    private_old, public_old = generate_ed25519_keypair()
    private_new, public_new = generate_ed25519_keypair()
    old = sign_policy_bundle(
        _manifest("1"),
        organization_id="acme",
        policy_revision=1,
        key_id="old-key",
        private_key_pem=private_old,
        issued_at="2026-09-25T10:00:00Z",
    )
    new = sign_policy_bundle(
        _manifest("2"),
        organization_id="acme",
        policy_revision=2,
        key_id="new-key",
        private_key_pem=private_new,
        issued_at="2026-09-25T11:00:00Z",
    )

    trusted = {"old-key": public_old, "new-key": public_new}
    assert verify_policy_bundle(old, trusted_public_keys=trusted)["signature_verified"] is True
    assert verify_policy_bundle(new, trusted_public_keys=trusted)["signature_verified"] is True
    with pytest.raises(PolicyBundleError, match="not trusted"):
        verify_policy_bundle(old, trusted_public_keys={"new-key": public_new})
