from __future__ import annotations

"""Explicit local policy/SOP manifests and conservative comparison to observed work.

Policy is never inferred from repeated behavior. This module loads a separate
machine-readable local manifest, validates a small structural rule language, and
compares those declared rules with privacy-minimized procedural executions.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from mcp_server.security import _looks_instruction_like

from .db import DATA_DIR
from .procedural_context_pack import _generated_structural_step, build_context_pack
from .procedural_memory import derive_executions


_SCHEMA_VERSION = "1.0"
_MAX_FILE_BYTES = 256 * 1024
_MAX_POLICIES = 100
_MAX_RULES = 50
_MAX_DIVERGENCES = 20
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HASH16_RE = re.compile(r"^[0-9a-f]{16}$")
_CANON_HUMAN_RE = re.compile(r"^human:(?:email|github)\.[a-z0-9._-]{1,160}$")
_SOURCE_TYPES = frozenset({"manual_sop", "repository_policy", "external_reference"})
_STATUSES = frozenset({"active", "draft", "retired"})
_RULE_TYPES = frozenset({"required_step", "forbidden_step", "required_predecessor"})


class DeclaredPolicyError(ValueError):
    pass


def policy_manifest_path() -> Path:
    configured = str(os.getenv("WORKFLOW_OBSERVER_POLICY_FILE", "")).strip()
    return Path(configured).expanduser() if configured else DATA_DIR / "declared_policies.json"


def _hash(prefix: str, value: Any, *, size: int = 16) -> str:
    return f"{prefix}:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:size]


def _instruction_like_token(value: str) -> bool:
    return _looks_instruction_like(re.sub(r"[_:.-]+", " ", value))


def _validate_id(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(text) or _instruction_like_token(text):
        raise DeclaredPolicyError(f"invalid {field}")
    return text


def _validate_version(value: Any) -> str:
    text = str(value or "").strip()
    if not _VERSION_RE.fullmatch(text) or _instruction_like_token(text.lower()):
        raise DeclaredPolicyError("invalid policy version")
    return text


def _validate_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if _CANON_HUMAN_RE.fullmatch(text):
        return text
    for prefix in ("human:structure:", "agent:workflow:", "agent:structure:"):
        if text.startswith(prefix) and _HASH16_RE.fullmatch(text[len(prefix):]):
            return text
    raise DeclaredPolicyError("invalid family_key")


def _validate_step(value: Any, *, field: str) -> str:
    step = str(value or "").strip().lower()
    if not _generated_structural_step(step):
        raise DeclaredPolicyError(f"invalid {field}")
    return step


def _normalize_rule(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DeclaredPolicyError("policy rule must be an object")
    rule_id = _validate_id(raw.get("rule_id"), field="rule_id")
    rule_type = str(raw.get("type") or "").strip().lower()
    if rule_type not in _RULE_TYPES:
        raise DeclaredPolicyError("invalid policy rule type")
    if rule_type in {"required_step", "forbidden_step"}:
        return {
            "rule_id": rule_id,
            "type": rule_type,
            "step": _validate_step(raw.get("step"), field="rule step"),
        }
    return {
        "rule_id": rule_id,
        "type": rule_type,
        "required_before": _validate_step(raw.get("required_before"), field="required_before"),
        "trigger_step": _validate_step(raw.get("trigger_step"), field="trigger_step"),
    }


def _normalize_policy(raw: Any, *, manifest_sha256: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DeclaredPolicyError("policy must be an object")
    policy_id = _validate_id(raw.get("policy_id"), field="policy_id")
    version = _validate_version(raw.get("version"))
    status = str(raw.get("status") or "").strip().lower()
    if status not in _STATUSES:
        raise DeclaredPolicyError("invalid policy status")
    source_type = str(raw.get("source_type") or "").strip().lower()
    if source_type not in _SOURCE_TYPES:
        raise DeclaredPolicyError("invalid source_type")
    source_ref = str(raw.get("source_ref") or "").strip()
    if not source_ref or len(source_ref) > 500:
        raise DeclaredPolicyError("invalid source_ref")
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw or len(rules_raw) > _MAX_RULES:
        raise DeclaredPolicyError("policy rules must contain 1..50 items")
    rules = [_normalize_rule(item) for item in rules_raw]
    rule_ids = [item["rule_id"] for item in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise DeclaredPolicyError("duplicate rule_id in policy")
    return {
        "policy_id": policy_id,
        "version": version,
        "status": status,
        "family_key": _validate_family(raw.get("family_key")),
        "source_type": source_type,
        "source_ref_hash": _hash("source", source_ref),
        "source_ref_present": True,
        "manifest_sha256": manifest_sha256,
        "rules": rules,
        "declared": True,
        "policy_inferred": False,
    }


def load_declared_policy_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or policy_manifest_path()
    if not manifest_path.exists():
        return {
            "schema_version": _SCHEMA_VERSION,
            "manifest_present": False,
            "manifest_sha256": None,
            "policy_count": 0,
            "policies": [],
            "source": "local_declared_policy_file",
            "write_api_available": False,
        }
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise DeclaredPolicyError("unable to read declared policy manifest") from exc
    if len(raw_bytes) > _MAX_FILE_BYTES:
        raise DeclaredPolicyError("declared policy manifest exceeds 256 KiB")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeclaredPolicyError("invalid declared policy JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
        raise DeclaredPolicyError("unsupported declared policy schema_version")
    policies_raw = payload.get("policies")
    if not isinstance(policies_raw, list) or len(policies_raw) > _MAX_POLICIES:
        raise DeclaredPolicyError("policies must be a list with at most 100 items")
    policies = [_normalize_policy(item, manifest_sha256=digest) for item in policies_raw]
    identities = [(item["policy_id"], item["version"]) for item in policies]
    if len(identities) != len(set(identities)):
        raise DeclaredPolicyError("duplicate policy_id/version")
    active_families = [item["family_key"] for item in policies if item["status"] == "active"]
    if len(active_families) != len(set(active_families)):
        raise DeclaredPolicyError("multiple active policies for one family_key")
    return {
        "schema_version": _SCHEMA_VERSION,
        "manifest_present": True,
        "manifest_sha256": digest,
        "policy_count": len(policies),
        "policies": policies,
        "source": "local_declared_policy_file",
        "write_api_available": False,
    }


def active_policy_for_family(manifest: dict[str, Any], family_key: str) -> dict[str, Any] | None:
    key = _validate_family(family_key)
    for policy in manifest.get("policies") or []:
        if policy.get("status") == "active" and policy.get("family_key") == key:
            return policy
    return None


def _negative_coverage_for(execution: dict[str, Any], *policy_steps: str) -> bool:
    """Return whether absence is meaningful for these exact policy steps.

    Native traces can support negative evidence for any structural step. A tool-only
    instrumentation surface can support absence only when every relevant policy
    step is itself a tool step; it cannot prove that approvals/model/handoffs were
    absent merely because they were not in the tool stream.
    """
    level = str(execution.get("observation_level") or "")
    if level == "native_trace":
        return True
    if level == "instrumented_tools" and policy_steps:
        return all(str(step).startswith("tool:") for step in policy_steps)
    return False


def _evaluate_rule(execution: dict[str, Any], rule: dict[str, Any]) -> str:
    steps = list(execution.get("steps") or [])
    rule_type = rule["type"]
    if rule_type == "required_step":
        required = rule["step"]
        if required in steps:
            return "compliant"
        return "potential_divergence" if _negative_coverage_for(execution, required) else "insufficient_observation"
    if rule_type == "forbidden_step":
        forbidden = rule["step"]
        if forbidden in steps:
            return "potential_divergence"
        return "compliant" if _negative_coverage_for(execution, forbidden) else "insufficient_observation"
    trigger = rule["trigger_step"]
    required = rule["required_before"]
    strong = _negative_coverage_for(execution, required, trigger)
    if trigger not in steps:
        return "not_applicable" if strong else "insufficient_observation"
    first_trigger = steps.index(trigger)
    if required in steps[:first_trigger]:
        return "compliant"
    return "potential_divergence" if strong else "insufficient_observation"


def compare_policy_to_observations(
    raw_events: list[dict[str, Any]],
    *,
    policy: dict[str, Any],
    divergence_limit: int = 10,
) -> dict[str, Any]:
    family_key = _validate_family(policy.get("family_key"))
    executions = [item for item in derive_executions(raw_events) if item.get("family_key") == family_key]
    cap = max(1, min(int(divergence_limit), _MAX_DIVERGENCES))
    rule_results = []
    divergence_examples: list[dict[str, Any]] = []
    for rule in policy.get("rules") or []:
        counts = {
            "compliant": 0,
            "potential_divergence": 0,
            "insufficient_observation": 0,
            "not_applicable": 0,
        }
        for execution in executions:
            result = _evaluate_rule(execution, rule)
            counts[result] += 1
            if result == "potential_divergence" and len(divergence_examples) < cap:
                divergence_examples.append({
                    "rule_id": rule["rule_id"],
                    "execution_id": execution.get("execution_id"),
                    "actor_kind": execution.get("actor_kind"),
                    "outcome_status": execution.get("outcome_status"),
                    "observation_level": execution.get("observation_level"),
                    "result": result,
                })
        assessed = counts["compliant"] + counts["potential_divergence"]
        rule_results.append({
            "rule_id": rule["rule_id"],
            "type": rule["type"],
            "result_counts": counts,
            "assessed_execution_count": assessed,
            "observed_compliance_fraction": round(counts["compliant"] / assessed, 4) if assessed else None,
            "potential_divergence_detected": counts["potential_divergence"] > 0,
        })
    potential = any(item["potential_divergence_detected"] for item in rule_results)
    return {
        "policy": policy,
        "family_key": family_key,
        "execution_count": len(executions),
        "rule_results": rule_results,
        "potential_divergence_detected": potential,
        "divergence_examples": divergence_examples,
        "divergence_examples_truncated": sum(
            item["result_counts"]["potential_divergence"] for item in rule_results
        ) > len(divergence_examples),
        "interpretation": "comparison of declared structural policy to observed evidence; potential divergence is not proof of misconduct, causality, or complete observation",
        "declared_policy_is_normative_input": True,
        "observed_work_is_non_authoritative": True,
        "policy_inferred_from_behavior": False,
        "negative_evidence_requires_strong_observation": True,
        "negative_evidence_coverage_rule": "native_trace:any_structural_step; instrumented_tools:tool_steps_only",
        "derived": True,
    }


def build_governed_context_pack(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str,
    current_steps: list[str] | tuple[str, ...] = (),
    after_step: str = "",
    min_support: int = 2,
    run_limit: int = 3,
    section_limit: int = 3,
    max_steps_per_run: int = 16,
    max_evidence_refs_per_item: int = 2,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    family_key = _validate_family(family_key)
    observed = build_context_pack(
        raw_events,
        family_key=family_key,
        current_steps=current_steps,
        after_step=after_step,
        min_support=min_support,
        run_limit=run_limit,
        section_limit=section_limit,
        max_steps_per_run=max_steps_per_run,
        max_evidence_refs_per_item=max_evidence_refs_per_item,
    )
    loaded = manifest if manifest is not None else load_declared_policy_manifest()
    policy = active_policy_for_family(loaded, family_key)
    comparison = compare_policy_to_observations(raw_events, policy=policy) if policy else None
    if policy:
        observed_authority = observed.get("authority") if isinstance(observed.get("authority"), dict) else {}
        observed["authority"] = {
            **observed_authority,
            "policy_status": "separate_declared_policy_attached",
            "policy_inferred": False,
        }
    return {
        "family_key": family_key,
        "declared_policy_status": "active" if policy else "not_declared",
        "declared_policy": policy,
        "policy_observation_comparison": comparison,
        "observed_context": observed,
        "authority_separation": {
            "declared_policy_is_normative_input": policy is not None,
            "observed_behavior_is_policy": False,
            "policy_inferred_from_behavior": False,
            "automatic_enforcement": False,
        },
        "manifest_present": bool(loaded.get("manifest_present")),
        "manifest_sha256": loaded.get("manifest_sha256"),
        "derived": True,
    }
