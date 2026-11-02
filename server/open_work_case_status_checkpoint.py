from __future__ import annotations

"""Operator-carried, source-scoped case status checkpoints over the existing DAG.

These HMAC-sealed receipts are *not* a source-status database or an outcome
verifier. They carry only a bounded provider-open/unknown observation; current
review, retention, source scope and every prerequisite are re-admitted on
each use. They cannot confer agent permissions or establish payment.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from typing import Any

from .open_work_case_declarations import CaseDeclarationError

_FORMAT = "openworkgraph.local-case-source-checkpoint.v1"
_RESPONSE = "openworkgraph.local-case-status-comparison.v1"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z0-9_]{1,64}$")
_FIELDS = frozenset({
    "format", "work_item_ref", "revision", "graph_binding", "source_binding",
    "issued_at", "expires_at", "provider_state", "provider_verified_at",
    "bounded_inventory_complete", "reason", "seal",
})
_MAX_AGE = timedelta(hours=24)
_MAX_SOURCE_AGE = timedelta(minutes=15)
_MAX_CLOCK_SKEW = timedelta(minutes=5)


def _clock(now: datetime | None) -> datetime:
    value = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CaseDeclarationError("invalid checkpoint clock")
    return value.astimezone(timezone.utc)


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not 20 <= len(value) <= 40:
        raise CaseDeclarationError("invalid checkpoint timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise CaseDeclarationError("invalid checkpoint timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CaseDeclarationError("unscoped checkpoint timestamp")
    return parsed.astimezone(timezone.utc)


def _key(secret: str) -> bytes:
    if not isinstance(secret, str) or not 16 <= len(secret) <= 512:
        raise CaseDeclarationError("unavailable local checkpoint signing key")
    return secret.encode("utf-8")


def _mac(key: bytes, purpose: bytes, value: bytes) -> str:
    return hmac.new(key, b"open-work:status-checkpoint:v1:" + purpose + b":" + value,
                    hashlib.sha256).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError):
        raise CaseDeclarationError("invalid checkpoint evidence") from None


def _bindings(signature: tuple[tuple[Any, ...], ...], scope: str, secret: str) -> tuple[str, str, bytes]:
    if not isinstance(scope, str) or not re.fullmatch(r"owg:scope:[0-9a-f]{32}", scope):
        raise CaseDeclarationError("invalid source scope")
    if not isinstance(signature, tuple) or not signature or len(signature) > 50:
        raise CaseDeclarationError("invalid reviewed dependency signature")
    key = _key(secret)
    return (
        _mac(key, b"graph", _canonical(signature)),
        _mac(key, b"source", scope.encode("ascii")),
        key,
    )


def validate_prior_checkpoint(
    prior: dict[str, Any], admitted: dict[str, Any],
    signature: tuple[tuple[Any, ...], ...], *, scope: str, secret: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reject replay, tampering and stale owner/source scope *before* source IO."""
    current = _clock(now)
    if not isinstance(prior, dict) or set(prior) != _FIELDS:
        raise CaseDeclarationError("invalid prior source checkpoint")
    if (prior["format"] != _FORMAT or
        prior["work_item_ref"] != admitted["work_item_ref"] or
        type(prior["revision"]) is not int or
        prior["revision"] != admitted["owner_declaration"]["revision"]):
        raise CaseDeclarationError("stale source checkpoint declaration")
    graph, source, key = _bindings(signature, scope, secret)
    for field, expected in (("graph_binding", graph), ("source_binding", source)):
        value = prior[field]
        if not isinstance(value, str) or not _HEX.fullmatch(value) or not hmac.compare_digest(value, expected):
            raise CaseDeclarationError("stale source checkpoint scope or graph")
    if (prior["provider_state"] not in ("open", "unknown") or
        type(prior["bounded_inventory_complete"]) is not bool or
        not isinstance(prior["seal"], str) or not _HEX.fullmatch(prior["seal"])):
        raise CaseDeclarationError("invalid source checkpoint state")
    if prior["provider_state"] == "open":
        if prior["reason"] is not None or not isinstance(prior["provider_verified_at"], str):
            raise CaseDeclarationError("conflicting source checkpoint status")
    else:
        if (prior["provider_verified_at"] is not None or
            prior["bounded_inventory_complete"] is not False or
            not isinstance(prior["reason"], str) or not _REASON.fullmatch(prior["reason"])):
            raise CaseDeclarationError("conflicting unknown checkpoint status")
    issued, expires = _parse_utc(prior["issued_at"]), _parse_utc(prior["expires_at"])
    declaration_expiry = _parse_utc(admitted["owner_declaration"]["expires_at"])
    if (issued > current + _MAX_CLOCK_SKEW or current > expires or
        expires <= issued or expires - issued > _MAX_AGE or
        expires > declaration_expiry or current - issued > _MAX_AGE):
        raise CaseDeclarationError("stale or expired source checkpoint")
    if prior["provider_state"] == "open":
        verified = _parse_utc(prior["provider_verified_at"])
        if (verified > issued + _MAX_CLOCK_SKEW or
            issued - verified > _MAX_SOURCE_AGE):
            raise CaseDeclarationError("conflicting source checkpoint freshness")
    unsigned = {name: prior[name] for name in _FIELDS if name != "seal"}
    expected_seal = _mac(key, b"receipt", _canonical(unsigned))
    if not hmac.compare_digest(prior["seal"], expected_seal):
        raise CaseDeclarationError("tampered source checkpoint")
    return {
        "provider_state": prior["provider_state"],
        "provider_verified_at": prior["provider_verified_at"],
        "issued_at": prior["issued_at"],
    }


def issue_status_checkpoint(
    admitted: dict[str, Any], signature: tuple[tuple[Any, ...], ...],
    minimized: dict[str, Any], *, scope: str, secret: str,
    prior_state: dict[str, Any] | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """Issue a bounded portable receipt; compare only source observations."""
    current = _clock(now)
    graph, source, key = _bindings(signature, scope, secret)
    status = minimized.get("source_reported_status")
    if not isinstance(status, dict):
        raise CaseDeclarationError("invalid source status for checkpoint")
    state = status.get("state")
    if state not in ("open", "unknown"):
        raise CaseDeclarationError("conflicting source checkpoint state")
    verified_at = None
    reason = status.get("reason")
    complete = False
    if state == "open":
        if (status.get("availability") != "source_reported" or
            type(status.get("bounded_inventory_complete")) is not bool or
            reason is not None):
            raise CaseDeclarationError("conflicting source checkpoint evidence")
        verified = _parse_utc(status.get("verified_at"))
        if verified > current + _MAX_CLOCK_SKEW or current - verified > _MAX_SOURCE_AGE:
            state, reason = "unknown", "source_stale_or_clock_conflict"
        else:
            verified_at = verified.isoformat()
            complete = status["bounded_inventory_complete"]
    else:
        if (status.get("availability") != "unknown" or
            status.get("verified_at") is not None or
            not isinstance(reason, str) or not _REASON.fullmatch(reason)):
            raise CaseDeclarationError("conflicting unknown source evidence")
    expiry = _parse_utc(admitted["owner_declaration"]["expires_at"])
    if expiry <= current:
        raise CaseDeclarationError("stale reviewed declaration expiry")
    expires = min(current + _MAX_AGE, expiry)
    unsigned = {
        "format": _FORMAT,
        "work_item_ref": admitted["work_item_ref"],
        "revision": admitted["owner_declaration"]["revision"],
        "graph_binding": graph,
        "source_binding": source,
        "issued_at": current.isoformat(),
        "expires_at": expires.isoformat(),
        "provider_state": state,
        "provider_verified_at": verified_at,
        "bounded_inventory_complete": complete,
        "reason": reason,
    }
    checkpoint = {**unsigned, "seal": _mac(key, b"receipt", _canonical(unsigned))}
    comparison = {"available": prior_state is not None}
    if prior_state is not None:
        before = prior_state["provider_state"]
        comparison.update({
            "prior_provider_state": before,
            "current_provider_state": state,
            "transition": before + "_to_" + state,
            "source_state_change_not_proof_of_payment": True,
            "provider_operation_verified": False,
            "customer_outcome_verified": False,
        })
    return {
        "format": _RESPONSE,
        "work_item_ref": admitted["work_item_ref"],
        "owner_declaration": admitted["owner_declaration"],
        "checkpoint": checkpoint,
        "comparison": comparison,
        "source_scan_explicitly_requested": True,
        "source_checks_not_atomic": True,
        "source_write_performed": False,
        "business_obligation_verified": False,
        "provider_operation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
        "no_server_side_source_status_persistence": True,
        "not_synced": True,
    }


__all__ = ["validate_prior_checkpoint", "issue_status_checkpoint"]
