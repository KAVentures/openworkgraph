from __future__ import annotations

"""Explicit, bounded fresh provider checks for a reviewed payment-goal DAG.

This is a source-verification *batch*, not a provider-atomic snapshot or a
customer/business-outcome verdict. All links and reviews are admitted before
any provider I/O; local evidence writes are committed atomically afterward.
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_dossier import admitted_case_dossier_snapshot
from .open_work_case_declared_outcome import (
    EXPECTED, GOAL, compose_fresh_declared_payment_goal,
)
from .open_work_case_operation_verification import validate_case_record_link

_FORMAT = "openworkgraph.local-case-declared-dependency-payment-check.v1"
_MAX_CASES = 4


def admit_declared_dependency_payment_links(
    snapshot: dict[str, Any], *, work_item_ref: str,
    expected_revision: int, expected_edit_token: str,
    record_links: Any, scope: str, secret: str,
) -> dict[str, Any]:
    """Pin every transitive dependency and verify one distinct link per case."""
    root, root_signature = admitted_case_dossier_snapshot(
        snapshot, work_item_ref=work_item_ref,
        expected_revision=expected_revision, expected_edit_token=expected_edit_token,
    )
    ordered = tuple(row[0] for row in root_signature)
    if not 1 <= len(ordered) <= _MAX_CASES:
        raise CaseDeclarationError("declared payment dependency chain exceeds four cases")
    if (not isinstance(record_links, list) or len(record_links) != len(ordered)):
        raise CaseDeclarationError("one signed source link required for every reviewed case")
    rows = {row["work_item_ref"]: row for row in snapshot["items"]}
    if len(rows) != len(snapshot["items"]) or len(set(ordered)) != len(ordered):
        raise CaseDeclarationError("ambiguous reviewed payment case identity")
    cases: dict[str, dict[str, Any]] = {}
    for ref in ordered:
        row = rows[ref]
        if (row["goal_code"] != GOAL or row["expected_outcome_code"] != EXPECTED):
            raise CaseDeclarationError("every selected dependency must declare the Stripe payment goal")
        admitted, signature = admitted_case_dossier_snapshot(
            snapshot, work_item_ref=ref,
            expected_revision=row["revision"], expected_edit_token=row["edit_token"],
        )
        cases[ref] = {
            "admitted": admitted, "signature": signature,
            "revision": row["revision"], "edit_token": row["edit_token"],
        }
    resources: set[str] = set()
    for link in record_links:
        if not isinstance(link, dict):
            raise CaseDeclarationError("invalid dependency source link")
        ref = link.get("work_item_ref")
        if not isinstance(ref, str) or ref not in cases or "resource_ref" in cases[ref]:
            raise CaseDeclarationError("duplicate or foreign dependency source link")
        item = cases[ref]
        resource = validate_case_record_link(
            link, item["admitted"], item["signature"], scope=scope, secret=secret,
        )
        if resource in resources:
            raise CaseDeclarationError("one invoice cannot satisfy multiple declared cases")
        resources.add(resource)
        item["resource_ref"] = resource
    if any("resource_ref" not in item for item in cases.values()):
        raise CaseDeclarationError("missing dependency source link")
    return {"root_signature": root_signature, "ordered": ordered, "cases": cases}


def same_admitted_payment_chain(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Exact review incarnation, source record and DAG check after provider I/O."""
    if before["root_signature"] != after["root_signature"] or before["ordered"] != after["ordered"]:
        return False
    for ref in before["ordered"]:
        a, b = before["cases"][ref], after["cases"][ref]
        if (a["signature"] != b["signature"] or
            a["resource_ref"] != b["resource_ref"] or
            a["revision"] != b["revision"] or a["edit_token"] != b["edit_token"]):
            return False
    return True


def compose_declared_dependency_payment_result(
    *, root_ref: str, admitted: dict[str, Any],
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    verifications: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Show each fresh result and transition, without claiming simultaneity."""
    ordered = admitted["ordered"]
    if (root_ref not in ordered or
        set(previous) != set(ordered) or set(current) != set(ordered) or
        set(verifications) != set(ordered)):
        raise CaseDeclarationError("incomplete declared dependency payment batch")
    items: list[dict[str, Any]] = []
    for ref in ordered:
        item = admitted["cases"][ref]
        result = compose_fresh_declared_payment_goal(
            item["admitted"], verifications[ref], current[ref],
            prior_history=previous[ref],
        )
        items.append({
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
    paid = sum(item["provider_goal_met_at_check"] is True for item in items)
    return {
        "format": _FORMAT,
        "work_item_ref": root_ref,
        "cases": items,
        "reviewed_case_count": len(items),
        "fresh_source_check_count": len(items),
        "fresh_provider_paid_count": paid,
        "fresh_provider_unknown_count": len(items) - paid,
        "all_declared_provider_goals_met_at_individual_checks": paid == len(items),
        "provider_reads_are_sequential_not_atomic_snapshot": True,
        "local_evidence_appends_are_one_atomic_transaction": True,
        "prerequisite_first": True,
        "provider_paid_is_not_bank_settlement": True,
        "business_objective_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_write_performed": False,
        "not_synced": True,
    }


__all__ = [
    "admit_declared_dependency_payment_links", "same_admitted_payment_chain",
    "compose_declared_dependency_payment_result",
]
