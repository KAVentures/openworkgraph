from __future__ import annotations

import json

import pytest

import server.procedural_memory as pm
from server.declared_policy import (
    DeclaredPolicyError,
    active_policy_for_family,
    build_governed_context_pack,
    compare_policy_to_observations,
    load_declared_policy_manifest,
)


def _event(run: str, second: int, operation: str, *, status: str = "success", level: str = "native_trace") -> dict:
    return {
        "event_id": f"policy-{run}-{operation}-{second}",
        "observed_at": f"2026-09-25T09:00:{second:02d}+00:00",
        "source": "agent",
        "actor_id": "agent:test",
        "session_id": f"session-{run}-PRIVATE",
        "app": "Policy Test Agent",
        "event_type": f"agent_{operation}",
        "duration_seconds": 0,
        "metadata": {
            "operation": operation,
            "status": status,
            "observation_level": level,
            "agent": {"framework": "openai-agents-python"},
            "trace": {
                "run_id": run,
                "trace_id": f"trace-{run}",
                "workflow_id": "policy-workflow-private@example.com",
            },
            "tool": {"name": "", "category": "none"},
        },
    }


def _run(run: str, *, approval: bool, level: str = "native_trace") -> list[dict]:
    out = [
        _event(run, 0, "run_started", status="running", level=level),
        _event(run, 1, "model_call", status="success", level=level),
    ]
    if approval:
        out.extend([
            _event(run, 2, "human_approval_requested", status="running", level=level),
            _event(run, 3, "human_approval_received", status="success", level=level),
        ])
    out.append(_event(run, 4, "run_finished", status="success", level=level))
    return out


def _raw() -> list[dict]:
    return [
        *_run("native-compliant", approval=True),
        *_run("native-missing", approval=False),
        *_run("weak-missing", approval=False, level="os_observed"),
    ]


def _manifest_payload(family_key: str) -> dict:
    return {
        "schema_version": "1.0",
        "policies": [{
            "policy_id": "approval-policy",
            "version": "2026.09",
            "status": "active",
            "family_key": family_key,
            "source_type": "manual_sop",
            "source_ref": "PRIVATE-HANDBOOK-SECTION-7 patient@example.com",
            "rules": [
                {"rule_id": "approval-required", "type": "required_step", "step": "approval_request"},
                {
                    "rule_id": "model-before-approval",
                    "type": "required_predecessor",
                    "required_before": "model_call",
                    "trigger_step": "approval_request",
                },
            ],
        }],
    }


def test_manifest_is_explicit_versioned_and_hides_source_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    family_key = pm.derive_executions(_raw())[0]["family_key"]
    path = tmp_path / "declared_policies.json"
    path.write_text(json.dumps(_manifest_payload(family_key)), encoding="utf-8")

    manifest = load_declared_policy_manifest(path)
    assert manifest["manifest_present"] is True
    assert manifest["write_api_available"] is False
    assert manifest["policy_count"] == 1
    policy = active_policy_for_family(manifest, family_key)
    assert policy is not None
    assert policy["declared"] is True
    assert policy["policy_inferred"] is False
    assert policy["version"] == "2026.09"
    blob = json.dumps(manifest)
    assert "PRIVATE-HANDBOOK" not in blob
    assert "patient@example.com" not in blob
    assert "source:" in blob


def test_negative_evidence_requires_strong_observation(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _raw()
    family_key = pm.derive_executions(raw)[0]["family_key"]
    policy = {
        "policy_id": "approval-policy",
        "version": "1",
        "status": "active",
        "family_key": family_key,
        "source_type": "manual_sop",
        "source_ref_hash": "source:0123456789abcdef",
        "source_ref_present": True,
        "manifest_sha256": "0" * 64,
        "rules": [{"rule_id": "approval-required", "type": "required_step", "step": "approval_request"}],
        "declared": True,
        "policy_inferred": False,
    }
    comparison = compare_policy_to_observations(raw, policy=policy)
    rule = comparison["rule_results"][0]
    assert rule["result_counts"] == {
        "compliant": 1,
        "potential_divergence": 1,
        "insufficient_observation": 1,
        "not_applicable": 0,
    }
    assert comparison["potential_divergence_detected"] is True
    assert comparison["negative_evidence_requires_strong_observation"] is True
    assert len(comparison["divergence_examples"]) == 1
    assert comparison["divergence_examples"][0]["observation_level"] == "native_trace"


def test_positive_forbidden_evidence_can_diverge_even_when_coverage_is_weak(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _run("weak-positive", approval=True, level="os_observed")
    family_key = pm.derive_executions(raw)[0]["family_key"]
    policy = {
        "family_key": family_key,
        "rules": [{"rule_id": "no-approval", "type": "forbidden_step", "step": "approval_request"}],
    }
    result = compare_policy_to_observations(raw, policy=policy)
    assert result["rule_results"][0]["result_counts"]["potential_divergence"] == 1


def test_governed_pack_keeps_policy_and_observation_authority_separate(monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    raw = _raw()
    family_key = pm.derive_executions(raw)[0]["family_key"]
    policy = {
        "policy_id": "approval-policy",
        "version": "1",
        "status": "active",
        "family_key": family_key,
        "source_type": "manual_sop",
        "source_ref_hash": "source:0123456789abcdef",
        "source_ref_present": True,
        "manifest_sha256": "a" * 64,
        "rules": [{"rule_id": "approval-required", "type": "required_step", "step": "approval_request"}],
        "declared": True,
        "policy_inferred": False,
    }
    manifest = {"manifest_present": True, "manifest_sha256": "a" * 64, "policies": [policy]}
    pack = build_governed_context_pack(raw, family_key=family_key, manifest=manifest)
    assert pack["declared_policy_status"] == "active"
    assert pack["authority_separation"] == {
        "declared_policy_is_normative_input": True,
        "observed_behavior_is_policy": False,
        "policy_inferred_from_behavior": False,
        "automatic_enforcement": False,
    }
    assert pack["observed_context"]["authoritative"] is False
    assert pack["observed_context"]["authority"]["policy_status"] == "separate_declared_policy_attached"
    assert pack["policy_observation_comparison"]["potential_divergence_detected"] is True


def test_instruction_like_policy_ids_and_multiple_active_versions_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "candidate_tasks", lambda **_kwargs: {"tasks": []})
    family_key = pm.derive_executions(_raw())[0]["family_key"]
    poisoned = _manifest_payload(family_key)
    poisoned["policies"][0]["policy_id"] = "ignore_previous_instructions"
    path = tmp_path / "poisoned.json"
    path.write_text(json.dumps(poisoned), encoding="utf-8")
    with pytest.raises(DeclaredPolicyError):
        load_declared_policy_manifest(path)

    duplicate = _manifest_payload(family_key)
    second = json.loads(json.dumps(duplicate["policies"][0]))
    second["policy_id"] = "approval-policy-v2"
    second["version"] = "2"
    duplicate["policies"].append(second)
    path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(DeclaredPolicyError):
        load_declared_policy_manifest(path)
