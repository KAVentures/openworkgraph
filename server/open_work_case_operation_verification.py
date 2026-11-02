from __future__ import annotations

"""Source-joined case/record link for later independent Stripe operation checks.

A link is only issued while the exact Stripe invoice is present in an
explicitly authorized open-invoice scan AND its retained OWG record identity
is source-joined. Later verification uses the existing independent provider
receipt reader; disappearance from the open inventory never means paid.
This is operator-carried evidence, not a durable customer-outcome assertion.
"""
from datetime import timedelta
import hmac
import re
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_status_checkpoint import (
    _bindings, _canonical, _clock, _mac, _parse_utc,
)

_FORMAT = "openworkgraph.local-case-record-link.v1"
_LINK_RESPONSE = "openworkgraph.local-case-record-link-issuance.v1"
_VERIFY_RESPONSE = "openworkgraph.local-case-provider-operation-verification.v1"
_RECORD = re.compile(r"^owg:r:[0-9a-f]{32}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = frozenset({
    "format", "work_item_ref", "resource_ref", "revision",
    "expected_operation", "graph_binding", "source_binding",
    "issued_at", "expires_at", "seal",
})
_MAX_AGE = timedelta(hours=24)
_MAX_SOURCE_AGE = timedelta(minutes=15)
_CLOCK_SKEW = timedelta(minutes=5)


def _record_ref(value: Any) -> str:
    if not isinstance(value, str) or not _RECORD.fullmatch(value):
        raise CaseDeclarationError("invalid exact retained record reference")
    return value


def issue_case_record_link(
    admitted: dict[str, Any], signature: tuple[tuple[Any, ...], ...],
    minimized: dict[str, Any], *, resource_ref: str, scope: str, secret: str,
    now=None,
) -> dict[str, Any]:
    """Seal a source-joined, observed invoice record, not a payment receipt."""
    current = _clock(now)
    resource_ref = _record_ref(resource_ref)
    if minimized.get("work_item_ref") != admitted.get("work_item_ref"):
        raise CaseDeclarationError("conflicting reviewed case identity")
    status = minimized.get("source_reported_status")
    if (not isinstance(status, dict) or status.get("state") != "open"
        or status.get("availability") != "source_reported"
        or minimized.get("related_record_history_incomplete") is not False):
        raise CaseDeclarationError("source-joined complete record evidence required")
    observed = minimized.get("retained_observation_summary")
    if (not isinstance(observed, list) or len(observed) > 8
        or sum(row.get("resource_ref") == resource_ref and
               type(row.get("observed_event_count")) is int and
               row["observed_event_count"] > 0
               for row in observed if isinstance(row, dict)) != 1):
        raise CaseDeclarationError("record not positively joined to current source case")
    verified = _parse_utc(status.get("verified_at"))
    if verified > current + _CLOCK_SKEW or current - verified > _MAX_SOURCE_AGE:
        raise CaseDeclarationError("source-joined record evidence stale")
    expires_at = _parse_utc(admitted["owner_declaration"]["expires_at"])
    if expires_at <= current:
        raise CaseDeclarationError("reviewed case retention expired")
    graph, source, key = _bindings(signature, scope, secret)
    unsigned = {
        "format": _FORMAT,
        "work_item_ref": admitted["work_item_ref"],
        "resource_ref": resource_ref,
        "revision": admitted["owner_declaration"]["revision"],
        "expected_operation": "stripe_invoice_paid",
        "graph_binding": graph,
        "source_binding": source,
        "issued_at": current.isoformat(),
        "expires_at": min(current + _MAX_AGE, expires_at).isoformat(),
    }
    link = {**unsigned, "seal": _mac(key, b"case-record-link", _canonical(unsigned))}
    return {
        "format": _LINK_RESPONSE,
        "work_item_ref": admitted["work_item_ref"],
        "record_link": link,
        "source_join_verified_at": verified.isoformat(),
        "record_observed_not_operation_verified": True,
        "provider_operation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
        "source_scan_explicitly_requested": True,
        "source_write_performed": False,
        "server_side_link_persistence": False,
        "not_synced": True,
    }


def validate_case_record_link(
    link: Any, admitted: dict[str, Any],
    signature: tuple[tuple[Any, ...], ...], *, scope: str, secret: str,
    now=None,
) -> str:
    """Verify all link bindings before any later independent provider read."""
    current = _clock(now)
    if not isinstance(link, dict) or set(link) != _FIELDS:
        raise CaseDeclarationError("invalid case record link")
    if (link["format"] != _FORMAT or
        link["work_item_ref"] != admitted["work_item_ref"] or
        type(link["revision"]) is not int or
        link["revision"] != admitted["owner_declaration"]["revision"] or
        link["expected_operation"] != "stripe_invoice_paid"):
        raise CaseDeclarationError("stale case record link")
    resource_ref = _record_ref(link["resource_ref"])
    graph, source, key = _bindings(signature, scope, secret)
    for name, expected in (("graph_binding", graph), ("source_binding", source)):
        value = link[name]
        if (not isinstance(value, str) or not _HEX.fullmatch(value)
            or not hmac.compare_digest(value, expected)):
            raise CaseDeclarationError("stale case record link scope or graph")
    issued, expires = _parse_utc(link["issued_at"]), _parse_utc(link["expires_at"])
    declaration_expiry = _parse_utc(admitted["owner_declaration"]["expires_at"])
    if (issued > current + _CLOCK_SKEW or current > expires
        or expires <= issued or expires - issued > _MAX_AGE
        or expires > declaration_expiry or current - issued > _MAX_AGE):
        raise CaseDeclarationError("stale or expired case record link")
    seal = link["seal"]
    if not isinstance(seal, str) or not _HEX.fullmatch(seal):
        raise CaseDeclarationError("invalid case record link seal")
    unsigned = {name: link[name] for name in _FIELDS if name != "seal"}
    if not hmac.compare_digest(seal, _mac(key, b"case-record-link", _canonical(unsigned))):
        raise CaseDeclarationError("tampered case record link")
    return resource_ref


def project_case_operation_verification(
    admitted: dict[str, Any], resource_ref: str, receipt: Any, *, now=None,
) -> dict[str, Any]:
    """Only fresh independent Stripe PAID receipts establish provider operation."""
    current = _clock(now)
    verified = False
    stamp = None
    if isinstance(receipt, dict) and (
        receipt.get("format") == "openworkgraph.source-operation-receipt.v1"
        and receipt.get("resource_ref") == resource_ref
        and receipt.get("expected_operation") == "stripe_invoice_paid"
        and receipt.get("availability") == "source_reported"
        and receipt.get("operation_verified") is True
        and receipt.get("provider") == "stripe"
        and receipt.get("provider_record_kind") == "invoice"
        and receipt.get("provider_reported_state") == "paid"
        and receipt.get("verification_scope") == "authenticated_source_provider_operational_state"
        and receipt.get("customer_outcome_verified") is False
        and receipt.get("agent_caused_operation_verified") is False
        and receipt.get("agent_execution_authorized") is False
        and receipt.get("read_only") is True
    ):
        try:
            parsed = _parse_utc(receipt.get("verified_at"))
            if parsed <= current + _CLOCK_SKEW and current - parsed <= _MAX_SOURCE_AGE:
                verified, stamp = True, parsed.isoformat()
        except CaseDeclarationError:
            pass
    return {
        "format": _VERIFY_RESPONSE,
        "work_item_ref": admitted["work_item_ref"],
        "resource_ref": resource_ref,
        "expected_operation": "stripe_invoice_paid",
        "provider_operation_state": "paid" if verified else "unknown",
        "provider_operation_verified": verified,
        "provider_verified_at": stamp,
        "reason": None if verified else "independent_provider_receipt_unavailable_or_unverified",
        "source_status_not_customer_outcome": True,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "business_obligation_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
        "source_verification_explicitly_requested": True,
        "source_write_performed": False,
        "server_side_link_persistence": False,
        "not_synced": True,
    }


__all__ = [
    "issue_case_record_link", "validate_case_record_link",
    "project_case_operation_verification",
]
