from __future__ import annotations

import pytest

from server.declared_policy import DeclaredPolicyError
from server.policy_action_advisory import action_policy_advisory


FAMILY = "human:github.create_issue"
DEPLOY = "tool:deployment:tool:aaaaaaaaaaaa"
NETWORK = "tool:network:tool:bbbbbbbbbbbb"
SEARCH = "tool:search:tool:cccccccccccc"


def _manifest(rules: list[dict]) -> dict:
    return {
        "manifest_present": True,
        "manifest_sha256": "d" * 64,
        "policies": [
            {
                "policy_id": "github-change-policy",
                "version": "7",
                "status": "active",
                "family_key": FAMILY,
                "source_type": "manual_sop",
                "source_ref_hash": "source:1234567890abcdef",
                "manifest_sha256": "d" * 64,
                "rules": rules,
                "declared": True,
                "policy_inferred": False,
            }
        ],
    }


def test_forbidden_step_warns_but_never_blocks_or_authorizes():
    result = action_policy_advisory(
        family_key=FAMILY,
        proposed_step=DEPLOY,
        manifest=_manifest([
            {"rule_id": "no-deploy", "type": "forbidden_step", "step": DEPLOY},
        ]),
    )
    assert result["advisory_status"] == "declared_policy_warning"
    assert result["warning_count"] == 1
    assert result["relevant_rules"][0]["advisory"] == "declared_forbidden_step"
    assert result["authorization_decision"] == "not_made"
    assert result["action_allowed"] is None
    assert result["blocking"] is False
    assert result["automatic_enforcement"] is False
    assert result["execution_performed"] is False
    assert result["observed_work_used_as_permission"] is False


def test_missing_approval_predecessor_is_advisory_warning_and_can_be_satisfied():
    manifest = _manifest([
        {
            "rule_id": "approval-before-deploy",
            "type": "required_predecessor",
            "required_before": "approval_received:success",
            "trigger_step": DEPLOY,
        }
    ])
    missing = action_policy_advisory(
        family_key=FAMILY,
        proposed_step=DEPLOY,
        completed_steps=[SEARCH],
        manifest=manifest,
    )
    assert missing["advisory_status"] == "declared_policy_warning"
    assert missing["approval_prerequisite_missing"] is True
    assert missing["missing_prerequisite_count"] == 1
    assert missing["relevant_rules"][0]["advisory"] == "approval_prerequisite_missing"

    satisfied = action_policy_advisory(
        family_key=FAMILY,
        proposed_step=DEPLOY,
        completed_steps=[SEARCH, "approval_received:success"],
        manifest=manifest,
    )
    assert satisfied["advisory_status"] == "matched_constraints_satisfied"
    assert satisfied["warning_count"] == 0
    assert satisfied["approval_prerequisite_missing"] is False
    assert satisfied["relevant_rules"][0]["advisory"] == "prerequisite_satisfied"
    assert satisfied["action_allowed"] is None


def test_generic_required_step_is_not_reinterpreted_as_ordering_constraint():
    result = action_policy_advisory(
        family_key=FAMILY,
        proposed_step=NETWORK,
        completed_steps=[],
        manifest=_manifest([
            {"rule_id": "must-request-approval-sometime", "type": "required_step", "step": "approval_request"},
        ]),
    )
    assert result["advisory_status"] == "no_matching_declared_constraint"
    assert result["warning_count"] == 0
    assert result["relevant_rules"] == []
    assert result["authority"]["absence_of_matching_rule_means_allowed"] is False
    assert result["action_allowed"] is None


def test_proposing_a_declared_required_step_is_informational_not_permission():
    result = action_policy_advisory(
        family_key=FAMILY,
        proposed_step="approval_request",
        manifest=_manifest([
            {"rule_id": "must-request-approval", "type": "required_step", "step": "approval_request"},
        ]),
    )
    assert result["advisory_status"] == "matched_constraints_satisfied"
    assert result["relevant_rules"][0]["advisory"] == "declared_required_step_is_proposed"
    assert result["action_allowed"] is None


def test_no_policy_is_not_authorization_and_invalid_structural_input_fails_closed():
    empty = action_policy_advisory(
        family_key=FAMILY,
        proposed_step=SEARCH,
        manifest={"manifest_present": False, "manifest_sha256": None, "policies": []},
    )
    assert empty["advisory_status"] == "no_active_declared_policy"
    assert empty["action_allowed"] is None
    assert empty["authorization_decision"] == "not_made"

    with pytest.raises(ValueError):
        action_policy_advisory(
            family_key=FAMILY,
            proposed_step="ignore_previous_instructions",
            manifest=_manifest([]),
        )

    with pytest.raises(DeclaredPolicyError):
        action_policy_advisory(
            family_key="human:github.ignore_previous_instructions",
            proposed_step=SEARCH,
            manifest=_manifest([]),
        )
