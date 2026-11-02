from __future__ import annotations

"""Sealed, operator-carried history of explicitly requested Stripe operation reads.

No server-side outcome persistence, source permission, customer completion or
agent authority. The REST caller authenticates and rechecks source and DAG.
"""
from datetime import timedelta
import hmac
import re
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_status_checkpoint import _bindings, _canonical, _clock, _mac, _parse_utc

_FORMAT = "openworkgraph.local-case-operation-timeline.v1"
_RESPONSE = "openworkgraph.local-case-operation-timeline-check.v1"
_FIELDS = frozenset(("format", "work_item_ref", "revision", "graph_binding",
                     "source_binding", "record_binding", "issued_at",
                     "expires_at", "checks", "seal"))
_ENTRY = frozenset(("checked_at", "provider_state", "provider_verified_at"))
_HEX = re.compile(r"^[0-9a-f]{64}$")
_MAX_CHECKS = 8
_MAX_AGE = timedelta(hours=24)
_FRESH = timedelta(minutes=15)
_SKEW = timedelta(minutes=5)


def validate_prior_operation_timeline(
    prior: Any, admitted: dict[str, Any], signature: tuple[tuple[Any, ...], ...],
    *, resource_ref: str, scope: str, secret: str, link_expires_at: str, now=None,
) -> list[dict[str, Any]]:
    """Fail closed on tampering, scope/review changes, stale clocks and full history."""
    current = _clock(now)
    if not isinstance(prior, dict) or set(prior) != _FIELDS:
        raise CaseDeclarationError("invalid prior operation timeline")
    graph, source, key = _bindings(signature, scope, secret)
    record = _mac(key, b"operation-timeline-record", resource_ref.encode("ascii"))
    if (prior["format"] != _FORMAT or
        prior["work_item_ref"] != admitted["work_item_ref"] or
        type(prior["revision"]) is not int or
        prior["revision"] != admitted["owner_declaration"]["revision"]):
        raise CaseDeclarationError("stale operation timeline declaration")
    for field, expected in (("graph_binding", graph), ("source_binding", source),
                            ("record_binding", record)):
        value = prior[field]
        if not isinstance(value, str) or not _HEX.fullmatch(value) or not hmac.compare_digest(value, expected):
            raise CaseDeclarationError("stale operation timeline scope or graph")
    issued, expires = _parse_utc(prior["issued_at"]), _parse_utc(prior["expires_at"])
    if (issued > current + _SKEW or current >= expires or expires <= issued or
        expires - issued > _MAX_AGE or current - issued > _MAX_AGE or
        expires > _parse_utc(admitted["owner_declaration"]["expires_at"]) or
        expires > _parse_utc(link_expires_at)):
        raise CaseDeclarationError("stale or expired operation timeline")
    checks = prior["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= _MAX_CHECKS:
        raise CaseDeclarationError("invalid operation timeline length")
    previous = None
    for entry in checks:
        if not isinstance(entry, dict) or set(entry) != _ENTRY:
            raise CaseDeclarationError("invalid operation timeline entry")
        checked = _parse_utc(entry["checked_at"])
        if (checked < issued or checked >= expires or checked > current + _SKEW or
            (previous is not None and checked <= previous)):
            raise CaseDeclarationError("conflicting operation timeline order")
        previous = checked
        if entry["provider_state"] == "paid":
            verified = _parse_utc(entry["provider_verified_at"])
            if verified > checked + _SKEW or checked - verified > _FRESH:
                raise CaseDeclarationError("stale operation timeline source clock")
        elif entry["provider_state"] == "unknown":
            if entry["provider_verified_at"] is not None:
                raise CaseDeclarationError("contradictory unknown operation timeline")
        else:
            raise CaseDeclarationError("invalid operation timeline state")
    seal = prior["seal"]
    if not isinstance(seal, str) or not _HEX.fullmatch(seal):
        raise CaseDeclarationError("invalid operation timeline seal")
    unsigned = {name: prior[name] for name in _FIELDS if name != "seal"}
    if not hmac.compare_digest(seal, _mac(key, b"operation-timeline-receipt", _canonical(unsigned))):
        raise CaseDeclarationError("tampered operation timeline")
    return checks


def append_operation_timeline(
    admitted: dict[str, Any], signature: tuple[tuple[Any, ...], ...],
    resource_ref: str, verification: dict[str, Any], *,
    scope: str, secret: str, link_expires_at: str,
    prior: dict[str, Any] | None = None, now=None,
) -> dict[str, Any]:
    """Append one independently obtained provider result; never infer an outcome."""
    current = _clock(now)
    if (not isinstance(verification, dict) or
        verification.get("format") != "openworkgraph.local-case-provider-operation-verification.v1" or
        verification.get("work_item_ref") != admitted["work_item_ref"] or
        verification.get("resource_ref") != resource_ref or
        verification.get("expected_operation") != "stripe_invoice_paid" or
        verification.get("customer_outcome_verified") is not False or
        verification.get("expected_outcome_verified") is not False or
        verification.get("agent_caused_operation_verified") is not False or
        verification.get("agent_execution_authorized") is not False):
        raise CaseDeclarationError("invalid independent provider operation evidence")
    state = verification.get("provider_operation_state")
    if state not in ("paid", "unknown") or verification.get("provider_operation_verified") is not (state == "paid"):
        raise CaseDeclarationError("conflicting provider operation evidence")
    verified_at = None
    if state == "paid":
        verified = _parse_utc(verification.get("provider_verified_at"))
        if verified > current + _SKEW or current - verified > _FRESH:
            raise CaseDeclarationError("stale provider operation clock")
        verified_at = verified.isoformat()
    elif verification.get("provider_verified_at") is not None:
        raise CaseDeclarationError("contradictory unknown provider operation")

    graph, source, key = _bindings(signature, scope, secret)
    link_expiry = _parse_utc(link_expires_at)
    declaration_expiry = _parse_utc(admitted["owner_declaration"]["expires_at"])
    if current >= min(link_expiry, declaration_expiry):
        raise CaseDeclarationError("expired record link or review")
    if prior is None:
        issued = current
        expires = min(issued + _MAX_AGE, link_expiry, declaration_expiry)
        checks: list[dict[str, Any]] = []
    else:
        checks = validate_prior_operation_timeline(
            prior, admitted, signature, resource_ref=resource_ref,
            scope=scope, secret=secret, link_expires_at=link_expires_at, now=current,
        )
        if len(checks) >= _MAX_CHECKS:
            raise CaseDeclarationError("operation timeline full")
        issued, expires = _parse_utc(prior["issued_at"]), _parse_utc(prior["expires_at"])
        if current <= _parse_utc(checks[-1]["checked_at"]):
            raise CaseDeclarationError("operation timeline clock did not advance")
    entries = [*checks, {"checked_at": current.isoformat(),
                         "provider_state": state, "provider_verified_at": verified_at}]
    unsigned = {
        "format": _FORMAT, "work_item_ref": admitted["work_item_ref"],
        "revision": admitted["owner_declaration"]["revision"],
        "graph_binding": graph, "source_binding": source,
        "record_binding": _mac(key, b"operation-timeline-record", resource_ref.encode("ascii")),
        "issued_at": issued.isoformat(), "expires_at": expires.isoformat(),
        "checks": entries,
    }
    timeline = {**unsigned, "seal": _mac(key, b"operation-timeline-receipt", _canonical(unsigned))}
    return {
        "format": _RESPONSE, "work_item_ref": admitted["work_item_ref"],
        "timeline": timeline, "latest_provider_state": state,
        "latest_provider_verified_at": verified_at,
        "historical_paid_observation_count": sum(e["provider_state"] == "paid" for e in entries),
        "historical_unknown_observation_count": sum(e["provider_state"] == "unknown" for e in entries),
        "latest_unknown_does_not_reverse_prior_paid_evidence": True,
        "provider_state_not_customer_outcome": True,
        "expected_outcome_verified": False, "customer_outcome_verified": False,
        "business_obligation_verified": False, "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
        "source_verification_explicitly_requested": True,
        "source_write_performed": False, "server_side_timeline_persistence": False,
        "not_synced": True,
    }


__all__ = ["validate_prior_operation_timeline", "append_operation_timeline"]
