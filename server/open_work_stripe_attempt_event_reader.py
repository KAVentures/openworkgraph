"""Read one exact Stripe event independently of an agent's claimed success.

Restricted local source credentials only; no provider writes, no event bodies in
receipts, and no business/customer outcome assertion. Callers must independently
admit a retained run/case and recheck consent after this network operation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import re
from typing import Any, Callable

import httpx

from .local_source_credentials import load_stripe_source
from .source_http_logging import private_source_http_logs
from .stripe_source_evidence import STRIPE_READ_API_VERSION

_ACCOUNT = re.compile(r"^acct_[A-Za-z0-9]{8,90}$")
_EVENT = re.compile(r"^evt_[A-Za-z0-9]{8,90}$")
_OBJECT = re.compile(r"^[A-Za-z0-9_]{1,160}$")
_MAX_BYTES = 64_000


def _read_stripe_json(path: str, key: str) -> dict[str, Any]:
    """Fixed-origin, bounded, redirect-free GET. Never accept a caller URL."""
    if path != "/v1/account" and not re.fullmatch(r"/v1/events/evt_[A-Za-z0-9]{8,90}", path):
        raise ValueError("unsupported provider read")
    with private_source_http_logs(), httpx.Client(
        follow_redirects=False, trust_env=False, timeout=8.0,
        headers={"Authorization": "Bearer " + key, "Accept": "application/json",
                 "Stripe-Version": STRIPE_READ_API_VERSION},
    ) as client:
        with client.stream("GET", "https://api.stripe.com" + path) as response:
            if response.status_code != 200:
                raise ValueError("provider unavailable")
            chunks = bytearray()
            for chunk in response.iter_bytes():
                if len(chunks) + len(chunk) > _MAX_BYTES:
                    raise ValueError("provider response too large")
                chunks.extend(chunk)
    try:
        import json
        result = json.loads(chunks)
        if not isinstance(result, dict):
            raise ValueError("invalid provider response")
        return result
    finally:
        chunks.clear()


def read_exact_stripe_attempt_event(
    *, account: str, object_id: str, operation: str, correlation_id: str,
    expected_event_id: str,
    credentials: Callable[[], dict[str, str] | None] = load_stripe_source,
    reader: Callable[[str, str], dict[str, Any]] = _read_stripe_json,
    recheck_authorization: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Supply a source-authenticated snapshot to verify_provider_attempt.

    Correlation is Stripe's independently returned request.idempotency_key.
    An absent key cannot be reconstructed from timestamp or agent testimony.
    Only exact event ID GETs are used, never a partial list/search result.
    """
    unknown = {"complete": False, "authenticated": False, "revoked": False}
    # A direct adapter caller must not bypass the dashboard/source grant by
    # omitting the callback. The route supplies a fresh actor-scoped guard.
    if recheck_authorization is None or not callable(recheck_authorization):
        return {**unknown, "revoked": True}
    if (not _ACCOUNT.fullmatch(str(account or ""))
            or not _EVENT.fullmatch(str(expected_event_id or ""))
            or not _OBJECT.fullmatch(str(object_id or ""))
            or not isinstance(operation, str) or not re.fullmatch(r"[a-z][a-z0-9_.]{1,100}", operation)
            or not isinstance(correlation_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", correlation_id)):
        return unknown
    try:
        env = credentials()
        if not isinstance(env, dict) or str(env.get("OWG_STRIPE_SOURCE_READ_ENABLED", "")).lower() not in ("true", "1", "on", "yes"):
            return {**unknown, "revoked": True}
        key = env.get("OWG_STRIPE_RESTRICTED_READ_KEY")
        if (env.get("OWG_STRIPE_ACCOUNT_ID") != account or not isinstance(key, str)
                or not key.startswith(("rk_test_", "rk_live_")) or len(key) > 256
                or "\n" in key or "\r" in key):
            return unknown
        if recheck_authorization is not None and recheck_authorization() is not True:
            return {**unknown, "revoked": True}
        source_account = reader("/v1/account", key)
        if (not isinstance(source_account, dict) or source_account.get("object") != "account"
                or source_account.get("id") != account):
            return unknown
        # The account lookup and exact event GET are separate network calls.
        # A revoked grant between them must prevent the second provider read.
        if recheck_authorization is not None and recheck_authorization() is not True:
            return {**unknown, "revoked": True}
        # A source credential can be disabled or rotated independently of the
        # dashboard grant. Never reuse the old key for a second provider GET.
        current_env = credentials()
        if (not isinstance(current_env, dict)
                or str(current_env.get("OWG_STRIPE_SOURCE_READ_ENABLED", "")).lower()
                   not in ("true", "1", "on", "yes")
                or current_env.get("OWG_STRIPE_ACCOUNT_ID") != account
                or not isinstance(current_env.get("OWG_STRIPE_RESTRICTED_READ_KEY"), str)
                or not hmac.compare_digest(current_env["OWG_STRIPE_RESTRICTED_READ_KEY"], key)):
            return {**unknown, "revoked": True}
        event = reader("/v1/events/" + expected_event_id, key)
        if recheck_authorization is not None and recheck_authorization() is not True:
            return {**unknown, "revoked": True}
        # Discard evidence if credentials were revoked or rotated during I/O.
        final_env = credentials()
        if (not isinstance(final_env, dict)
                or str(final_env.get("OWG_STRIPE_SOURCE_READ_ENABLED", "")).lower()
                   not in ("true", "1", "on", "yes")
                or final_env.get("OWG_STRIPE_ACCOUNT_ID") != account
                or not isinstance(final_env.get("OWG_STRIPE_RESTRICTED_READ_KEY"), str)
                or not hmac.compare_digest(final_env["OWG_STRIPE_RESTRICTED_READ_KEY"], key)):
            return {**unknown, "revoked": True}
        # Stripe accounts can have test and live events. The restricted key's
        # environment is part of the independently authenticated source
        # identity; an exact ID match in the wrong mode is not evidence.
        expected_live = key.startswith("rk_live_")
        if (not isinstance(event, dict) or event.get("object") != "event"
                or event.get("id") != expected_event_id
                or type(event.get("livemode")) is not bool
                or event["livemode"] is not expected_live):
            return unknown
        # Connect events may be returned with a connected-account identity.
        # Never treat a different account's event as evidence for this source,
        # even when the supplied event/object/idempotency identifiers agree.
        if "account" in event and event["account"] != account:
            return unknown
        # Reject provider events dated in the future rather than allowing the
        # downstream verifier to infer a historical observation from them.
        created = event.get("created")
        if type(created) is not int or created <= 0:
            return unknown
        now = datetime.now(timezone.utc)
        try:
            occurred = datetime.fromtimestamp(created, timezone.utc)
        except (ValueError, OverflowError, OSError):
            return unknown
        if occurred > now:
            return unknown
        data = event.get("data")
        obj = data.get("object") if isinstance(data, dict) else None
        request = event.get("request")
        if (not isinstance(obj, dict) or obj.get("id") != object_id
                or not isinstance(request, dict)
                or request.get("idempotency_key") != correlation_id
                or event.get("type") != operation):
            return unknown
        # Stripe's event existence does not prove a successful provider action.
        # Status observed is the maximum justified claim without a separate
        # operation-specific, independently verified status contract.
        return {"complete": True, "authenticated": True, "account": account,
                "object_id": object_id, "checked_at": datetime.now(timezone.utc).isoformat(),
                "events": [{"event_id": expected_event_id, "correlation_id": correlation_id,
                            "operation": operation, "object_id": object_id,
                            "occurred_at": occurred.isoformat(), "status": "observed"}]}
    except (OSError, ValueError, TypeError, OverflowError, httpx.HTTPError):
        return unknown
