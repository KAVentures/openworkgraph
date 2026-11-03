from __future__ import annotations

"""Revocable, time-separated review progress over two retained local evidence ledgers.

Only authenticated, currently admitted REST review routes call this module.
It cannot prove a current provider state, customer outcome or agent causality.
"""
import hmac
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_review_packet import issue_review_packet, validate_review_packet
from .open_work_case_review_actions import compose_outcome_review_action_plan
from .open_work_case_status_checkpoint import _bindings, _canonical, _mac

_FORMAT = "openworkgraph.local-outcome-review-progress-checkpoint.v1"
_RESULT = "openworkgraph.local-outcome-review-progress.v1"
_FIELDS = frozenset({"format", "review_packet", "case_states", "seal"})
_STATE_FIELDS = frozenset({
    "work_item_ref", "human_assessment_count", "latest_human_assessed_status",
    "provider_check_count", "disputed_check_count", "historical_last_provider_state",
    "operator_action_codes", "prerequisite_review_pending",
})
_ACTIONS = frozenset({
    "record_human_assessment", "review_human_provider_disagreement",
    "review_historical_provider_evidence", "obtain_signed_invoice_link",
    "fresh_declared_payment_check", "expand_dependency_review",
})


def _states(triage: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    cases = triage.get("case_reviews")
    actions = plan.get("cases")
    if (triage.get("format") != "openworkgraph.local-reviewed-outcome-evidence-triage.v1"
            or plan.get("format") != "openworkgraph.local-reviewed-outcome-action-plan.v1"
            or triage.get("work_item_ref") != plan.get("work_item_ref")
            or not isinstance(cases, list) or not isinstance(actions, list)
            or not 1 <= len(cases) <= 4 or len(cases) != len(actions)
            or triage.get("customer_outcome_verified") is not False
            or plan.get("customer_outcome_verified") is not False
            or plan.get("source_read_performed") is not False):
        raise CaseDeclarationError("invalid historical review progress input")
    states = []
    for case, action in zip(cases, actions):
        if (not isinstance(case, dict) or not isinstance(action, dict)
                or case.get("work_item_ref") != action.get("work_item_ref")):
            raise CaseDeclarationError("invalid historical review progress pairing")
        state = {
            "work_item_ref": case.get("work_item_ref"),
            "human_assessment_count": case.get("human_assessment_count"),
            "latest_human_assessed_status": case.get("latest_human_assessed_status"),
            "provider_check_count": case.get("provider_check_count"),
            "disputed_check_count": case.get("disputed_check_count"),
            "historical_last_provider_state": case.get("historical_last_provider_state"),
            "operator_action_codes": action.get("operator_action_codes"),
            "prerequisite_review_pending": action.get("prerequisite_review_pending"),
        }
        _validate_state(state)
        states.append(state)
    return states


def _validate_state(state: Any) -> None:
    if not isinstance(state, dict) or set(state) != _STATE_FIELDS:
        raise CaseDeclarationError("invalid progress checkpoint state")
    actions = state["operator_action_codes"]
    if (not isinstance(state["work_item_ref"], str)
            or not state["work_item_ref"].startswith("owg:w:")
            or len(state["work_item_ref"]) != 38
            or any(c not in "0123456789abcdef" for c in state["work_item_ref"][6:])
            or type(state["human_assessment_count"]) is not int
            or not 0 <= state["human_assessment_count"] <= 32
            or state["latest_human_assessed_status"] not in ("met", "not_met", "unknown")
            or type(state["provider_check_count"]) is not int
            or not 0 <= state["provider_check_count"] <= 32
            or type(state["disputed_check_count"]) is not int
            or not 0 <= state["disputed_check_count"] <= state["provider_check_count"]
            or state["historical_last_provider_state"] not in ("paid", "unknown")
            or (state["provider_check_count"] == 0 and
                state["historical_last_provider_state"] != "unknown")
            or not isinstance(actions, list) or len(actions) > len(_ACTIONS)
            or len(set(actions)) != len(actions)
            or any(not isinstance(a, str) or a not in _ACTIONS for a in actions)
            or type(state["prerequisite_review_pending"]) is not bool):
        raise CaseDeclarationError("invalid bounded progress checkpoint state")


def issue_review_progress(
    triage: dict[str, Any], admitted: dict[str, Any], signature: tuple,
    *, scope: str, secret: str, max_cases: int,
) -> dict[str, Any]:
    """Return a sealed, minimized baseline without retaining a new local copy."""
    plan = compose_outcome_review_action_plan(triage)
    states = _states(triage, plan)
    packet = issue_review_packet(
        triage, admitted, signature, scope=scope, secret=secret, max_cases=max_cases,
    )["packet"]
    if [row["work_item_ref"] for row in states] != packet["selected_case_refs"]:
        raise CaseDeclarationError("inconsistent progress review selection")
    _graph, _source, key = _bindings(signature, scope, secret)
    unsigned = {"format": _FORMAT, "review_packet": packet, "case_states": states}
    checkpoint = {
        **unsigned, "seal": _mac(key, b"outcome-review-progress", _canonical(unsigned)),
    }
    return {
        "format": _RESULT, "work_item_ref": triage["work_item_ref"],
        "checkpoint": checkpoint, "baseline_action_plan": plan,
        "historical_only_requires_fresh_source_recheck": True,
        "customer_outcome_verified": False, "business_obligation_verified": False,
        "agent_caused_outcome_verified": False, "agent_execution_authorized": False,
        "source_read_performed": False, "source_write_performed": False,
        "not_synced": True,
    }


def validate_review_progress(
    checkpoint: Any, admitted: dict[str, Any], signature: tuple,
    *, scope: str, secret: str,
) -> dict[str, Any]:
    """Verify token/graph/expiry before any private ledger reads."""
    if not isinstance(checkpoint, dict) or set(checkpoint) != _FIELDS:
        raise CaseDeclarationError("invalid progress checkpoint fields")
    if checkpoint["format"] != _FORMAT or not isinstance(checkpoint["seal"], str):
        raise CaseDeclarationError("invalid progress checkpoint format")
    validated = validate_review_packet(
        checkpoint["review_packet"], admitted, signature, scope=scope, secret=secret,
    )
    states = checkpoint["case_states"]
    refs = validated["selected_case_refs"]
    if (not isinstance(states, list) or len(states) != len(refs)
            or len(states) > 4):
        raise CaseDeclarationError("invalid progress checkpoint selection")
    for state, ref in zip(states, refs):
        _validate_state(state)
        if state["work_item_ref"] != ref:
            raise CaseDeclarationError("stale progress checkpoint selection")
    _graph, _source, key = _bindings(signature, scope, secret)
    unsigned = {field: checkpoint[field] for field in _FIELDS if field != "seal"}
    expected = _mac(key, b"outcome-review-progress", _canonical(unsigned))
    if not hmac.compare_digest(checkpoint["seal"], expected):
        raise CaseDeclarationError("tampered progress checkpoint")
    return validated


def compare_review_progress(
    checkpoint: dict[str, Any], triage: dict[str, Any], admitted: dict[str, Any],
    signature: tuple, *, scope: str, secret: str,
) -> dict[str, Any]:
    """Compare evidence and review work, never infer business success."""
    validate_review_progress(checkpoint, admitted, signature, scope=scope, secret=secret)
    plan = compose_outcome_review_action_plan(triage)
    current = _states(triage, plan)
    previous = checkpoint["case_states"]
    if [s["work_item_ref"] for s in current] != [s["work_item_ref"] for s in previous]:
        raise CaseDeclarationError("changed progress dependency selection")
    changed = []
    for before, after in zip(previous, current):
        # A reduced event count means retention, reset or replaced history.
        # Never misreport that as an improvement.
        for key in ("human_assessment_count", "provider_check_count", "disputed_check_count"):
            if after[key] < before[key]:
                raise CaseDeclarationError("stale progress history reset or revoked")
        old_actions = before["operator_action_codes"]
        new_actions = after["operator_action_codes"]
        delta = {
            "work_item_ref": after["work_item_ref"],
            "new_human_assessments": after["human_assessment_count"] - before["human_assessment_count"],
            "new_provider_checks": after["provider_check_count"] - before["provider_check_count"],
            "new_provider_disputes": after["disputed_check_count"] - before["disputed_check_count"],
            "human_status_before": before["latest_human_assessed_status"],
            "human_status_after": after["latest_human_assessed_status"],
            "historical_provider_state_before": before["historical_last_provider_state"],
            "historical_provider_state_after": after["historical_last_provider_state"],
            "resolved_review_action_codes": [a for a in old_actions if a not in new_actions],
            "new_review_action_codes": [a for a in new_actions if a not in old_actions],
            "prerequisite_review_pending_before": before["prerequisite_review_pending"],
            "prerequisite_review_pending_after": after["prerequisite_review_pending"],
            "historical_evidence_changed": before != after,
            "current_provider_state_verified": False,
            "customer_outcome_verified": False,
        }
        changed.append(delta)
    return {
        "format": _RESULT, "work_item_ref": triage["work_item_ref"],
        "comparison": "changed" if any(c["historical_evidence_changed"] for c in changed) else "unchanged",
        "cases": changed, "case_count": len(changed),
        "unscanned_prerequisite_count": triage["unscanned_prerequisite_count"],
        "review_action_plan": plan,
        "comparison_is_historical_not_business_success": True,
        "separate_ledger_reads_not_atomic_snapshot": True,
        "historical_only_requires_fresh_source_recheck": True,
        "current_provider_operation_verified": False,
        "customer_outcome_verified": False, "business_obligation_verified": False,
        "agent_caused_outcome_verified": False, "agent_execution_authorized": False,
        "source_read_performed": False, "source_write_performed": False,
        "not_synced": True,
    }


__all__ = ["issue_review_progress", "validate_review_progress", "compare_review_progress"]
