from __future__ import annotations

import json
from pathlib import Path

from server import enterprise_policy_admin


def _manifest() -> dict:
    return {
        "schema_version": "1.0",
        "policies": [{
            "policy_id": "approval-policy",
            "version": "1",
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


def test_offline_keygen_sign_verify_flow_never_prints_private_key(tmp_path: Path, capsys):
    private_path = tmp_path / "offline" / "signing.pem"
    public_path = tmp_path / "offline" / "public.json"
    manifest_path = tmp_path / "manifest.json"
    bundle_path = tmp_path / "bundle.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

    assert enterprise_policy_admin.main([
        "keygen",
        "--key-id", "acme-root-2026",
        "--private-key", str(private_path),
        "--public-key", str(public_path),
    ]) == 0
    keygen_output = capsys.readouterr().out
    assert "BEGIN PRIVATE KEY" not in keygen_output
    assert "private_key_printed" in keygen_output
    assert private_path.exists()
    assert public_path.exists()
    assert "BEGIN PRIVATE KEY" in private_path.read_text(encoding="utf-8")

    assert enterprise_policy_admin.main([
        "sign", str(manifest_path),
        "--organization-id", "acme",
        "--revision", "1",
        "--key-id", "acme-root-2026",
        "--private-key", str(private_path),
        "--output", str(bundle_path),
    ]) == 0
    sign_output = capsys.readouterr().out
    assert "BEGIN PRIVATE KEY" not in sign_output
    assert bundle_path.exists()

    assert enterprise_policy_admin.main([
        "verify", str(bundle_path),
        "--public-key", str(public_path),
        "--organization-id", "acme",
    ]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified"
    assert verified["signature_verified"] is True
    assert verified["policy_revision"] == 1


def test_keygen_and_sign_refuse_overwrite(tmp_path: Path, capsys):
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.json"
    assert enterprise_policy_admin.main([
        "keygen", "--key-id", "root", "--private-key", str(private_path), "--public-key", str(public_path)
    ]) == 0
    capsys.readouterr()
    assert enterprise_policy_admin.main([
        "keygen", "--key-id", "root", "--private-key", str(private_path), "--public-key", str(tmp_path / "other-public.json")
    ]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err
