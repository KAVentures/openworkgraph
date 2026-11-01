from __future__ import annotations
"""Explicit, bounded source-status projection for owner-reviewed dependencies.

Reuses the existing reviewed declaration DAG and Stripe case-context reader.
No new source permissions, inferred obligations, or customer outcome claims.
"""
from typing import Any
from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_dossier import compose_reviewed_case_dossier

def select_dependency_status_targets(admitted: dict[str, Any], *, max_cases: int) -> tuple[list[dict[str, Any]], int]:
    if type(max_cases) is not int or not 1 <= max_cases <= 4:
        raise CaseDeclarationError("invalid bounded dependency scan")
    prerequisites = admitted["declared_prerequisites"]
    owner = admitted["owner_declaration"]
    target = admitted["work_item_ref"]
    if not isinstance(prerequisites, list) or len(prerequisites) > 49:
        raise CaseDeclarationError("invalid dependency snapshot")
    refs = [p["work_item_ref"] for p in prerequisites]
    if len(set(refs + [target])) != len(refs) + 1:
        raise CaseDeclarationError("duplicate dependency identity")
    selected = [
        {"work_item_ref": p["work_item_ref"], "role": "declared_prerequisite",
         "goal_code": p["goal_code"], "outcome_code": p["expected_outcome_code"],
         "revision": p["revision"]}
        for p in prerequisites[:max_cases - 1]
    ]
    selected.append({
        "work_item_ref": target, "role": "requested_case",
        "goal_code": owner["goal_code"],
        "outcome_code": owner["expected_outcome_code"],
        "revision": owner["revision"],
    })
    return selected, len(prerequisites) - (len(selected) - 1)


def compose_dependency_status_snapshot(
    admitted: dict[str, Any], selected: list[dict[str, Any]],
    contexts: list[dict[str, Any]], *, unscanned_prerequisites: int,
) -> dict[str, Any]:
    if (not 1 <= len(selected) <= 4 or len(selected) != len(contexts)
        or type(unscanned_prerequisites) is not int or unscanned_prerequisites < 0):
        raise CaseDeclarationError("invalid bounded dependency status projection")
    statuses = []
    for item, context in zip(selected, contexts):
        minimized = compose_reviewed_case_dossier(
            {"work_item_ref": item["work_item_ref"]}, context)
        statuses.append({
            "work_item_ref": item["work_item_ref"],
            "role": item["role"],
            "owner_declared_goal_code": item["goal_code"],
            "owner_declared_expected_outcome_code": item["outcome_code"],
            "owner_declaration_revision": item["revision"],
            "owner_declaration_not_source_verified": True,
            "source_reported_status": minimized["source_reported_status"],
            "retained_observation_summary": minimized["retained_observation_summary"],
            "related_record_history_incomplete": minimized["related_record_history_incomplete"],
            "expected_outcome_verified": False,
        })
    return {
        "format": "openworkgraph.local-reviewed-dependency-status.v1",
        "work_item_ref": admitted["work_item_ref"],
        "owner_declaration": admitted["owner_declaration"],
        "declared_prerequisite_count": admitted["declared_prerequisite_count"],
        "checked_case_count": len(statuses),
        "unscanned_prerequisite_count": unscanned_prerequisites,
        "all_declared_prerequisites_scanned": unscanned_prerequisites == 0,
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
