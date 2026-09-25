from __future__ import annotations

"""Read-only declared-policy advisory for one proposed structural action.

This module evaluates only explicit declared policy. It never treats observed
workflow frequency as permission, never executes or blocks an action, and never
turns a missing matching rule into an authorization decision.
"""

from typing import Any, Iterable

from .declared_policy import (
    active_policy_for_family,
    load_declared_policy_manifest,
)
from .procedural_context_pack import _generated_structural_step


_MAX_COMPLETED_STEPS = 48


def _validated_step(value: Any, *, field: str) -> str:
    step = str(value or "").strip().lower()
    if not _generated_structural_step(step):
        raise ValueError(f"invalid {field}")
    return step


def _validated_completed_steps(values: Iterable[str]) -> list[str]:
    normalized = [str(value).strip().lower() for value in values if str(value).strip()]
    if len(normalized) > _MAX_COMPLETED_STEPS:
        raise ValueError("too many completed structural steps")
    if any(not _generated_structural_step(step) for step in normalized):
        raise ValueError("invalid completed structural steps")
    return normalized


def _approval_prerequisite(step: str) -> bool:
    return step == "approval_request" or step.startswith("approval_received:")


def action_policy_advisory(
    *,
    family_key: str,
    proposed_step: str,
    completed_steps: Iterable[str] = (),
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe declared-policy constraints relevant to one proposed next step.

    This is advisory interpretation of explicit structural policy only. It does
    not decide whether an action is authorized and cannot execute or block it.
    """
    proposed = _validated_step(proposed_step, field="proposed_step")
    completed = _validated_completed_steps(completed_steps)
    loaded = manifest if manifest is not None else load_declared_policy_manifest()
    policy = active_policy_for_family(loaded, family_key)

    if policy is None:
        return {
            "family_key": str(family_key or "").strip().lower(),
            "proposed_step": proposed,
            "completed_steps_considered": completed,
            "declared_policy_status": "not_declared",
            "advisory_status": "no_active_declared_policy",
            "relevant_rules": [],
            "warning_count": 0,
            "missing_prerequisite_count": 0,
            "approval_prerequisite_missing": False,
            "authorization_decision": "not_made",
            "action_allowed": None,
            "blocking": False,
            "automatic_enforcement": False,
            "execution_performed": False,
            "observed_work_used_as_permission": False,
            "observed_behavior_considered": False,
            "manifest_present": bool(loaded.get("manifest_present")),
            "manifest_sha256": loaded.get("manifest_sha256"),
            "derived": True,
            "read_only": True,
        }

    relevant: list[dict[str, Any]] = []
    warning_count = 0
    missing_count = 0
    approval_missing = False

    for rule in policy.get("rules") or []:
        rule_type = str(rule.get("type") or "")
        if rule_type == "forbidden_step" and rule.get("step") == proposed:
            warning_count += 1
            relevant.append({
                "rule_id": rule.get("rule_id"),
                "type": rule_type,
                "advisory": "declared_forbidden_step",
                "warning": True,
                "constraint_satisfied": False,
                "step": proposed,
            })
            continue

        if rule_type == "required_predecessor" and rule.get("trigger_step") == proposed:
            required = str(rule.get("required_before") or "")
            satisfied = required in completed
            approval_related = _approval_prerequisite(required)
            if not satisfied:
                warning_count += 1
                missing_count += 1
                approval_missing = approval_missing or approval_related
            relevant.append({
                "rule_id": rule.get("rule_id"),
                "type": rule_type,
                "advisory": (
                    "prerequisite_satisfied"
                    if satisfied
                    else "approval_prerequisite_missing"
                    if approval_related
                    else "prerequisite_missing"
                ),
                "warning": not satisfied,
                "constraint_satisfied": satisfied,
                "required_before": required,
                "trigger_step": proposed,
                "approval_related": approval_related,
            })
            continue

        # A generic required_step has no deadline/order semantics. It is relevant
        # only when the proposed action is that required step itself; it must not
        # be reinterpreted as a prerequisite for unrelated actions.
        if rule_type == "required_step" and rule.get("step") == proposed:
            relevant.append({
                "rule_id": rule.get("rule_id"),
                "type": rule_type,
                "advisory": "declared_required_step_is_proposed",
                "warning": False,
                "constraint_satisfied": True,
                "step": proposed,
            })

    if warning_count:
        status = "declared_policy_warning"
    elif relevant:
        status = "matched_constraints_satisfied"
    else:
        status = "no_matching_declared_constraint"

    return {
        "family_key": policy.get("family_key"),
        "proposed_step": proposed,
        "completed_steps_considered": completed,
        "declared_policy_status": "active",
        "advisory_status": status,
        "relevant_rules": relevant,
        "warning_count": warning_count,
        "missing_prerequisite_count": missing_count,
        "approval_prerequisite_missing": approval_missing,
        "authorization_decision": "not_made",
        "action_allowed": None,
        "blocking": False,
        "automatic_enforcement": False,
        "execution_performed": False,
        "observed_work_used_as_permission": False,
        "observed_behavior_considered": False,
        "declared_policy": {
            "policy_id": policy.get("policy_id"),
            "version": policy.get("version"),
            "family_key": policy.get("family_key"),
            "source_type": policy.get("source_type"),
            "source_ref_hash": policy.get("source_ref_hash"),
            "manifest_sha256": policy.get("manifest_sha256"),
            "declared": True,
            "policy_inferred": False,
        },
        "manifest_present": bool(loaded.get("manifest_present")),
        "manifest_sha256": loaded.get("manifest_sha256"),
        "authority": {
            "declared_policy_is_normative_input": True,
            "observed_behavior_is_policy": False,
            "policy_inferred_from_behavior": False,
            "advisory_is_authorization": False,
            "absence_of_matching_rule_means_allowed": False,
        },
        "derived": True,
        "read_only": True,
    }
