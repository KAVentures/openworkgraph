from __future__ import annotations

"""Reviewed dependency DAG joined to retained provider-operation history.

Read-only composition: caller must admit every case, validate the exact owner
DAG and retrieve each history through the existing private SQLite ledger.
Historical provider receipts never establish a current or customer outcome.
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError

_FORMAT = "openworkgraph.local-reviewed-dependency-operation-history.v1"
_HISTORY_FORMAT = "openworkgraph.local-case-operation-evidence-ledger.v1"
_EVENT_KEYS = (
    "sequence", "kind", "checked_at", "provider_state",
    "provider_verified_at", "disputes_sequence", "dispute_reason", "disputed",
)


def compose_dependency_operation_history(
    admitted: dict[str, Any],
    signature: tuple,
    selected: list[dict[str, Any]],
    histories: list[dict[str, Any]],
    *,
    unscanned_prerequisites: int,
) -> dict[str, Any]:
    """Minimize up to four independently integrity-checked ledger histories."""
    if (
        not isinstance(admitted, dict)
        or not isinstance(signature, tuple)
        or not isinstance(selected, list)
        or not isinstance(histories, list)
        or not 1 <= len(selected) <= 4
        or len(selected) != len(histories)
        or type(unscanned_prerequisites) is not int
        or unscanned_prerequisites < 0
    ):
        raise CaseDeclarationError("invalid bounded dependency evidence request")
    by_ref = {}
    for item in signature:
        if not isinstance(item, tuple) or len(item) != 8 or item[0] in by_ref:
            raise CaseDeclarationError("invalid reviewed dependency signature")
        by_ref[item[0]] = item
    selected_refs = [item.get("work_item_ref") for item in selected]
    if (
        len(set(selected_refs)) != len(selected_refs)
        or any(ref not in by_ref for ref in selected_refs)
        or selected_refs[-1] != admitted.get("work_item_ref")
        or len(signature) != len(selected) + unscanned_prerequisites
        or len(admitted.get("declared_prerequisites", [])) != len(signature) - 1
    ):
        raise CaseDeclarationError("stale or incomplete dependency evidence selection")

    rows = []
    all_checked = True
    for item, history in zip(selected, histories):
        ref = item["work_item_ref"]
        if (
            not isinstance(history, dict)
            or history.get("format") != _HISTORY_FORMAT
            or history.get("work_item_ref") != ref
            or history.get("historical_only_requires_fresh_source_recheck") is not True
            or history.get("current_provider_operation_verified") is not False
            or history.get("expected_outcome_verified") is not False
            or history.get("customer_outcome_verified") is not False
            or history.get("agent_execution_authorized") is not False
            or history.get("source_write_performed") is not False
        ):
            raise CaseDeclarationError("invalid historical provider evidence projection")
        events = history.get("events")
        checks = history.get("provider_check_count")
        disputes = history.get("disputed_check_count")
        if (
            not isinstance(events, list) or len(events) > 32
            or type(checks) is not int or checks < 0 or checks > len(events)
            or type(disputes) is not int or disputes < 0 or disputes > checks
            or history.get("event_count") != len(events)
            or history.get("last_observed_provider_state") not in ("paid", "unknown")
        ):
            raise CaseDeclarationError("invalid bounded provider evidence history")
        minimized = []
        for event in events:
            if not isinstance(event, dict):
                raise CaseDeclarationError("invalid provider evidence event")
            minimized.append({key: event.get(key) for key in _EVENT_KEYS})
        all_checked = all_checked and checks > 0
        rows.append({
            "work_item_ref": ref,
            "role": item["role"],
            "owner_declared_goal_code": item["goal_code"],
            "owner_declared_expected_outcome_code": item["outcome_code"],
            "owner_declaration_revision": item["revision"],
            "provider_check_count": checks,
            "disputed_check_count": disputes,
            "historical_last_provider_state": history["last_observed_provider_state"],
            "retained_history_present": checks > 0,
            "history_events": minimized,
            "historical_only_requires_fresh_source_recheck": True,
            "current_provider_operation_verified": False,
            "expected_outcome_verified": False,
            "customer_outcome_verified": False,
        })
    included = set(selected_refs)
    edges = [
        {
            "prerequisite_ref": prerequisite,
            "dependent_ref": item[0],
            "edge_type": "owner_declared_dependency",
            "source_verified": False,
        }
        for item in signature
        if item[0] in included
        for prerequisite in item[7]
        if prerequisite in included
    ]
    return {
        "format": _FORMAT,
        "work_item_ref": admitted["work_item_ref"],
        "case_histories": rows,
        "selected_dependency_edges": edges,
        "declared_prerequisite_count": admitted["declared_prerequisite_count"],
        "included_case_count": len(rows),
        "unscanned_prerequisite_count": unscanned_prerequisites,
        "all_declared_prerequisites_included": unscanned_prerequisites == 0,
        "every_selected_case_has_retained_provider_check": all_checked,
        "historical_provider_evidence_coverage_complete": (
            unscanned_prerequisites == 0 and all_checked
        ),
        "historical_only_requires_fresh_source_recheck": True,
        "separate_local_ledger_reads_not_atomic_snapshot": True,
        "source_read_performed": False,
        "source_write_performed": False,
        "current_provider_operation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }


__all__ = ["compose_dependency_operation_history"]
