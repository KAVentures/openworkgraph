from __future__ import annotations

"""Bounded, prerequisite-first operator actions from independently checked histories.

Only the existing authenticated evidence-triage route may call this composer.
It never reads a provider, mutates evidence, infers obligations, or authorizes
an agent. Historical provider status is never promoted to current truth.
"""
import re
from typing import Any

from .open_work_case_declarations import CaseDeclarationError

_FORMAT = "openworkgraph.local-reviewed-outcome-action-plan.v1"
_TRIAGE = "openworkgraph.local-reviewed-outcome-evidence-triage.v1"
_REF = re.compile(r"^owg:w:[0-9a-f]{32}$")
_PAYMENT = "stripe_invoice_paid_reported"
_ROUTES = {
    "record_human_assessment": "/v1/open-work/declarations/human-outcomes/assess",
    "review_human_provider_disagreement": "/v1/open-work/declarations/human-outcomes/evidence-triage",
    "review_historical_provider_evidence": "/v1/open-work/declarations/operation-ledger/history",
    "obtain_signed_invoice_link": "/v1/open-work/declarations/operation-link",
    "fresh_declared_payment_check": "/v1/open-work/declarations/source-goals/verify-payment",
    "expand_dependency_review": "/v1/open-work/declarations/human-outcomes/evidence-triage",
}


def compose_outcome_review_action_plan(triage: dict[str, Any]) -> dict[str, Any]:
    """Provide concrete operator next steps without inventing a business duty.

    Input must be the bounded, admitted, HMAC-ledger-backed projection from
    compose_outcome_evidence_triage; never use client-supplied triage JSON.
    """
    if not isinstance(triage, dict) or triage.get("format") != _TRIAGE:
        raise CaseDeclarationError("invalid reviewed outcome triage for action plan")
    rows = triage.get("case_reviews")
    omitted = triage.get("unscanned_prerequisite_count")
    edges = triage.get("selected_dependency_edges")
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 4
            or triage.get("included_case_count") != len(rows)
            or type(omitted) is not int or omitted < 0
            or not isinstance(edges, list) or len(edges) > 8
            or triage.get("work_item_ref") != rows[-1].get("work_item_ref")
            or triage.get("customer_outcome_verified") is not False
            or triage.get("source_read_performed") is not False
            or triage.get("agent_execution_authorized") is not False):
        raise CaseDeclarationError("incomplete historical action plan evidence")
    refs = [row.get("work_item_ref") if isinstance(row, dict) else None for row in rows]
    if (any(not isinstance(ref, str) or not _REF.fullmatch(ref) for ref in refs)
            or len(set(refs)) != len(refs)):
        raise CaseDeclarationError("invalid action plan case identities")
    positions = {ref: i for i, ref in enumerate(refs)}
    prerequisites: dict[str, list[str]] = {ref: [] for ref in refs}
    seen_edges = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise CaseDeclarationError("invalid reviewed dependency edge")
        pre, dep = edge.get("prerequisite_ref"), edge.get("dependent_ref")
        if (pre not in positions or dep not in positions or
                positions[pre] >= positions[dep] or (pre, dep) in seen_edges or
                edge.get("edge_type") != "owner_declared_dependency" or
                edge.get("source_verified") is not False):
            raise CaseDeclarationError("conflicting prerequisite-first review graph")
        seen_edges.add((pre, dep))
        prerequisites[dep].append(pre)

    plans = []
    unresolved: dict[str, bool] = {}
    for row in rows:
        ref = row["work_item_ref"]
        human = row.get("latest_human_assessed_status")
        provider = row.get("historical_last_provider_state")
        checks = row.get("provider_check_count")
        assessments = row.get("human_assessment_count")
        disputes = row.get("disputed_check_count")
        if (human not in ("met", "not_met", "unknown")
                or provider not in ("paid", "unknown")
                or type(checks) is not int or not 0 <= checks <= 32
                or type(assessments) is not int or not 0 <= assessments <= 32
                or type(disputes) is not int or not 0 <= disputes <= checks
                or (checks == 0 and provider != "unknown")
                or not isinstance(row.get("owner_declared_expected_outcome_code"), str)
                or row.get("work_item_ref") != ref):
            raise CaseDeclarationError("invalid reviewed case action evidence")
        payment = row["owner_declared_expected_outcome_code"] == _PAYMENT
        conflict = payment and ((human == "met" and provider != "paid")
                                or (provider == "paid" and human in ("not_met", "unknown")
                                    and assessments > 0))
        actions: list[str] = []
        if not assessments:
            actions.append("record_human_assessment")
        if conflict:
            actions.append("review_human_provider_disagreement")
        if payment:
            if not checks:
                actions.append("obtain_signed_invoice_link")
            # Even a historical paid receipt is not a current verification.
            actions.append("fresh_declared_payment_check")
        elif not checks or provider == "unknown" or disputes:
            actions.append("review_historical_provider_evidence")
        if ref == refs[-1] and omitted:
            actions.insert(0, "expand_dependency_review")
        needs_review = (not assessments or not checks or provider == "unknown"
                        or conflict or bool(disputes))
        unresolved[ref] = bool(needs_review)
        pending = [pre for pre in prerequisites[ref] if unresolved[pre]]
        plans.append({
            "work_item_ref": ref,
            "role": row.get("role"),
            "owner_declaration_revision": row.get("owner_declaration_revision"),
            "prerequisite_refs": prerequisites[ref],
            "prerequisites_with_unresolved_review": pending,
            "prerequisite_review_pending": bool(pending or (ref == refs[-1] and omitted)),
            "human_assessment_missing": assessments == 0,
            "provider_history_missing": checks == 0,
            "historical_provider_unknown_or_disputed": provider == "unknown" or bool(disputes),
            "human_provider_disagreement_requires_review": bool(conflict),
            "historical_provider_state": provider,
            "operator_action_codes": actions,
            "operator_actions": [{"code": code, "existing_rest_path": _ROUTES[code]}
                                 for code in actions],
            "review_state": "attention_required" if needs_review else "historical_evidence_present",
            "current_source_state_verified": False,
            "customer_outcome_verified": False,
            "agent_execution_authorized": False,
        })
    return {
        "format": _FORMAT,
        "work_item_ref": triage["work_item_ref"],
        "cases": plans,
        "prerequisite_first": True,
        "included_case_count": len(plans),
        "unscanned_prerequisite_count": omitted,
        "all_prerequisites_reviewed": omitted == 0,
        "cases_needing_human_review": sum(unresolved.values()),
        "operator_action_count": sum(len(row["operator_action_codes"]) for row in plans),
        "actions_are_suggestions_not_obligations": True,
        "historical_evidence_is_not_current_provider_state": True,
        "separate_ledger_reads_not_atomic_snapshot": True,
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_read_performed": False,
        "source_write_performed": False,
        "not_synced": True,
    }


__all__ = ["compose_outcome_review_action_plan"]
