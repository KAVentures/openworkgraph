from __future__ import annotations

"""Explicit, bounded source checks for independent reviewed portfolio cases.

Only an authenticated REST caller may trigger provider reads. This module
validates exact owner-review incarnations and signed record links, then composes
the results of one atomic private-ledger append. It grants no agent authority.
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_dossier import admitted_case_dossier_snapshot
from .open_work_case_declared_outcome import (
    EXPECTED, require_declared_payment_goal, compose_fresh_declared_payment_goal,
)
from .open_work_case_operation_verification import validate_case_record_link

_FORMAT = "openworkgraph.local-case-declared-portfolio-payment-check.v1"
_MAX_CASES = 4
_SELECTION_FIELDS = frozenset({
    "work_item_ref", "expected_revision", "expected_edit_token", "record_link",
})


def admit_selected_portfolio_payment_links(
    snapshot: dict[str, Any], *, selections: Any, scope: str, secret: str,
) -> dict[str, Any]:
    """Require 2–4 distinct reviewed roots, independent of each other.

    Each root's *entire* transitive prerequisite DAG is admitted by the
    existing dossier engine. Shared prerequisites are allowed, but no selected
    root may be a prerequisite of another selected root: that is the separate
    dependency-payment API's responsibility.
    """
    if not isinstance(selections, list) or not 2 <= len(selections) <= _MAX_CASES:
        raise CaseDeclarationError("select two to four reviewed portfolio payment cases")
    cases: dict[str, dict[str, Any]] = {}
    resources: set[str] = set()
    for selected in selections:
        if not isinstance(selected, dict) or set(selected) != _SELECTION_FIELDS:
            raise CaseDeclarationError("exact selected portfolio payment fields required")
        ref = selected["work_item_ref"]
        if not isinstance(ref, str) or ref in cases:
            raise CaseDeclarationError("duplicate or invalid selected payment case")
        admitted, signature = admitted_case_dossier_snapshot(
            snapshot, work_item_ref=ref,
            expected_revision=selected["expected_revision"],
            expected_edit_token=selected["expected_edit_token"],
        )
        require_declared_payment_goal(admitted)
        resource = validate_case_record_link(
            selected["record_link"], admitted, signature,
            scope=scope, secret=secret,
        )
        if resource in resources:
            raise CaseDeclarationError("one invoice cannot satisfy multiple selected goals")
        resources.add(resource)
        cases[ref] = {
            "admitted": admitted,
            "signature": signature,
            "revision": selected["expected_revision"],
            "edit_token": selected["expected_edit_token"],
            "resource_ref": resource,
        }
    selected_roots = set(cases)
    for ref, case in cases.items():
        if any(node[0] in selected_roots - {ref} for node in case["signature"]):
            raise CaseDeclarationError("selected cases are dependent; use dependency payment verification")
    return {"ordered": tuple(cases), "cases": cases}


def same_selected_portfolio_payment_reviews(
    before: dict[str, Any], after: dict[str, Any],
) -> bool:
    """Re-admit every selected case and its full DAG after each source read."""
    if before["ordered"] != after["ordered"]:
        return False
    for ref in before["ordered"]:
        a, b = before["cases"][ref], after["cases"][ref]
        if (a["signature"] != b["signature"] or
            a["resource_ref"] != b["resource_ref"] or
            a["revision"] != b["revision"] or
            a["edit_token"] != b["edit_token"]):
            return False
    return True


def compose_selected_portfolio_payment_result(
    *, selected: dict[str, Any],
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    verifications: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return fresh, source-limited outcomes, never inferred customer success."""
    ordered = selected["ordered"]
    if (set(previous) != set(ordered) or set(current) != set(ordered)
            or set(verifications) != set(ordered)):
        raise CaseDeclarationError("incomplete selected portfolio payment checks")
    cases = []
    for ref in ordered:
        result = compose_fresh_declared_payment_goal(
            selected["cases"][ref]["admitted"], verifications[ref],
            current[ref], prior_history=previous[ref],
        )
        cases.append({
            "work_item_ref": ref,
            "declared_expected_outcome_code": EXPECTED,
            "provider_goal_state_at_check": result["provider_declared_goal_state_at_check"],
            "provider_goal_met_at_check": result["provider_declared_goal_met_at_check"],
            "provider_verified_at": result["provider_verified_at"],
            "provider_state_transition": result["provider_state_transition"],
            "prior_historical_provider_state": result["prior_historical_provider_state"],
            "prior_last_check_disputed": result["prior_last_check_disputed"],
            "provider_check_count": result["provider_check_count"],
        })
    paid = sum(row["provider_goal_met_at_check"] is True for row in cases)
    return {
        "format": _FORMAT,
        "cases": cases,
        "selected_case_count": len(cases),
        "fresh_source_check_count": len(cases),
        "fresh_provider_paid_count": paid,
        "fresh_provider_unknown_count": len(cases) - paid,
        "all_declared_provider_goals_met_at_individual_checks": paid == len(cases),
        "provider_reads_are_sequential_not_atomic_snapshot": True,
        "local_evidence_appends_are_one_atomic_transaction": True,
        "source_read_performed": True,
        "source_write_performed": False,
        "provider_paid_is_not_bank_settlement": True,
        "business_objective_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }


__all__ = [
    "admit_selected_portfolio_payment_links",
    "same_selected_portfolio_payment_reviews",
    "compose_selected_portfolio_payment_result",
]
