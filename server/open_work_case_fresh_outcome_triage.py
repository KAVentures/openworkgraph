from __future__ import annotations

"""One explicit fresh provider check alongside retained human/provider evidence.

Only the source-operation receipt reader can establish a *current* provider
operation. Human assessments and older local receipts are never promoted into
customer outcome, business obligation, or agent causality verification.
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError

_FORMAT = "openworkgraph.local-fresh-outcome-evidence-triage.v1"
_PROVIDER_FORMAT = "openworkgraph.local-case-provider-operation-verification.v1"
_HISTORICAL_FORMAT = "openworkgraph.local-reviewed-outcome-evidence-triage.v1"


def compose_fresh_outcome_triage(
    historical: dict[str, Any], verification: dict[str, Any],
) -> dict[str, Any]:
    """Combine independently admitted histories with one fresh source read.

    This pure projection does not access Stripe or either private ledger. Its
    caller must enforce signed-link validation and full graph/source rechecks.
    """
    if (not isinstance(historical, dict)
            or historical.get("format") != _HISTORICAL_FORMAT
            or not isinstance(verification, dict)
            or verification.get("format") != _PROVIDER_FORMAT):
        raise CaseDeclarationError("invalid fresh outcome evidence inputs")
    cases = historical.get("case_reviews")
    if (not isinstance(cases, list) or not 1 <= len(cases) <= 4
            or historical.get("included_case_count") != len(cases)
            or historical.get("work_item_ref") != verification.get("work_item_ref")
            or not isinstance(cases[-1], dict)
            or cases[-1].get("work_item_ref") != historical["work_item_ref"]
            or historical.get("source_read_performed") is not False
            or historical.get("expected_outcome_verified") is not False
            or historical.get("customer_outcome_verified") is not False
            or verification.get("expected_outcome_verified") is not False
            or verification.get("customer_outcome_verified") is not False
            or verification.get("agent_caused_operation_verified") is not False
            or verification.get("agent_execution_authorized") is not False):
        raise CaseDeclarationError("conflicting fresh outcome review binding")
    state = verification.get("provider_operation_state")
    verified = verification.get("provider_operation_verified")
    if (state not in ("paid", "unknown")
            or type(verified) is not bool
            or verified != (state == "paid")):
        raise CaseDeclarationError("conflicting fresh provider operation state")

    root = cases[-1]
    human = root.get("latest_human_assessed_status")
    prior = root.get("historical_last_provider_state")
    if human not in ("met", "not_met", "unknown") or prior not in ("paid", "unknown"):
        raise CaseDeclarationError("invalid retained outcome review state")

    attention = []
    if not verified:
        attention.append("fresh_provider_operation_unverified")
        if human == "met":
            attention.append("human_met_without_fresh_paid_receipt")
        if prior == "paid":
            attention.append("historical_paid_not_freshly_reconfirmed")
    else:
        if human != "met":
            attention.append("fresh_paid_does_not_establish_human_goal_met")
        if prior != "paid":
            attention.append("fresh_paid_differs_from_retained_provider_history")
    return {
        "format": _FORMAT,
        "work_item_ref": historical["work_item_ref"],
        "historical_review": historical,
        "fresh_root_provider": {
            "work_item_ref": historical["work_item_ref"],
            "provider_operation_state": state,
            "provider_operation_verified": verified,
            "provider_verified_at": verification.get("provider_verified_at") if verified else None,
            "reason": None if verified else "independent_provider_receipt_unavailable_or_unverified",
            "human_assessed_status": human,
            "historical_last_provider_state": prior,
            "review_attention": attention,
        },
        "selected_case_count": len(cases),
        "unscanned_prerequisite_count": historical.get("unscanned_prerequisite_count"),
        "all_prerequisites_freshly_checked": False,
        "fresh_provider_check_applies_only_to_root": True,
        "historical_and_fresh_evidence_not_atomic_provider_snapshot": True,
        "current_provider_operation_verified": verified,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_read_attempted": True,
        "source_write_performed": False,
        "server_side_fresh_result_persisted": False,
        "not_synced": True,
    }


__all__ = ["compose_fresh_outcome_triage"]
