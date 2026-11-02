from __future__ import annotations

"""Portable, revocable local review packet for two independently checked ledgers.

A packet proves only that this installation previously assembled the given
minimized historical review. It is not a provider signature, customer outcome,
permission grant, or cross-process atomic authorization snapshot.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
import re
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_status_checkpoint import (
    _bindings, _canonical, _clock, _mac, _parse_utc,
)

_FORMAT = "openworkgraph.local-outcome-review-packet.v1"
_RESPONSE = "openworkgraph.local-outcome-review-packet-result.v1"
_TRIAGE_FORMAT = "openworkgraph.local-reviewed-outcome-evidence-triage.v1"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_REF = re.compile(r"^owg:w:[0-9a-f]{32}$")
_MAX_AGE = timedelta(hours=1)
_CLOCK_SKEW = timedelta(minutes=5)
_FIELDS = frozenset({
    "format", "work_item_ref", "revision", "graph_binding", "source_binding",
    "issued_at", "expires_at", "max_cases", "selected_case_refs",
    "unscanned_prerequisite_count", "triage_digest", "case_digests", "seal",
})


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _case_refs(triage: dict[str, Any]) -> list[str]:
    if not isinstance(triage, dict) or triage.get("format") != _TRIAGE_FORMAT:
        raise CaseDeclarationError("invalid historical review projection")
    cases = triage.get("case_reviews")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 4:
        raise CaseDeclarationError("invalid bounded historical review")
    refs = [row.get("work_item_ref") if isinstance(row, dict) else None for row in cases]
    if (any(not isinstance(ref, str) or not _REF.fullmatch(ref) for ref in refs)
            or len(set(refs)) != len(refs)
            or refs[-1] != triage.get("work_item_ref")
            or triage.get("included_case_count") != len(refs)):
        raise CaseDeclarationError("invalid historical review case selection")
    return refs


def issue_review_packet(
    triage: dict[str, Any], admitted: dict[str, Any], signature: tuple,
    *, scope: str, secret: str, max_cases: int, now: datetime | None = None,
) -> dict[str, Any]:
    """Seal a minimized, integrity-checked, owner-reviewed historical snapshot."""
    current = _clock(now)
    if type(max_cases) is not int or not 1 <= max_cases <= 4:
        raise CaseDeclarationError("invalid review packet case limit")
    refs = _case_refs(triage)
    if (len(refs) > max_cases or
            triage.get("unscanned_prerequisite_count") != len(signature) - len(refs) or
            triage.get("expected_outcome_verified") is not False or
            triage.get("customer_outcome_verified") is not False or
            triage.get("source_read_performed") is not False or
            triage.get("source_write_performed") is not False or
            triage.get("work_item_ref") != admitted.get("work_item_ref")):
        raise CaseDeclarationError("invalid historical review packet evidence")
    graph, source, key = _bindings(signature, scope, secret)
    owner = admitted["owner_declaration"]
    expiry = min(current + _MAX_AGE, _parse_utc(owner["expires_at"]))
    if expiry <= current:
        raise CaseDeclarationError("stale reviewed declaration expiry")
    unsigned = {
        "format": _FORMAT,
        "work_item_ref": admitted["work_item_ref"],
        "revision": owner["revision"],
        "graph_binding": graph,
        "source_binding": source,
        "issued_at": current.isoformat(),
        "expires_at": expiry.isoformat(),
        "max_cases": max_cases,
        "selected_case_refs": refs,
        "unscanned_prerequisite_count": len(signature) - len(refs),
        "triage_digest": _digest(triage),
        "case_digests": [_digest(row) for row in triage["case_reviews"]],
    }
    packet = {**unsigned, "seal": _mac(key, b"outcome-review-packet", _canonical(unsigned))}
    return {
        "format": _RESPONSE, "work_item_ref": admitted["work_item_ref"],
        "packet": packet, "review_summary": triage,
        "packet_validity": "local_integrity_only_not_independent_provider_signature",
        "current_provider_operation_verified": False,
        "expected_outcome_verified": False, "customer_outcome_verified": False,
        "agent_execution_authorized": False, "source_read_performed": False,
        "source_write_performed": False, "not_synced": True,
    }


def validate_review_packet(
    packet: Any, admitted: dict[str, Any], signature: tuple,
    *, scope: str, secret: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed on forgery, expiry, wrong scope or graph BEFORE ledger reads."""
    current = _clock(now)
    if not isinstance(packet, dict) or set(packet) != _FIELDS:
        raise CaseDeclarationError("invalid review packet fields")
    owner = admitted["owner_declaration"]
    if (packet["format"] != _FORMAT or
            packet["work_item_ref"] != admitted["work_item_ref"] or
            type(packet["revision"]) is not int or
            packet["revision"] != owner["revision"]):
        raise CaseDeclarationError("stale review packet declaration")
    graph, source, key = _bindings(signature, scope, secret)
    for field, expected in (("graph_binding", graph), ("source_binding", source)):
        value = packet[field]
        if (not isinstance(value, str) or not _HEX.fullmatch(value)
                or not hmac.compare_digest(value, expected)):
            raise CaseDeclarationError("stale review packet graph or scope")
    count = packet["max_cases"]
    refs = packet["selected_case_refs"]
    digests = packet["case_digests"]
    omitted = packet["unscanned_prerequisite_count"]
    if (type(count) is not int or not 1 <= count <= 4
            or not isinstance(refs, list) or not 1 <= len(refs) <= count
            or not isinstance(digests, list) or len(digests) != len(refs)
            or any(not isinstance(ref, str) or not _REF.fullmatch(ref) for ref in refs)
            or len(set(refs)) != len(refs) or refs[-1] != admitted["work_item_ref"]
            or type(omitted) is not int or omitted != len(signature) - len(refs)
            or omitted < 0
            or any(not isinstance(v, str) or not _HEX.fullmatch(v) for v in digests)
            or not isinstance(packet["triage_digest"], str)
            or not _HEX.fullmatch(packet["triage_digest"])
            or not isinstance(packet["seal"], str) or not _HEX.fullmatch(packet["seal"])):
        raise CaseDeclarationError("invalid bounded review packet")
    known = {item[0] for item in signature}
    if any(ref not in known for ref in refs):
        raise CaseDeclarationError("stale review packet case selection")
    issued = _parse_utc(packet["issued_at"])
    expires = _parse_utc(packet["expires_at"])
    owner_expiry = _parse_utc(owner["expires_at"])
    if (issued > current + _CLOCK_SKEW or current > expires
            or expires <= issued or expires - issued > _MAX_AGE
            or expires > owner_expiry or current - issued > _MAX_AGE):
        raise CaseDeclarationError("expired or stale review packet")
    unsigned = {field: packet[field] for field in _FIELDS if field != "seal"}
    seal = _mac(key, b"outcome-review-packet", _canonical(unsigned))
    if not hmac.compare_digest(packet["seal"], seal):
        raise CaseDeclarationError("tampered review packet")
    return {"max_cases": count, "selected_case_refs": refs}


def compare_review_packet(
    packet: dict[str, Any], triage: dict[str, Any],
) -> dict[str, Any]:
    """Compare against newly admitted private histories, never source state."""
    refs = _case_refs(triage)
    if refs != packet["selected_case_refs"]:
        raise CaseDeclarationError("changed historical review selection")
    changed = [
        ref for ref, row, prior in zip(
            refs, triage["case_reviews"], packet["case_digests"]
        ) if not hmac.compare_digest(_digest(row), prior)
    ]
    same = hmac.compare_digest(_digest(triage), packet["triage_digest"])
    return {
        "format": _RESPONSE, "work_item_ref": triage["work_item_ref"],
        "comparison": "unchanged" if same else "changed",
        "changed_case_refs": changed,
        "other_review_metadata_changed": not same and not changed,
        "review_summary": triage,
        "historical_only_requires_fresh_source_recheck": True,
        "packet_validity": "local_integrity_only_not_independent_provider_signature",
        "current_provider_operation_verified": False,
        "expected_outcome_verified": False, "customer_outcome_verified": False,
        "agent_execution_authorized": False, "source_read_performed": False,
        "source_write_performed": False, "not_synced": True,
    }


__all__ = ["issue_review_packet", "validate_review_packet", "compare_review_packet"]
