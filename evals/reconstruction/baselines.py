from __future__ import annotations

from collections import defaultdict
from typing import Any

from .scoring import aggregate


def _prediction(case: dict[str, Any], groups: dict[str, list[str]]) -> dict[str, Any]:
    workflows = []
    assigned: set[str] = set()
    for idx, event_ids in enumerate(groups.values(), start=1):
        if not event_ids:
            continue
        assigned.update(event_ids)
        workflows.append({
            "prediction_id": f"baseline_{idx}",
            "event_ids": event_ids,
            "ordered_event_ids": event_ids,
            "automation_relevant_event_ids": event_ids,
            "ui_mechanic_event_ids": [],
        })
    all_ids = [str(e["event_id"]) for e in case["presented_evidence"]]
    return {
        "case_id": case["case_id"],
        "workflows": workflows,
        "unassigned_event_ids": [event_id for event_id in all_ids if event_id not in assigned],
        "insufficient_evidence": False,
    }


def group_by_tab(case: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[str]] = defaultdict(list)
    for event in case["presented_evidence"]:
        tab = str(event.get("tab_context_id") or "")
        if tab:
            groups[tab].append(str(event["event_id"]))
    return _prediction(case, groups)


def group_by_resource(case: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[str]] = defaultdict(list)
    for event in case["presented_evidence"]:
        ref = ((event.get("metadata") or {}).get("resource_reference") or {}).get("resource_ref")
        if ref:
            groups[str(ref)].append(str(event["event_id"]))
    return _prediction(case, groups)


def group_by_position_parity(case: dict[str, Any]) -> dict[str, Any]:
    """Deliberately naive alternating-position shortcut."""
    groups: dict[str, list[str]] = defaultdict(list)
    for index, event in enumerate(case["presented_evidence"]):
        groups[str(index % 2)].append(str(event["event_id"]))
    return _prediction(case, groups)


def group_by_application(case: dict[str, Any]) -> dict[str, Any]:
    """Deliberately naive shortcut that treats each application as one workflow."""
    groups: dict[str, list[str]] = defaultdict(list)
    for event in case["presented_evidence"]:
        groups[str(event.get("app") or "unknown")].append(str(event["event_id"]))
    return _prediction(case, groups)


def score_baselines(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        "tab_context": aggregate(cases, [group_by_tab(case) for case in cases]),
        "resource_reference": aggregate(cases, [group_by_resource(case) for case in cases]),
        "position_parity": aggregate(cases, [group_by_position_parity(case) for case in cases]),
        "application": aggregate(cases, [group_by_application(case) for case in cases]),
    }
