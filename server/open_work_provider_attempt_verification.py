"""Conservative correlation of independently fetched provider events to agent attempts.

This pure verification boundary accepts only evidence returned by an authenticated
provider reader supplied by the caller. It does not fetch, authorize, or persist
evidence, and must never be used as proof of customer outcome.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
import hmac
import re

_TOKEN = re.compile(r"^[A-Za-z0-9_./:-]{1,160}$")
_MAX_EVENTS = 200


def _valid(value: Any) -> bool:
    return isinstance(value, str) and bool(_TOKEN.fullmatch(value))


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def verify_provider_attempt(
    *,
    account: str,
    object_id: str,
    operation: str,
    correlation_id: str,
    expected_event_id: str,
    read_events: Callable[[], dict[str, Any]],
    now: datetime | None = None,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    """Return a conservative provider-operation verdict, never business success.

    The caller must authenticate and authorize the provider read independently
    of the agent's own report. A complete bounded response must carry the
    authenticated account and exact object identity. No timestamp proximity,
    human attestation, or agent-reported success substitutes for correlation.
    """
    unknown = {"status": "unknown", "provider_operation": "unknown",
               "customer_outcome": "unknown"}
    if not all(map(_valid, (account, object_id, operation, correlation_id, expected_event_id))):
        return {**unknown, "reason": "invalid_identity"}
    if type(max_age_seconds) is not int or not 0 < max_age_seconds <= 3600:
        return {**unknown, "reason": "invalid_freshness"}
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime) or current.tzinfo is None:
        return {**unknown, "reason": "invalid_clock"}
    if not callable(read_events):
        return {**unknown, "reason": "invalid_provider_reader"}
    try:
        payload = read_events()
    except Exception:
        return {**unknown, "reason": "provider_unavailable"}
    if not isinstance(payload, dict) or payload.get("revoked") is True:
        return {**unknown, "reason": "source_revoked_or_invalid"}
    if payload.get("complete") is not True or payload.get("authenticated") is not True:
        return {**unknown, "reason": "incomplete_or_unauthenticated"}
    if payload.get("account") != account or payload.get("object_id") != object_id:
        return {**unknown, "reason": "source_scope_mismatch"}
    checked = _utc(payload.get("checked_at"))
    if checked is None or abs((current - checked).total_seconds()) > max_age_seconds:
        return {**unknown, "reason": "stale_source"}
    events = payload.get("events")
    if not isinstance(events, list) or len(events) > _MAX_EVENTS:
        return {**unknown, "reason": "unbounded_source"}
    matches = []
    for event in events:
        if not isinstance(event, dict):
            return {**unknown, "reason": "malformed_event"}
        if (event.get("event_id") == expected_event_id
                or event.get("correlation_id") == correlation_id):
            if not all(_valid(event.get(k)) for k in ("event_id", "correlation_id", "operation", "object_id")):
                return {**unknown, "reason": "malformed_match"}
            matches.append(event)
    if len(matches) != 1:
        return {**unknown, "reason": "missing_or_conflicting_event"}
    event = matches[0]
    for key, expected in (("event_id", expected_event_id),
                          ("correlation_id", correlation_id),
                          ("operation", operation), ("object_id", object_id)):
        if not hmac.compare_digest(event[key], expected):
            return {**unknown, "reason": "identity_conflict"}
    if event.get("status") not in ("observed", "succeeded", "failed"):
        return {**unknown, "reason": "unconfirmed_provider_status"}
    return {"status": "verified_provider_event" if event["status"] == "observed" else "verified_provider_operation",
            "provider_operation": "unknown" if event["status"] == "observed" else event["status"],
            "customer_outcome": "unknown",
            "event_id": expected_event_id,
            "checked_at": checked.isoformat()}
