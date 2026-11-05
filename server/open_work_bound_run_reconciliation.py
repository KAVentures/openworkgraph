from __future__ import annotations

"""Conservative historical reconciliation of separately authenticated case ledgers.

The caller must have independently authenticated both ledgers and the run/case
binding. This projection NEVER upgrades an operator association to run identity.
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError


def reconcile_bound_run_case_history(provider: dict[str, Any],
                                     human: dict[str, Any]) -> dict[str, Any]:
    if (not isinstance(provider, dict) or not isinstance(human, dict)
            or provider.get("work_item_ref") != human.get("work_item_ref")
            or not isinstance(provider.get("work_item_ref"), str)
            or not isinstance(provider.get("events"), list)
            or not isinstance(human.get("events"), list)):
        raise CaseDeclarationError("conflicting retained case evidence")
    checks = provider.get("provider_check_count")
    disputes = provider.get("disputed_check_count")
    human_count = human.get("event_count")
    state = provider.get("last_observed_provider_state")
    assessment = human.get("latest_human_assessed_status")
    if (type(checks) is not int or type(disputes) is not int
            or type(human_count) is not int or min(checks, disputes, human_count) < 0
            or checks > len(provider["events"]) or disputes > len(provider["events"])
            or human_count != len(human["events"])
            or state not in ("paid", "open", "void", "uncollectible", "unknown", None)
            or assessment not in ("met", "not_met", "unknown")):
        raise CaseDeclarationError("invalid retained case evidence")
    reasons = []
    if not checks:
        reasons.append("provider_check_missing")
    if disputes:
        reasons.append("provider_check_disputed")
    if not human_count:
        reasons.append("human_assessment_missing")
    if state in ("unknown", None):
        reasons.append("provider_state_unknown")
    if assessment == "met" and state != "paid":
        reasons.append("human_met_without_historical_paid_receipt")
    if state == "paid" and assessment == "not_met":
        reasons.append("historical_paid_human_not_met")
    if state == "paid" and assessment == "unknown":
        reasons.append("historical_paid_human_unknown")
    return {
        "format": "openworkgraph.local-bound-run-case-reconciliation.v1",
        "work_item_ref": provider["work_item_ref"],
        "historical_provider_state": state or "unknown",
        "latest_human_assessed_status": assessment,
        "provider_check_count": checks,
        "disputed_check_count": disputes,
        "human_assessment_count": human_count,
        "review_attention": reasons,
        "review_attention_present": bool(reasons),
        "evidence_scope": "case_wide_not_run_attributed",
        "historical_only_requires_fresh_source_recheck": True,
        "separate_ledger_reads_not_atomic_snapshot": True,
        "run_case_identity_independently_verified": False,
        "agent_caused_operation_verified": False,
        "customer_outcome_verified": False,
        "expected_outcome_verified": False,
        "agent_execution_authorized": False,
    }
