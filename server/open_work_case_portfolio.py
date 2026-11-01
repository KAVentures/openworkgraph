from __future__ import annotations

"""Explicit multi-case status portfolio for reviewed Open Work declarations.

This is a pure projection over the existing declaration DAG and source case
context. The dashboard REST route owns authentication, consent and source reads.
No inferred obligations, business completion, agent permission or persistence.
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_dossier import (
    admitted_case_dossier_snapshot, compose_reviewed_case_dossier,
)

_MAX_SELECTED = 4


def admit_portfolio(
    snapshot: dict[str, Any], cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], tuple[tuple[tuple[Any, ...], ...], ...]]:
    """Preflight every selected case AND its reviewed transitive prerequisites.

    Return immutable signatures for a full recheck after EACH provider read.
    This prevents one valid selected case from triggering a read before another
    selected case's stale/foreign/revoked declaration is rejected.
    """
    if not isinstance(cases, list) or not 1 <= len(cases) <= _MAX_SELECTED:
        raise CaseDeclarationError("invalid bounded portfolio selection")
    seen: set[str] = set()
    admitted: list[dict[str, Any]] = []
    signatures: list[tuple[tuple[Any, ...], ...]] = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "work_item_ref", "expected_revision", "expected_edit_token",
        }:
            raise CaseDeclarationError("invalid exact portfolio case selection")
        ref = case["work_item_ref"]
        if not isinstance(ref, str) or ref in seen:
            raise CaseDeclarationError("duplicate or invalid portfolio case")
        seen.add(ref)
        item, signature = admitted_case_dossier_snapshot(
            snapshot, work_item_ref=ref,
            expected_revision=case["expected_revision"],
            expected_edit_token=case["expected_edit_token"],
        )
        admitted.append(item)
        signatures.append(signature)
    return admitted, tuple(signatures)


def compose_portfolio_status(
    admitted: list[dict[str, Any]], contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Expose only reviewed codes and minimized provider/retained evidence."""
    if (not isinstance(admitted, list) or not isinstance(contexts, list)
        or not 1 <= len(admitted) <= _MAX_SELECTED
        or len(admitted) != len(contexts)):
        raise CaseDeclarationError("invalid bounded portfolio projection")
    statuses = []
    for item, context in zip(admitted, contexts):
        minimized = compose_reviewed_case_dossier(item, context)
        statuses.append({
            "work_item_ref": item["work_item_ref"],
            "owner_declaration": item["owner_declaration"],
            "declared_prerequisite_count": item["declared_prerequisite_count"],
            # Only selected cases are scanned. A prerequisite that also appears
            # as another selected case is not a claim of atomic provider state.
            "prerequisite_status_not_implicitly_verified": True,
            "source_reported_status": minimized["source_reported_status"],
            "retained_observation_summary": minimized["retained_observation_summary"],
            "related_record_history_incomplete": minimized["related_record_history_incomplete"],
            "expected_outcome_verified": False,
        })
    return {
        "format": "openworkgraph.local-reviewed-case-portfolio.v1",
        "checked_case_count": len(statuses),
        "case_statuses": statuses,
        "source_checks_sequential_not_atomic": True,
        "source_inventory_globally_complete": False,
        "source_scan_explicitly_requested": True,
        "source_write_performed": False,
        "business_obligation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }


__all__ = ["admit_portfolio", "compose_portfolio_status"]
