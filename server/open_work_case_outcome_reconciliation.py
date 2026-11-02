from __future__ import annotations

"""Review triage of independently retained human and provider evidence.

This is a read-only historical join, not adjudication of a business outcome.
Each input history is independently admitted and HMAC-checked by its ledger
before this projection. The full owner-reviewed dependency DAG remains pinned.
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_dependency_history import compose_dependency_operation_history
from .open_work_case_human_dependency_history import compose_dependency_human_outcomes

_FORMAT = "openworkgraph.local-reviewed-outcome-evidence-triage.v1"


def compose_outcome_evidence_triage(
    admitted: dict[str, Any], signature: tuple,
    selected: list[dict[str, Any]],
    provider_histories: list[dict[str, Any]],
    human_histories: list[dict[str, Any]],
    *, unscanned_prerequisites: int,
) -> dict[str, Any]:
    """Join two validated projections by exact reviewed case, never by time."""
    provider = compose_dependency_operation_history(
        admitted, signature, selected, provider_histories,
        unscanned_prerequisites=unscanned_prerequisites,
    )
    human = compose_dependency_human_outcomes(
        admitted, signature, selected, human_histories,
        unscanned_prerequisites=unscanned_prerequisites,
    )
    p_rows = provider["case_histories"]
    h_rows = human["case_histories"]
    if (len(p_rows) != len(h_rows) or
            provider["selected_dependency_edges"] != human["selected_dependency_edges"]):
        raise CaseDeclarationError("conflicting historical outcome dependency selection")
    cases = []
    attention_count = 0
    for p, h in zip(p_rows, h_rows):
        for key in ("work_item_ref", "role", "owner_declared_goal_code",
                    "owner_declared_expected_outcome_code", "owner_declaration_revision"):
            if p[key] != h[key]:
                raise CaseDeclarationError("conflicting historical outcome case binding")
        status = h["latest_human_assessed_status"]
        state = p["historical_last_provider_state"]
        human_count = h["human_assessment_count"]
        check_count = p["provider_check_count"]
        reasons = []
        if not human_count:
            reasons.append("human_assessment_missing")
        if not check_count:
            reasons.append("provider_check_missing")
        elif state == "unknown":
            reasons.append("provider_history_unknown_or_disputed")
        if status == "met" and state != "paid":
            reasons.append("human_met_without_historical_paid_receipt")
        if state == "paid" and status in ("not_met", "unknown"):
            reasons.append("historical_paid_without_human_met_assessment")
        attention_count += bool(reasons)
        p_checks = [e for e in p["history_events"] if e["kind"] == "check"]
        h_events = h["history_events"]
        cases.append({
            "work_item_ref": p["work_item_ref"],
            "role": p["role"],
            "owner_declared_goal_code": p["owner_declared_goal_code"],
            "owner_declared_expected_outcome_code": p["owner_declared_expected_outcome_code"],
            "owner_declaration_revision": p["owner_declaration_revision"],
            "latest_human_assessed_status": status,
            "human_assessment_count": human_count,
            "latest_human_assessed_at": h_events[-1]["assessed_at"] if h_events else None,
            "historical_last_provider_state": state,
            "provider_check_count": check_count,
            "disputed_check_count": p["disputed_check_count"],
            "last_historical_provider_check_at": p_checks[-1]["checked_at"] if p_checks else None,
            "review_attention": reasons,
            "review_attention_present": bool(reasons),
            "historical_only_requires_fresh_source_recheck": True,
            "human_assessment_is_not_independent_verification": True,
            "current_provider_operation_verified": False,
            "expected_outcome_verified": False,
            "customer_outcome_verified": False,
        })
    return {
        "format": _FORMAT,
        "work_item_ref": admitted["work_item_ref"],
        "case_reviews": cases,
        "selected_dependency_edges": provider["selected_dependency_edges"],
        "included_case_count": len(cases),
        "declared_prerequisite_count": admitted["declared_prerequisite_count"],
        "unscanned_prerequisite_count": unscanned_prerequisites,
        "all_declared_prerequisites_included": unscanned_prerequisites == 0,
        "cases_with_review_attention": attention_count,
        "all_selected_cases_have_both_histories": all(
            c["human_assessment_count"] and c["provider_check_count"] for c in cases
        ),
        "historical_only_requires_fresh_source_recheck": True,
        "separate_local_ledger_reads_not_atomic_snapshot": True,
        "human_assessments_are_not_independent_verification": True,
        "current_provider_operation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_read_performed": False,
        "source_write_performed": False,
        "not_synced": True,
    }


__all__ = ["compose_outcome_evidence_triage"]
