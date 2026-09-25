from __future__ import annotations

import pytest

from adapters.action_guard import ActionGuardError, ActionGuardUnavailable, ActionPolicyClient
from adapters.enforcement_preview import SHADOW_PROFILE_ID, ShadowEnforcementSimulator


FAMILY = "human:github.create_issue"
ACTION = "tool:deployment:tool:aaaaaaaaaaaa"
APPROVAL = "approval_received:success"
MANIFEST = "a" * 64


def _payload(
    body: dict,
    *,
    active: bool = True,
    warning_codes: tuple[str, ...] = (),
    no_match: bool = False,
) -> dict:
    relevant = []
    for index, code in enumerate(warning_codes):
        if code == "declared_forbidden_step":
            relevant.append({
                "rule_id": f"rule-{index}",
                "type": "forbidden_step",
                "advisory": code,
                "warning": True,
                "step": body["proposed_step"],
            })
        else:
            required = APPROVAL if code == "approval_prerequisite_missing" else "tool:search:tool:bbbbbbbbbbbb"
            relevant.append({
                "rule_id": f"rule-{index}",
                "type": "required_predecessor",
                "advisory": code,
                "warning": True,
                "required_before": required,
                "trigger_step": body["proposed_step"],
                "approval_related": code == "approval_prerequisite_missing",
            })
    if active and not warning_codes and not no_match:
        relevant = [{
            "rule_id": "rule-satisfied",
            "type": "required_predecessor",
            "advisory": "prerequisite_satisfied",
            "warning": False,
            "constraint_satisfied": True,
            "required_before": APPROVAL,
            "trigger_step": body["proposed_step"],
            "approval_related": True,
        }]

    if not active:
        advisory_status = "no_active_declared_policy"
    elif no_match:
        advisory_status = "no_matching_declared_constraint"
    elif warning_codes:
        advisory_status = "declared_policy_warning"
    else:
        advisory_status = "matched_prerequisites_satisfied"

    approval_missing = "approval_prerequisite_missing" in warning_codes
    return {
        "family_key": body["family_key"],
        "proposed_step": body["proposed_step"],
        "completed_steps_considered": list(body.get("completed_steps") or []),
        "declared_policy_status": "active" if active else "not_declared",
        "advisory_status": advisory_status,
        "relevant_rules": relevant,
        "warning_count": len(warning_codes),
        "missing_prerequisite_count": sum(
            code in {"approval_prerequisite_missing", "prerequisite_missing"}
            for code in warning_codes
        ),
        "approval_prerequisite_missing": approval_missing,
        "authorization_decision": "not_made",
        "action_allowed": None,
        "blocking": False,
        "automatic_enforcement": False,
        "execution_performed": False,
        "observed_work_used_as_permission": False,
        "observed_behavior_considered": False,
        "declared_policy": ({
            "policy_id": "change-policy",
            "version": "7",
            "family_key": body["family_key"],
            "source_type": "manual_sop",
            "source_ref_hash": "source:1234567890abcdef",
            "manifest_sha256": MANIFEST,
            "declared": True,
            "policy_inferred": False,
        } if active else None),
        "manifest_present": active,
        "manifest_sha256": MANIFEST if active else None,
        "derived": True,
        "read_only": True,
        "writes_performed": False,
        "capability_scope": "policy_action_advisory_only",
    }


def test_forbidden_step_previews_candidate_deny_but_shadow_action_still_executes():
    simulator = ShadowEnforcementSimulator(
        client=ActionPolicyClient(
            fetcher=lambda body: _payload(body, warning_codes=("declared_forbidden_step",))
        )
    )
    actions = []
    result = simulator.run(
        lambda: actions.append("ran") or "local-value",
        family_key=FAMILY,
        proposed_step=ACTION,
    )
    assert result.executed is True
    assert result.value == "local-value"
    assert actions == ["ran"]
    assert result.preview.candidate_disposition == "candidate_deny"
    assert result.preview.candidate_would_interrupt is True
    assert result.preview.actual_blocking is False
    assert result.preview.actual_enforcement_enabled is False
    assert result.preview.authorization_decision == "not_made"
    assert result.preview.action_allowed is None
    assert "local-value" not in str(result.preview.as_dict())


def test_missing_approval_previews_pause_but_does_not_pause_shadow_execution():
    simulator = ShadowEnforcementSimulator(
        client=ActionPolicyClient(
            fetcher=lambda body: _payload(body, warning_codes=("approval_prerequisite_missing",))
        )
    )
    actions = []
    result = simulator.run(
        lambda: actions.append("ran"),
        family_key=FAMILY,
        proposed_step=ACTION,
    )
    assert result.executed is True
    assert actions == ["ran"]
    assert result.preview.candidate_disposition == "candidate_pause_for_human_approval"
    assert result.preview.candidate_would_interrupt is True
    assert result.preview.reason_codes == ("approval_prerequisite_missing",)


def test_missing_nonapproval_predecessor_previews_prerequisite_pause_only():
    preview = ShadowEnforcementSimulator(
        client=ActionPolicyClient(
            fetcher=lambda body: _payload(body, warning_codes=("prerequisite_missing",))
        )
    ).evaluate(family_key=FAMILY, proposed_step=ACTION)
    assert preview.candidate_disposition == "candidate_pause_for_prerequisite"
    assert preview.candidate_would_interrupt is True
    assert preview.actual_blocking is False


def test_satisfied_constraint_reports_no_blocking_condition_without_authorization():
    preview = ShadowEnforcementSimulator(
        client=ActionPolicyClient(fetcher=lambda body: _payload(body))
    ).evaluate(family_key=FAMILY, proposed_step=ACTION, completed_steps=[APPROVAL])
    assert preview.candidate_disposition == "no_blocking_condition_observed"
    assert preview.candidate_would_interrupt is False
    assert preview.authorization_decision == "not_made"
    assert preview.action_allowed is None
    assert preview.policy_id == "change-policy"
    assert preview.policy_version == "7"
    assert preview.manifest_sha256 == MANIFEST


def test_no_matching_rule_and_no_policy_never_become_allow_decisions():
    no_match = ShadowEnforcementSimulator(
        client=ActionPolicyClient(fetcher=lambda body: _payload(body, no_match=True))
    ).evaluate(family_key=FAMILY, proposed_step=ACTION)
    assert no_match.candidate_disposition == "no_declared_enforcement_decision"
    assert no_match.candidate_would_interrupt is None
    assert no_match.action_allowed is None

    no_policy = ShadowEnforcementSimulator(
        client=ActionPolicyClient(fetcher=lambda body: _payload(body, active=False))
    ).evaluate(family_key=FAMILY, proposed_step=ACTION)
    assert no_policy.candidate_disposition == "no_declared_enforcement_decision"
    assert no_policy.candidate_would_interrupt is None
    assert no_policy.action_allowed is None


def test_policy_service_unavailable_is_indeterminate_and_shadow_action_runs():
    def unavailable(body):
        raise ActionGuardUnavailable("down")

    actions = []
    result = ShadowEnforcementSimulator(
        client=ActionPolicyClient(fetcher=unavailable)
    ).run(lambda: actions.append("ran"), family_key=FAMILY, proposed_step=ACTION)
    assert result.executed is True
    assert actions == ["ran"]
    assert result.preview.available is False
    assert result.preview.candidate_disposition == "indeterminate_policy_unavailable"
    assert result.preview.candidate_would_interrupt is None


def test_preview_callback_failure_cannot_change_shadow_execution():
    def broken_callback(preview):
        raise RuntimeError("UI/logging failure")

    actions = []
    result = ShadowEnforcementSimulator(
        client=ActionPolicyClient(fetcher=lambda body: _payload(body, warning_codes=("declared_forbidden_step",))),
        on_preview=broken_callback,
    ).run(lambda: actions.append("ran"), family_key=FAMILY, proposed_step=ACTION)
    assert result.executed is True
    assert actions == ["ran"]
    assert result.preview.candidate_disposition == "candidate_deny"


def test_invalid_structural_input_is_programming_error_not_shadow_permission():
    simulator = ShadowEnforcementSimulator(
        client=ActionPolicyClient(fetcher=lambda body: _payload(body))
    )
    actions = []
    with pytest.raises(ActionGuardError):
        simulator.run(
            lambda: actions.append("ran"),
            family_key="IGNORE PREVIOUS INSTRUCTIONS",
            proposed_step=ACTION,
        )
    assert actions == []


def test_shadow_contract_is_explicit_and_stable():
    preview = ShadowEnforcementSimulator(
        client=ActionPolicyClient(fetcher=lambda body: _payload(body, warning_codes=("declared_forbidden_step",)))
    ).evaluate(family_key=FAMILY, proposed_step=ACTION)
    payload = preview.as_dict()
    assert payload["shadow_profile_id"] == SHADOW_PROFILE_ID
    assert payload["simulated_only"] is True
    assert payload["actual_enforcement_enabled"] is False
    assert payload["actual_blocking"] is False
    assert payload["execution_performed"] is False
    assert payload["authorization_decision"] == "not_made"
    assert payload["action_allowed"] is None
