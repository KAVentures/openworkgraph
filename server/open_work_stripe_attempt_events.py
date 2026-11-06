"""Read-only Stripe Events source adapter for explicit agent-attempt verification.

No background sync, implicit credentials, or business-outcome claims. The caller
must independently authorize the account and supply a restricted credential.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Callable, Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_API = "https://api.stripe.com/v1"
_MAX_BYTES = 1024 * 1024


def _fetch(url: str, token: str, transport: Callable | None) -> dict[str, Any]:
    req = Request(url, headers={"Authorization": "Bearer " + token,
                                "Stripe-Version": "2025-02-24.acacia",
                                "Accept": "application/json"})
    if transport is None:
        def transport(req, timeout):
            return urlopen(req, timeout=timeout)
    with transport(req, timeout=8) as response:
        if getattr(response, "status", 200) != 200:
            raise ValueError("provider status")
        raw = response.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("provider response too large")
        result = json.loads(raw)
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError("invalid provider response")
    return result


def read_stripe_attempt_events(*, token: str, expected_account: str,
                               object_id: str, event_type: str,
                               correlation_id: str,
                               transport: Callable | None = None,
                               now: datetime | None = None) -> dict[str, Any]:
    """Return a complete bounded provider-read envelope, or fail closed.

    Stripe's request.idempotency_key must equal the exact key transmitted
    by the agent for the operation. Event ID is sourced from Stripe, never
    from the agent's assertion. A single bounded page is intentional: if
    more events exist, completeness cannot be established and is rejected.
    """
    from .open_work_provider_attempt_verification import _valid
    if (not isinstance(token, str) or not token.startswith(("rk_live_", "rk_test_", "sk_test_", "sk_live_"))
            or len(token) > 256 or not all(_valid(v) for v in
            (expected_account, object_id, event_type, correlation_id))):
        raise ValueError("invalid explicit provider scope")
    account = _fetch(_API + "/account", token, transport)
    if account.get("id") != expected_account:
        raise ValueError("provider account mismatch")
    page = _fetch(_API + "/events?" + urlencode({"limit": 100, "type": event_type}),
                  token, transport)
    events = page.get("data")
    if (page.get("object") != "list" or page.get("has_more") is not False
            or not isinstance(events, list) or len(events) > 100):
        raise ValueError("incomplete provider event page")
    matched = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("invalid provider event")
        obj = (event.get("data") or {}).get("object")
        request = event.get("request")
        if not isinstance(obj, dict) or not isinstance(request, dict):
            continue
        if obj.get("id") == object_id and request.get("idempotency_key") == correlation_id:
            if event.get("type") != event_type or not _valid(event.get("id")):
                raise ValueError("conflicting provider event")
            matched.append({"event_id": event["id"], "correlation_id": correlation_id,
                            "operation": event_type, "object_id": object_id,
                            "status": "succeeded"})
    if len(matched) > 1:
        raise ValueError("ambiguous provider operation")
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("naive clock")
    return {"authenticated": True, "complete": True, "account": expected_account,
            "object_id": object_id, "checked_at": clock.astimezone(timezone.utc).isoformat(),
            "events": matched}
