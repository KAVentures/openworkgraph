from __future__ import annotations

"""Owner-reviewed, read-only portfolio payment planning and stale-check token.

This does not verify live payment, authorize provider writes, or grant agent
execution. The token binds the exact selection and private evidence history,
so optional fresh verification can refuse stale intent before Stripe I/O.
"""
from datetime import timedelta
import hmac
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_status_checkpoint import _bindings, _canonical, _clock, _mac, _parse_utc

_FORMAT = "openworkgraph.local-case-declared-portfolio-payment-preflight.v1"
_TOKEN_FORMAT = "openworkgraph.local-case-declared-portfolio-payment-preflight-token.v1"
_TOKEN_FIELDS = frozenset({
    "format", "work_item_refs", "snapshot_seal", "issued_at", "expires_at", "seal",
})
_MAX_EVENTS = 32
_TTL = timedelta(minutes=5)
_SKEW = timedelta(minutes=5)


def _material(selected: dict[str, Any], histories: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ordered = selected.get("ordered")
    if (not isinstance(ordered, tuple) or not 2 <= len(ordered) <= 4
            or len(set(ordered)) != len(ordered) or set(histories) != set(ordered)):
        raise CaseDeclarationError("incomplete selected portfolio payment preflight")
    cases = []
    for ref in ordered:
        case = selected["cases"][ref]
        history = histories[ref]
        events = history.get("events")
        count = history.get("event_count")
        if (history.get("format") != "openworkgraph.local-case-operation-evidence-ledger.v1"
                or history.get("work_item_ref") != ref
                or not isinstance(events, list) or type(count) is not int
                or count != len(events) or not 0 <= count < _MAX_EVENTS
                or history.get("historical_only_requires_fresh_source_recheck") is not True
                or history.get("current_provider_operation_verified") is not False
                or history.get("customer_outcome_verified") is not False):
            raise CaseDeclarationError("invalid or full private portfolio evidence history")
        cases.append({
            "ref": ref, "revision": case["revision"],
            "edit_token": case["edit_token"],
            "signature": case["signature"],
            "record": case["resource_ref"], "events": events,
        })
    return {"cases": cases}


def _seal(selected: dict[str, Any], histories: dict[str, dict[str, Any]], *,
          scope: str, secret: str) -> tuple[str, bytes]:
    material = _material(selected, histories)
    first = selected["ordered"][0]
    graph, source, key = _bindings(selected["cases"][first]["signature"], scope, secret)
    material["first_graph_binding"] = graph
    material["source_binding"] = source
    return _mac(key, b"declared-portfolio-payment-preflight-state", _canonical(material)), key


def issue_portfolio_payment_preflight(
    selected: dict[str, Any], histories: dict[str, dict[str, Any]], *,
    scope: str, secret: str, now=None,
) -> dict[str, Any]:
    snapshot_seal, key = _seal(selected, histories, scope=scope, secret=secret)
    current = _clock(now)
    expiry = min(current + _TTL, *(
        _parse_utc(selected["cases"][ref]["admitted"]["owner_declaration"]["expires_at"])
        for ref in selected["ordered"]
    ))
    if expiry <= current:
        raise CaseDeclarationError("expired selected portfolio payment review")
    unsigned = {
        "format": _TOKEN_FORMAT, "work_item_refs": list(selected["ordered"]),
        "snapshot_seal": snapshot_seal,
        "issued_at": current.isoformat(), "expires_at": expiry.isoformat(),
    }
    token = {
        **unsigned,
        "seal": _mac(key, b"declared-portfolio-payment-preflight-token", _canonical(unsigned)),
    }
    cases = []
    for ref in selected["ordered"]:
        history = histories[ref]
        checks = [event for event in history["events"] if event["kind"] == "check"]
        cases.append({
            "work_item_ref": ref,
            "expected_outcome_code": "stripe_invoice_paid_reported",
            "historical_provider_state": history["last_observed_provider_state"],
            "historical_check_count": history["provider_check_count"],
            "disputed_check_count": history["disputed_check_count"],
            "last_check_disputed": bool(checks and checks[-1]["disputed"]),
            "remaining_private_ledger_event_slots": _MAX_EVENTS - len(history["events"]),
            "fresh_source_check_required": True,
            "provider_goal_met_now": False,
        })
    return {
        "format": _FORMAT, "selected_case_count": len(cases), "cases": cases,
        "expected_preflight": token,
        "source_read_performed": False, "source_write_performed": False,
        "new_evidence_appended": False,
        "historical_provider_state_not_current": True,
        "global_private_ledger_case_capacity_not_reserved": True,
        "customer_outcome_verified": False,
        "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False, "not_synced": True,
    }


def validate_portfolio_payment_preflight(
    token: Any, selected: dict[str, Any], histories: dict[str, dict[str, Any]], *,
    scope: str, secret: str, now=None,
) -> None:
    if not isinstance(token, dict) or set(token) != _TOKEN_FIELDS:
        raise CaseDeclarationError("invalid selected portfolio payment preflight token")
    snapshot_seal, key = _seal(selected, histories, scope=scope, secret=secret)
    if (token["format"] != _TOKEN_FORMAT
            or token["work_item_refs"] != list(selected["ordered"])
            or not isinstance(token["snapshot_seal"], str)
            or not hmac.compare_digest(token["snapshot_seal"], snapshot_seal)):
        raise CaseDeclarationError("stale selected portfolio payment preflight review or evidence")
    current = _clock(now)
    issued = _parse_utc(token["issued_at"])
    expiry = _parse_utc(token["expires_at"])
    if (issued > current + _SKEW or expiry <= current
            or expiry <= issued or expiry - issued > _TTL
            or any(expiry > _parse_utc(selected["cases"][ref]["admitted"]["owner_declaration"]["expires_at"])
                   for ref in selected["ordered"])):
        raise CaseDeclarationError("expired selected portfolio payment preflight token")
    seal = token["seal"]
    if not isinstance(seal, str) or len(seal) != 64:
        raise CaseDeclarationError("invalid selected portfolio payment preflight seal")
    unsigned = {name: token[name] for name in _TOKEN_FIELDS if name != "seal"}
    if not hmac.compare_digest(
        seal, _mac(key, b"declared-portfolio-payment-preflight-token", _canonical(unsigned))
    ):
        raise CaseDeclarationError("tampered selected portfolio payment preflight token")


__all__ = ["issue_portfolio_payment_preflight", "validate_portfolio_payment_preflight"]
