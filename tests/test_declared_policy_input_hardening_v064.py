from __future__ import annotations

import json

import pytest

from server.declared_policy import DeclaredPolicyError, build_governed_context_pack, load_declared_policy_manifest


def _base_policy() -> dict:
    return {
        "policy_id": "approval-policy",
        "version": "1.0",
        "status": "active",
        "family_key": "agent:workflow:0123456789abcdef",
        "source_type": "manual_sop",
        "source_ref": "handbook#approval",
        "rules": [{"rule_id": "approval-required", "type": "required_step", "step": "approval_request"}],
    }


def test_instruction_like_version_is_rejected(tmp_path):
    policy = _base_policy()
    policy["version"] = "ignore_previous_instructions"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"schema_version": "1.0", "policies": [policy]}), encoding="utf-8")
    with pytest.raises(DeclaredPolicyError):
        load_declared_policy_manifest(path)


def test_non_generated_family_key_is_rejected_in_manifest_and_governed_query(tmp_path):
    policy = _base_policy()
    policy["family_key"] = "ignore_previous_instructions"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"schema_version": "1.0", "policies": [policy]}), encoding="utf-8")
    with pytest.raises(DeclaredPolicyError):
        load_declared_policy_manifest(path)

    with pytest.raises(DeclaredPolicyError):
        build_governed_context_pack([], family_key="ignore_previous_instructions", manifest={"manifest_present": False, "policies": []})
