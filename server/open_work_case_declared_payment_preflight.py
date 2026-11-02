from __future__ import annotations

"""Explicit read-only planning checkpoint for a reviewed payment-goal DAG.

The plan validates the exact approved DAG, signed invoice links and every
retained HMAC evidence history before the operator elects to contact Stripe.
Its short-lived seal is a *staleness checkpoint*, never a source receipt,
approval, obligation, customer outcome or agent execution permission.
"""
from datetime import timedelta
import hmac
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_status_checkpoint import _bindings, _canonical, _clock, _mac, _parse_utc

_FORMAT = "openworkgraph.local-case-declared-payment-preflight.v1"
_TOKEN_FORMAT = "openworkgraph.local-case-declared-payment-preflight-token.v1"
_TOKEN_FIELDS = frozenset({
    "format", "work_item_ref", "snapshot_seal", "issued_at", "expires_at", "seal",
})
_MAX_EVENTS = 32
_TTL = timedelta(minutes=5)
_SKEW = timedelta(minutes=5)


def _checked_material(
    selected: dict[str, Any], histories: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Bind every prerequisite, exact record, review version and evidence event."""
    ordered = selected.get("ordered")
    if (not isinstance(ordered, tuple) or not 1 <= len(ordered) <= 4
            or len(set(ordered)) != len(ordered) or set(histories) != set(ordered)):
        raise CaseDeclarationError("incomplete declared payment preflight")
    cases = []
    for ref in ordered:
        item = selected["cases"][ref]
        history = histories[ref]
        events = history.get("events")
        count = history.get("event_count")
        if (history.get("format") != "openworkgraph.local-case-operation-evidence-ledger.v1"
                or history.get("work_item_ref") != ref
                or not isinstance(events, list)
                or type(count) is not int or count != len(events)
                or not 0 <= count < _MAX_EVENTS
                or history.get("historical_only_requires_fresh_source_recheck") is not True
                or history.get("current_provider_operation_verified") is not False
                or history.get("customer_outcome_verified") is not False):
            raise CaseDeclarationError("invalid or full private payment evidence history")
        cases.append({
            "ref": ref, "revision": item["revision"], "edit_token": item["edit_token"],
            "signature": item["signature"], "record": item["resource_ref"],
            "events": events,
        })
    return {"root_signature": selected["root_signature"], "cases": cases}


def _seal_material(
    selected: dict[str, Any], histories: dict[str, dict[str, Any]],
    *, scope: str, secret: str,
) -> tuple[str, bytes]:
    material = _checked_material(selected, histories)
    graph_binding, source_binding, key = _bindings(selected["root_signature"], scope, secret)
    material["graph_binding"] = graph_binding
    material["source_binding"] = source_binding
    return _mac(key, b"declared-payment-preflight-state", _canonical(material)), key


def issue_payment_preflight(
    selected: dict[str, Any], histories: dict[str, dict[str, Any]],
    *, scope: str, secret: str, now=None,
) -> dict[str, Any]:
    """Return a human-readable historical plan and five-minute sealed token."""
    snapshot_seal, key = _seal_material(selected, histories, scope=scope, secret=secret)
    current = _clock(now)
    expiries = [
        _parse_utc(selected["cases"][ref]["admitted"]["owner_declaration"]["expires_at"])
        for ref in selected["ordered"]
    ]
    expiry = min(current + _TTL, *expiries)
    if expiry <= current:
        raise CaseDeclarationError("expired owner payment review")
    # The root is identified by its full signature, not positional order.
    root_refs = [ref for ref in selected["ordered"]
                 if selected["cases"][ref]["signature"] == selected["root_signature"]]
    if len(root_refs) != 1:
        raise CaseDeclarationError("ambiguous declared payment root")
    unsigned = {
        "format": _TOKEN_FORMAT,
        "work_item_ref": root_refs[0],
        "snapshot_seal": snapshot_seal,
        "issued_at": current.isoformat(), "expires_at": expiry.isoformat(),
    }
    token = {**unsigned, "seal": _mac(key, b"declared-payment-preflight-token", _canonical(unsigned))}
    cases = []
    for ref in selected["ordered"]:
        history = histories[ref]
        events = history["events"]
        last_checks = [e for e in events if e["kind"] == "check"]
        cases.append({
            "work_item_ref": ref,
            "expected_outcome_code": "stripe_invoice_paid_reported",
            "historical_provider_state": history["last_observed_provider_state"],
            "historical_check_count": history["provider_check_count"],
            "disputed_check_count": history["disputed_check_count"],
            "last_check_disputed": bool(last_checks and last_checks[-1]["disputed"]),
            "remaining_private_ledger_event_slots": _MAX_EVENTS - len(events),
            "fresh_source_check_required": True,
            "provider_goal_met_now": False,
        })
    return {
        "format": _FORMAT, "work_item_ref": root_refs[0],
        "reviewed_case_count": len(cases), "cases": cases,
        "expected_preflight": token,
        "source_read_performed": False, "source_write_performed": False,
        "new_evidence_appended": False,
        "historical_provider_state_not_current": True,
        "customer_outcome_verified": False, "agent_caused_outcome_verified": False,
        "agent_execution_authorized": False, "not_synced": True,
    }


def validate_payment_preflight(
    token: Any, selected: dict[str, Any],
    histories: dict[str, dict[str, Any]],
    *, scope: str, secret: str, now=None,
) -> None:
    """Reject stale/forged review or evidence BEFORE the first provider read."""
    if not isinstance(token, dict) or set(token) != _TOKEN_FIELDS:
        raise CaseDeclarationError("invalid declared payment preflight token")
    snapshot_seal, key = _seal_material(selected, histories, scope=scope, secret=secret)
    root_refs = [ref for ref in selected["ordered"]
                 if selected["cases"][ref]["signature"] == selected["root_signature"]]
    if (len(root_refs) != 1 or token["format"] != _TOKEN_FORMAT
            or token["work_item_ref"] != root_refs[0]
            or not isinstance(token["snapshot_seal"], str)
            or not hmac.compare_digest(token["snapshot_seal"], snapshot_seal)):
        raise CaseDeclarationError("stale declared payment preflight review or evidence")
    current = _clock(now)
    issued = _parse_utc(token["issued_at"])
    expiry = _parse_utc(token["expires_at"])
    if (issued > current + _SKEW or expiry <= current
            or expiry <= issued or expiry - issued > _TTL
            or any(expiry > _parse_utc(selected["cases"][ref]["admitted"]["owner_declaration"]["expires_at"])
                   for ref in selected["ordered"])):
        raise CaseDeclarationError("expired declared payment preflight token")
    seal = token["seal"]
    if not isinstance(seal, str) or len(seal) != 64:
        raise CaseDeclarationError("invalid declared payment preflight seal")
    unsigned = {k: token[k] for k in _TOKEN_FIELDS if k != "seal"}
    if not hmac.compare_digest(seal, _mac(key, b"declared-payment-preflight-token", _canonical(unsigned))):
        raise CaseDeclarationError("tampered declared payment preflight token")


__all__ = ["issue_payment_preflight", "validate_payment_preflight"]
