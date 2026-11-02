from __future__ import annotations

"""Read-only reviewed dependency graph with retained HUMAN outcome feedback.

The caller must independently admit each selected case against the current
review/retention/source scope and verify the root graph before and after each
private-ledger read. This projection never turns human assertions into source
or customer verification, even when every owner says "met".
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError

_FORMAT = "openworkgraph.local-reviewed-dependency-human-outcomes.v1"
_HISTORY_FORMAT = "openworkgraph.local-case-human-outcome-assessments.v1"


def compose_dependency_human_outcomes(
    admitted: dict[str, Any], signature: tuple,
    selected: list[dict[str, Any]], histories: list[dict[str, Any]],
    *, unscanned_prerequisites: int,
) -> dict[str, Any]:
    if (not isinstance(admitted, dict) or not isinstance(signature, tuple)
            or not isinstance(selected, list) or not isinstance(histories, list)
            or not 1 <= len(selected) <= 4 or len(selected) != len(histories)
            or type(unscanned_prerequisites) is not int or unscanned_prerequisites < 0):
        raise CaseDeclarationError("invalid bounded human outcome dependency request")
    by_ref: dict[str, tuple] = {}
    for item in signature:
        if not isinstance(item, tuple) or len(item) != 8 or item[0] in by_ref:
            raise CaseDeclarationError("invalid reviewed dependency signature")
        by_ref[item[0]] = item
    refs = [item.get("work_item_ref") for item in selected]
    if (len(set(refs)) != len(refs) or any(ref not in by_ref for ref in refs)
            or refs[-1] != admitted.get("work_item_ref")
            or len(signature) != len(selected) + unscanned_prerequisites
            or len(admitted.get("declared_prerequisites", [])) != len(signature) - 1):
        raise CaseDeclarationError("stale or incomplete dependency outcome selection")

    rows = []
    for item, history in zip(selected, histories):
        ref = item["work_item_ref"]
        events = history.get("events") if isinstance(history, dict) else None
        if (not isinstance(history, dict)
                or history.get("format") != _HISTORY_FORMAT
                or history.get("work_item_ref") != ref
                or history.get("provenance") != "explicit_local_human_assessment_not_independently_verified"
                or history.get("expected_outcome_verified") is not False
                or history.get("customer_outcome_verified") is not False
                or history.get("provider_operation_verified") is not False
                or history.get("agent_execution_authorized") is not False
                or history.get("source_read_performed") is not False
                or history.get("source_write_performed") is not False
                or not isinstance(events, list) or len(events) > 32
                or history.get("event_count") != len(events)
                or history.get("latest_human_assessed_status") not in ("met", "not_met", "unknown")):
            raise CaseDeclarationError("invalid human outcome history projection")
        minimized = []
        for index, event in enumerate(events, 1):
            if (not isinstance(event, dict) or event.get("sequence") != index
                    or event.get("kind") not in ("assessment", "correction")
                    or event.get("human_assessed_status") not in ("met", "not_met", "unknown")
                    or not isinstance(event.get("assessed_at"), str)
                    or (event["kind"] == "correction" and
                        (index < 2 or event.get("corrects_sequence") != index - 1))
                    or (event["kind"] == "assessment" and event.get("corrects_sequence") is not None)):
                raise CaseDeclarationError("invalid human outcome history event")
            minimized.append({
                "sequence": index,
                "kind": event["kind"],
                "human_assessed_status": event["human_assessed_status"],
                "reason_code": event.get("reason_code"),
                "corrects_sequence": event.get("corrects_sequence"),
                "assessed_at": event["assessed_at"],
            })
        if events and events[-1]["human_assessed_status"] != history["latest_human_assessed_status"]:
            raise CaseDeclarationError("contradictory human outcome history")
        if not events and history["latest_human_assessed_status"] != "unknown":
            raise CaseDeclarationError("missing human outcome evidence")
        rows.append({
            "work_item_ref": ref,
            "role": item["role"],
            "owner_declared_goal_code": item["goal_code"],
            "owner_declared_expected_outcome_code": item["outcome_code"],
            "owner_declaration_revision": item["revision"],
            "human_assessment_count": len(events),
            "latest_human_assessed_status": history["latest_human_assessed_status"],
            "retained_human_feedback_present": bool(events),
            "history_events": minimized,
            "expected_outcome_verified": False,
            "customer_outcome_verified": False,
        })

    included = set(refs)
    edges = [
        {"prerequisite_ref": dep, "dependent_ref": item[0],
         "edge_type": "owner_declared_dependency", "source_verified": False}
        for item in signature if item[0] in included
        for dep in item[7] if dep in included
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
        "every_selected_case_has_human_feedback": all(
            row["retained_human_feedback_present"] for row in rows
        ),
        "human_feedback_coverage_complete": (
            unscanned_prerequisites == 0
            and all(row["retained_human_feedback_present"] for row in rows)
        ),
        "human_assessments_are_not_independent_verification": True,
        "separate_local_ledger_reads_not_atomic_snapshot": True,
        "source_read_performed": False,
        "source_write_performed": False,
        "current_provider_operation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }


__all__ = ["compose_dependency_human_outcomes"]
