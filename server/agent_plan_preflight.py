"""Offline, deterministic agent-plan preflight; NEVER executes workflow steps.

A candidate plan and synthetic traces are treated as untrusted data. This
module only checks structural coverage, proposed side effects, mock failures,
and missing verification gates. It does not run agents or inspect live systems.
"""
from __future__ import annotations

import re
from typing import Any

FORMAT = "openworkgraph.agent-plan-preflight.v1"
SPEC_FORMAT = "openworkgraph.agent-starter.v1"
CANDIDATE_FORMAT = "openworkgraph.agent-candidate-plan.v1"
MAX_BYTES = 64 * 1024
_MAX_CASES = 24
_MAX_STEPS = 40
_STEP_ID = re.compile(r"^step_[0-9]{3}$")
_SCENARIO = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,47}$")
_ACTIONS = {"read_only", "draft_only", "manual_review", "write"}
_EVIDENCE = {"none", "mock_only", "independent_source_readback"}
_EXCEPTIONS = {"not_configured", "human_escalation", "mock_only"}
_MOCK_RESULTS = {"mock_success", "mock_failure", "mock_unknown"}
_OUTCOME = {"unknown", "claimed_success", "claimed_failure"}


class AgentPreflightError(ValueError):
    """Error messages must be generic, never include imported customer text."""


def _mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentPreflightError(f"{name} must be a JSON object")
    return value


def _spec(value: Any) -> tuple[list[str], bool]:
    spec = _mapping(value, name="workflow specification")
    security = _mapping(spec.get("security"), name="security section")
    target = _mapping(spec.get("to_be"), name="target process")
    if (spec.get("format") != SPEC_FORMAT
            or spec.get("review_status") != "human_reviewed_design_not_authorized_execution"
            or security.get("live_actions_possible") is not False
            or security.get("runtime_tool_permissions_granted") is not False
            or security.get("source_records_included") is not False
            or target.get("user_proposed_not_production_process") is not True):
        raise AgentPreflightError("reviewed non-executable OWG starter required")
    steps = target.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= _MAX_STEPS:
        raise AgentPreflightError("starter requires 1 to 40 steps")
    ids = []
    for index, step in enumerate(steps, 1):
        row = _mapping(step, name="workflow step")
        step_id = f"step_{index:03d}"
        if (row.get("id") != step_id
                or row.get("tool") is not None
                or row.get("action_permission_granted") is not False
                or row.get("tool_binding_confirmed") is not False):
            raise AgentPreflightError("starter contains mapped or authorized actions")
        ids.append(step_id)
    declared = target.get("manual_review_conditions", [])
    if not isinstance(declared, list) or len(declared) > 12:
        raise AgentPreflightError("invalid manual-review condition declarations")
    if target.get("automatic_decision_routing_enabled", False) is not False:
        raise AgentPreflightError("automatic decision routing is not permitted")
    for item in declared:
        if (not isinstance(item, dict)
                or item.get("at_step_id") not in ids
                or item.get("executable") is not False
                or item.get("requires_independent_policy_review") is not True):
            raise AgentPreflightError("invalid manual-review condition")
    return ids, bool(declared)


def _candidate(value: Any, ids: list[str]) -> tuple[dict[str, dict[str, Any]], list[str], str]:
    candidate = _mapping(value, name="candidate plan")
    if candidate.get("format") != CANDIDATE_FORMAT:
        raise AgentPreflightError("invalid candidate plan format")
    if not isinstance(candidate.get("intent"), str) or candidate.get("intent") not in {"draft_only", "shadow_only", "live"}:
        raise AgentPreflightError("invalid candidate intent")
    if not isinstance(candidate.get("exception_handling"), str) or candidate.get("exception_handling") not in _EXCEPTIONS:
        raise AgentPreflightError("invalid exception handling mode")
    actions = candidate.get("proposed_actions")
    if not isinstance(actions, list) or len(actions) > _MAX_STEPS:
        raise AgentPreflightError("candidate actions must be a bounded list")
    by_id: dict[str, dict[str, Any]] = {}
    blockers = []
    if candidate["intent"] == "live":
        blockers.append("live_execution_requested")
    for action in actions:
        if not isinstance(action, dict):
            raise AgentPreflightError("invalid candidate action")
        step_id = action.get("step_id")
        kind = action.get("action_kind")
        evidence = action.get("outcome_evidence_mode")
        check = action.get("source_state_checked")
        if (not isinstance(step_id, str) or not _STEP_ID.fullmatch(step_id)
                or not isinstance(kind, str) or kind not in _ACTIONS
                or not isinstance(evidence, str) or evidence not in _EVIDENCE
                or type(check) is not bool):
            raise AgentPreflightError("candidate action has invalid structural fields")
        if step_id in by_id:
            blockers.append("duplicate_candidate_step")
        if step_id not in ids:
            blockers.append("unknown_candidate_step")
        by_id[step_id] = {
            "step_id": step_id, "action_kind": kind,
            "outcome_evidence_mode": evidence,
            "source_state_checked": check,
        }
        if kind == "write":
            blockers.append("write_action_has_no_live_authorization")
        if kind in {"write", "read_only"} and not check:
            blockers.append("source_state_not_checked")
        if kind == "write" and evidence != "independent_source_readback":
            blockers.append("write_without_source_readback_plan")
    if len(by_id) != len(ids) or set(by_id) != set(ids):
        blockers.append("missing_or_unmatched_candidate_steps")
    if candidate["exception_handling"] != "human_escalation":
        blockers.append("unverified_exception_handling")
    return by_id, blockers, candidate["exception_handling"]


def _simulated_case(case: Any, ids: list[str], index: int) -> dict[str, Any]:
    example = _mapping(case, name="synthetic case")
    label = example.get("scenario")
    steps = example.get("mock_steps")
    outcome = example.get("claimed_outcome")
    if (not isinstance(label, str) or not _SCENARIO.fullmatch(label)
            or not isinstance(steps, list) or len(steps) > _MAX_STEPS
            or not isinstance(outcome, str) or outcome not in _OUTCOME):
        raise AgentPreflightError("invalid synthetic test scenario")
    issues: list[str] = []
    seen: set[str] = set()
    positions: list[int] = []
    for item in steps:
        if not isinstance(item, dict):
            raise AgentPreflightError("invalid synthetic step")
        step_id = item.get("step_id")
        status = item.get("mock_result")
        if not isinstance(step_id, str) or not _STEP_ID.fullmatch(step_id) or not isinstance(status, str) or status not in _MOCK_RESULTS:
            raise AgentPreflightError("synthetic test has an invalid step/status")
        if step_id not in ids:
            issues.append("unknown_simulated_step")
        else:
            positions.append(ids.index(step_id))
        if step_id in seen:
            issues.append("duplicate_simulated_step")
        seen.add(step_id)
        if status == "mock_failure":
            issues.append("simulated_step_failed")
        if status == "mock_unknown":
            issues.append("simulated_step_unknown")
    if positions != sorted(positions):
        issues.append("simulated_step_order_deviation")
    if set(ids) != seen:
        issues.append("simulated_incomplete_path")
    if outcome == "claimed_success":
        # No synthetic sequence, regardless of appearance, independently
        # proves a real business outcome.
        issues.append("claimed_outcome_not_independently_verified")
    return {
        "case": f"Scenario {index}",
        "step_count": len(steps),
        "claimed_outcome": outcome,
        "issues": sorted(set(issues)),
        "all_declared_steps_mock_observed": set(ids) == seen,
        "real_business_outcome_verified": False,
    }


def preflight_agent_plan(document: Any) -> dict[str, Any]:
    obj = _mapping(document, name="preflight request")
    if obj.get("format") != FORMAT:
        raise AgentPreflightError("invalid preflight format")
    if set(obj) != {"format", "workflow", "candidate", "simulated_cases"}:
        raise AgentPreflightError("preflight request must use the documented structural fields only")
    ids, has_conditions = _spec(obj.get("workflow"))
    candidate, blockers, exception_handling = _candidate(obj.get("candidate"), ids)
    if has_conditions and exception_handling != "human_escalation":
        blockers.append("declared_review_conditions_not_handled")
    cases = obj.get("simulated_cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= _MAX_CASES:
        raise AgentPreflightError("submit 1 to 24 synthetic cases")
    rows = [_simulated_case(item, ids, i) for i, item in enumerate(cases, 1)]
    case_issues = sum(bool(item["issues"]) for item in rows)
    # A clean structural score is NOT a release decision; this tool can never
    # grant real-world tool permissions or validate business outcomes.
    return {
        "format": "openworkgraph.agent-preflight-report.v1",
        "target_step_count": len(ids),
        "candidate_step_count": len(candidate),
        "synthetic_case_count": len(rows),
        "synthetic_cases_with_findings": case_issues,
        "plan_blockers": sorted(set(blockers)),
        "synthetic_cases": rows,
        "structural_review_passed": not blockers and case_issues == 0,
        "business_outcomes_verified": False,
        "source_systems_contacted": False,
        "tool_calls_executed": False,
        "model_invoked": False,
        "authorization_granted": False,
        "deployment_ready": False,
        "observed_outcome_uplift_measured": False,
        "live_vs_baseline_comparison_performed": False,
        "persisted": False,
        "report_warning": (
            "This is deterministic structural preflight over user-supplied synthetic data. "
            "It is not an agent execution, authorized deployment, business policy validation, "
            "independent source receipt, or measured improvement against a baseline."
        ),
    }


__all__ = ["preflight_agent_plan", "AgentPreflightError", "MAX_BYTES"]
