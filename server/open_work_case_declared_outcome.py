from __future__ import annotations

"""Narrow, source-verifiable owner-declared Stripe payment goal.

A provider-reported paid invoice can satisfy ONLY the explicit local declaration
"Stripe reports this exact invoice paid at verification time". It does not
establish bank settlement, customer satisfaction, obligation discharge or
agent causality. This module neither reads providers nor stores credentials.
"""
from typing import Any

from .open_work_case_declarations import CaseDeclarationError

EXPECTED = "stripe_invoice_paid_reported"
GOAL = "verify_operation"
_PROVIDER_FORMAT = "openworkgraph.local-case-provider-operation-verification.v1"
_LEDGER_FORMAT = "openworkgraph.local-case-operation-evidence-ledger.v1"
_FORMAT = "openworkgraph.local-case-declared-source-goal-verification.v1"
_HISTORY_FORMAT = "openworkgraph.local-case-declared-source-goal-history.v1"


def require_declared_payment_goal(admitted: dict[str, Any]) -> str:
    """Reject unrelated or malformed declarations BEFORE any provider read."""
    if not isinstance(admitted, dict):
        raise CaseDeclarationError("invalid reviewed source goal")
    owner = admitted.get("owner_declaration")
    if not isinstance(owner, dict) or (
        owner.get("goal_code") != GOAL or
        owner.get("expected_outcome_code") != EXPECTED
    ):
        raise CaseDeclarationError("reviewed case has no matching source-verifiable payment goal")
    ref = admitted.get("work_item_ref")
    if not isinstance(ref, str) or not ref.startswith("owg:w:"):
        raise CaseDeclarationError("conflicting reviewed source goal identity")
    return ref


def _ledger(admitted: dict[str, Any], history: dict[str, Any]) -> tuple[int, int, str]:
    ref = require_declared_payment_goal(admitted)
    if (not isinstance(history, dict) or
        history.get("format") != _LEDGER_FORMAT or
        history.get("work_item_ref") != ref or
        history.get("historical_only_requires_fresh_source_recheck") is not True or
        history.get("current_provider_operation_verified") is not False or
        history.get("expected_outcome_verified") is not False):
        raise CaseDeclarationError("invalid declared goal provider history")
    count = history.get("provider_check_count")
    disputes = history.get("disputed_check_count")
    state = history.get("last_observed_provider_state")
    if (type(count) is not int or not 0 <= count <= 32 or
        type(disputes) is not int or not 0 <= disputes <= count or
        state not in ("paid", "unknown") or
        (count == 0 and state != "unknown")):
        raise CaseDeclarationError("invalid declared goal provider history state")
    return count, disputes, state


def compose_fresh_declared_payment_goal(
    admitted: dict[str, Any], verification: dict[str, Any],
    history: dict[str, Any],
    *, prior_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fresh checked status and durable evidence are distinct typed facts."""
    ref = require_declared_payment_goal(admitted)
    count, disputes, historical = _ledger(admitted, history)
    if (not isinstance(verification, dict) or
        verification.get("format") != _PROVIDER_FORMAT or
        verification.get("work_item_ref") != ref or
        verification.get("expected_operation") != "stripe_invoice_paid" or
        verification.get("provider_operation_state") not in ("paid", "unknown") or
        verification.get("provider_operation_verified") is not
            (verification.get("provider_operation_state") == "paid") or
        any(verification.get(flag) is not False for flag in (
            "expected_outcome_verified", "customer_outcome_verified",
            "agent_caused_operation_verified", "agent_execution_authorized",
        ))):
        raise CaseDeclarationError("invalid fresh declared source goal verification")
    paid = verification["provider_operation_verified"] is True
    stamp = verification.get("provider_verified_at")
    if (paid and not isinstance(stamp, str)) or (not paid and stamp is not None):
        raise CaseDeclarationError("invalid source verification timestamp")
    if count == 0:
        raise CaseDeclarationError("fresh source check was not durably recorded")
    # The just-appended event must agree with the returned ledger, including
    # a prior disputed paid receipt followed by a fresh unknown.
    if historical != ("paid" if paid else "unknown"):
        raise CaseDeclarationError("source check and retained history conflict")
    transition: dict[str, Any] = {}
    if prior_history is not None:
        previous_count, _previous_disputes, previous_state = _ledger(admitted, prior_history)
        before_events = prior_history.get("events")
        after_events = history.get("events")
        if (not isinstance(before_events, list) or not isinstance(after_events, list)
                or len(after_events) != len(before_events) + 1
                or after_events[:-1] != before_events
                or count != previous_count + 1
                or len(after_events) > 32
                or not isinstance(after_events[-1], dict)
                or after_events[-1].get("kind") != "check"
                or after_events[-1].get("sequence") != len(after_events)
                or after_events[-1].get("provider_state") != ("paid" if paid else "unknown")
                or after_events[-1].get("disputed") is not False):
            raise CaseDeclarationError("conflicting source goal evidence transition")
        prior_checks = [event for event in before_events
                        if isinstance(event, dict) and event.get("kind") == "check"]
        if len(prior_checks) != previous_count:
            raise CaseDeclarationError("invalid prior source goal evidence")
        if previous_count == 0:
            change = "first_check"
        elif previous_state == "unknown" and paid:
            change = "unknown_to_paid"
        elif previous_state == "paid" and not paid:
            change = "paid_to_unknown"
        else:
            change = "unchanged_paid" if paid else "unchanged_unknown"
        transition = {
            "prior_historical_provider_state": previous_state,
            "prior_provider_check_count": previous_count,
            "prior_last_check_disputed": bool(prior_checks and prior_checks[-1].get("disputed") is True),
            "provider_state_transition": change,
            "observed_provider_state_changed": change in ("unknown_to_paid", "paid_to_unknown"),
            "prior_unknown_does_not_mean_unpaid": True,
            "paid_to_unknown_does_not_prove_payment_reversal": True,
            "transition_based_on_atomically_fenced_local_ledger": True,
        }
    return {
        "format": _FORMAT,
        "work_item_ref": ref,
        **transition,
        "owner_declared_expected_outcome_code": EXPECTED,
        "provider_declared_goal_state_at_check": "met" if paid else "unknown",
        "provider_declared_goal_met_at_check": paid,
        "provider_verified_at": stamp if paid else None,
        "provider_check_count": count,
        "disputed_provider_check_count": disputes,
        "historical_last_provider_state": historical,
        "source_read_performed": True,
        "source_write_performed": False,
        "evidence_retained_locally": True,
        "provider_reported_payment_not_bank_settlement": True,
        "provider_goal_is_not_independently_verified_customer_success": True,
        "business_objective_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }


def compose_historical_declared_payment_goal(
    admitted: dict[str, Any], history: dict[str, Any],
) -> dict[str, Any]:
    """A retained paid check is never promoted into current provider status."""
    ref = require_declared_payment_goal(admitted)
    count, disputes, state = _ledger(admitted, history)
    return {
        "format": _HISTORY_FORMAT,
        "work_item_ref": ref,
        "owner_declared_expected_outcome_code": EXPECTED,
        "provider_check_count": count,
        "disputed_provider_check_count": disputes,
        "historical_last_provider_state": state,
        "historical_last_check_reported_paid": state == "paid",
        "provider_declared_goal_state_now": "unknown",
        "provider_declared_goal_met_now": False,
        "fresh_source_recheck_required": True,
        "source_read_performed": False,
        "source_write_performed": False,
        "provider_reported_payment_not_bank_settlement": True,
        "business_objective_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }


__all__ = [
    "EXPECTED", "GOAL", "require_declared_payment_goal",
    "compose_fresh_declared_payment_goal",
    "compose_historical_declared_payment_goal",
]
