from __future__ import annotations

"""Explicit independent provider-operation checks for a reviewed dependency DAG.

This composes existing source-joined record links and source_operation_receipt;
it neither infers customer outcomes nor creates a new source permission, graph
store, background scan, or agent authority.
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_dossier import admitted_case_dossier_snapshot
from .open_work_case_operation_verification import validate_case_record_link

_FORMAT = "openworkgraph.local-case-dependency-operations.v1"
_MAX_CASES = 4


def admit_dependency_operation_links(
    snapshot: dict[str, Any], *,
    work_item_ref: str,
    expected_revision: int,
    expected_edit_token: str,
    record_links: Any,
    scope: str,
    secret: str,
) -> tuple[tuple[tuple[Any, ...], ...], tuple[str, ...], dict[str, str]]:
    """Validate the entire reviewed DAG and every link before provider I/O.

    Each link was independently issued for its own exact transitive subgraph.
    A missing link is permitted but must be reported as not checked; an extra,
    duplicate, expired, wrong-scope or forged link is never silently skipped.
    """
    admitted, signature = admitted_case_dossier_snapshot(
        snapshot, work_item_ref=work_item_ref,
        expected_revision=expected_revision,
        expected_edit_token=expected_edit_token,
    )
    if (not isinstance(record_links, list) or len(record_links) > _MAX_CASES
        or not 1 <= len(signature) <= _MAX_CASES):
        raise CaseDeclarationError("dependency operation scan exceeds four reviewed cases")
    if admitted["work_item_ref"] != work_item_ref:
        raise CaseDeclarationError("invalid reviewed root case")
    ordered = tuple(entry[0] for entry in signature)
    rows = {item["work_item_ref"]: item for item in snapshot["items"]}
    if len(set(ordered)) != len(ordered):
        raise CaseDeclarationError("duplicate reviewed case in dependency graph")
    # This endpoint checks one explicitly declared provider operation, not an
    # arbitrary business goal or a policy review disguised as a paid invoice.
    for ref in ordered:
        row = rows[ref]
        if (row["goal_code"] != "verify_operation" or
            row["expected_outcome_code"] != "operation_receipt_reviewed"):
            raise CaseDeclarationError("dependency has no reviewed invoice operation goal")

    resources: dict[str, str] = {}
    used_resources: set[str] = set()
    for link in record_links:
        if not isinstance(link, dict):
            raise CaseDeclarationError("invalid dependency record link")
        ref = link.get("work_item_ref")
        if not isinstance(ref, str) or ref not in rows or ref not in ordered or ref in resources:
            raise CaseDeclarationError("extra or duplicate dependency record link")
        row = rows[ref]
        own, own_signature = admitted_case_dossier_snapshot(
            snapshot, work_item_ref=ref,
            expected_revision=row["revision"],
            expected_edit_token=row["edit_token"],
        )
        resource = validate_case_record_link(
            link, own, own_signature, scope=scope, secret=secret,
        )
        if resource in used_resources:
            raise CaseDeclarationError("duplicate source operation across reviewed cases")
        used_resources.add(resource)
        resources[ref] = resource
    return signature, ordered, resources


def compose_dependency_operation_result(
    *, work_item_ref: str, ordered: tuple[str, ...],
    resources: dict[str, str], verifications: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return only minimized provider status, with no receipt or source IDs."""
    if (not 1 <= len(ordered) <= _MAX_CASES or
        set(verifications) != set(resources) or
        not set(resources).issubset(ordered)):
        raise CaseDeclarationError("incomplete dependency operation verification")
    rows: list[dict[str, Any]] = []
    paid = 0
    unknown = 0
    for ref in ordered:
        if ref not in resources:
            rows.append({
                "work_item_ref": ref,
                "provider_operation_state": "not_checked",
                "provider_operation_verified": False,
                "provider_verified_at": None,
                "reason": "source_joined_record_link_not_supplied",
            })
            continue
        result = verifications[ref]
        if (not isinstance(result, dict) or
            result.get("work_item_ref") != ref or
            result.get("expected_operation") != "stripe_invoice_paid" or
            result.get("customer_outcome_verified") is not False or
            result.get("expected_outcome_verified") is not False or
            result.get("agent_execution_authorized") is not False):
            raise CaseDeclarationError("invalid provider operation verification projection")
        verified = result.get("provider_operation_verified") is True
        if verified:
            if (result.get("provider_operation_state") != "paid" or
                not isinstance(result.get("provider_verified_at"), str)):
                raise CaseDeclarationError("contradictory verified provider operation")
            paid += 1
        else:
            if result.get("provider_operation_state") != "unknown":
                raise CaseDeclarationError("contradictory unknown provider operation")
            unknown += 1
        rows.append({
            "work_item_ref": ref,
            "provider_operation_state": "paid" if verified else "unknown",
            "provider_operation_verified": verified,
            "provider_verified_at": result["provider_verified_at"] if verified else None,
            "reason": None if verified else "independent_provider_receipt_unavailable_or_unverified",
        })
    return {
        "format": _FORMAT,
        "work_item_ref": work_item_ref,
        "expected_operation": "stripe_invoice_paid",
        "cases": rows,
        "reviewed_case_count": len(ordered),
        "source_checked_count": len(resources),
        "source_join_missing_count": len(ordered) - len(resources),
        "provider_paid_count": paid,
        "provider_unknown_count": unknown,
        "all_declared_case_provider_operations_verified": (
            len(resources) == len(ordered) and paid == len(ordered)
        ),
        "all_declared_cases_checked": len(resources) == len(ordered),
        "prerequisite_first": True,
        "provider_status_not_customer_outcome": True,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
        "source_verification_explicitly_requested": True,
        "source_read_performed": bool(resources),
        "source_write_performed": False,
        "not_synced": True,
    }


__all__ = ["admit_dependency_operation_links", "compose_dependency_operation_result"]
