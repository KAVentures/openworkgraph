from __future__ import annotations

"""Default-off local Stripe processor status receipts for observed records.

Restricted read-only Stripe keys are operator provisioned; none are accepted
from an MCP tool request, browser event, URL, or API query. This adapter never
uses write APIs and never persists a provider response or source credentials.
"""
from datetime import datetime, timezone
import os
import re
import secrets
import hashlib
import hmac
from typing import Any, Callable

import httpx

from .local_reference_lookup import resolve_resource_reference
from .local_source_credentials import load_stripe_source
from .source_http_logging import private_source_http_logs

_REF = re.compile(r"^owg:r:[0-9a-f]{32}$")
_ACCT = re.compile(r"^acct_[A-Za-z0-9]{8,90}$")
_INVOICE = re.compile(r"^invoice:(in_[A-Za-z0-9]{10,90})$")
_REFUND = re.compile(r"^refund:(re_[A-Za-z0-9]{10,90})$")
# The invoice.paid boolean and invoice status contract have been checked
# against Stripe's dated Acacia API schema. Pin all related read calls to the
# same release rather than inheriting each connected account's default version.
# Real-account acceptance remains a separate release gate.
STRIPE_READ_API_VERSION = "2024-10-28.acacia"
_MAX_BYTES = 64_000
_LIFECYCLE_SECRET=secrets.token_bytes(32)


def _invoice_lifecycle(response,resource_ref):
    fields={'created':'invoice_created','finalized_at':'invoice_finalized',
            'marked_uncollectible_at':'invoice_marked_uncollectible',
            'paid_at':'invoice_paid','voided_at':'invoice_voided'}
    transitions=response.get('status_transitions')
    invalid=0
    if transitions is not None and not isinstance(transitions,dict):invalid+=1
    transitions=transitions if isinstance(transitions,dict) else {}
    values={'created':response.get('created'),**{key:transitions.get(key) for key in fields if key!='created'}}
    stamps={}
    for key,value in values.items():
        if value is None:continue
        try:
            if type(value) is not int or value<=0:raise ValueError()
            stamps[key]=datetime.fromtimestamp(value,timezone.utc)
        except (ValueError,OverflowError,OSError):invalid+=1
    conflict=(any(stamp<stamps['created'] for key,stamp in stamps.items() if key!='created') if 'created' in stamps else False)
    if 'finalized_at' in stamps:
        conflict=conflict or any(stamp<stamps['finalized_at'] for key,stamp in stamps.items() if key not in {'created','finalized_at'})
    if 'marked_uncollectible_at' in stamps:
        conflict=conflict or any(stamps[key]<stamps['marked_uncollectible_at'] for key in ('paid_at','voided_at') if key in stamps)
    conflict=conflict or ('paid_at' in stamps and 'voided_at' in stamps)
    events=[]
    if not conflict:
        for key,stamp in stamps.items():
            at=stamp.isoformat()
            digest=hmac.new(_LIFECYCLE_SECRET,(resource_ref+'|'+key+'|'+at).encode(),hashlib.sha256).hexdigest()[:32]
            events.append({'action':fields[key],'observed_at':at,'source_event_ref':'source_event:'+digest})
        events.sort(key=lambda item:item['observed_at'])
    return {'object_type':'invoice','process_audit_events':events,
        'source_lifecycle_projection_available':bool(events),
        'source_lifecycle_projection_is_complete_audit':False,
        'lifecycle_projection_diagnostics':{'invalid_timestamp_field_count':invalid,'chronology_conflict':bool(conflict)}}


def _unknown(reason: str) -> dict[str, Any]:
    return {
        "provider": "stripe", "availability": "unknown", "reason": reason,
        "source_state_reported": False, "verified_business_outcome": False,
        "not_a_claim_of_customer_bank_settlement": True,
    }


def _get(url: str, key: str) -> dict[str, Any]:
    from urllib.parse import urlsplit
    import json
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.netloc) != ("https", "api.stripe.com"):
        raise ValueError("unexpected_stripe_origin")
    if not (parsed.path == "/v1/account"
            or re.fullmatch(r"/v1/(invoices/in_|refunds/re_)[A-Za-z0-9]{10,90}", parsed.path)):
        raise ValueError("unexpected_stripe_read_resource")
    with private_source_http_logs(), httpx.Client(follow_redirects=False, trust_env=False, timeout=8.0,
                      headers={"Authorization": "Bearer " + key,
                               "Accept": "application/json",
                               "Stripe-Version": STRIPE_READ_API_VERSION}) as client:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise ValueError("source_unavailable")
            chunks = bytearray()
            for item in response.iter_bytes():
                chunks.extend(item)
                if len(chunks) > _MAX_BYTES:
                    raise ValueError("oversize_source")
    data = json.loads(chunks)
    if not isinstance(data, dict):
        raise ValueError("invalid_source_json")
    return data


def stripe_record_receipt(
    resource_ref: str, *,
    environment: dict[str, str] | None = None,
    resolver: Callable[[str], dict[str, Any] | None] = resolve_resource_reference,
    reader: Callable[[str, str], dict[str, Any]] = _get,
    include_lifecycle: bool = False,
) -> dict[str, Any]:
    env = os.environ if environment is None else environment
    # A signed Finder-launched app does not inherit interactive shell variables.
    # Use a separately confirmed, OS-vault-backed *local* source connection
    # only when no explicit env authorization override was supplied. Explicit
    # OFF always wins; explicit ON with missing credentials fails closed.
    if environment is None and "OWG_STRIPE_SOURCE_READ_ENABLED" not in env:
        stored = load_stripe_source()
        if stored is not None:
            env = stored
    if str(env.get("OWG_STRIPE_SOURCE_READ_ENABLED") or "").lower() not in ("true", "1", "on", "yes"):
        return _unknown("stripe_source_read_opt_in_off")
    if not _REF.fullmatch(str(resource_ref or "")):
        return _unknown("invalid_opaque_resource")
    account = str(env.get("OWG_STRIPE_ACCOUNT_ID") or "")
    key = str(env.get("OWG_STRIPE_RESTRICTED_READ_KEY") or "")
    if not _ACCT.fullmatch(account) or not key.startswith(("rk_live_", "rk_test_")) or len(key) > 256 or "\n" in key or "\r" in key:
        return _unknown("stripe_restricted_read_config_missing")
    record = resolver(resource_ref)
    if not isinstance(record, dict) or record.get("provider") != "stripe" or record.get("host") != "dashboard.stripe.com":
        return _unknown("stripe_resolved_source_not_permitted")
    kind = record.get("resource_kind")
    locator = str(record.get("resolver_locator") or "")
    match = (_INVOICE.fullmatch(locator) if kind == "invoice"
             else _REFUND.fullmatch(locator) if kind == "refund" else None)
    if match is None:
        return _unknown("stripe_invalid_stored_locator")

    try:
        # Confirm the key belongs to exactly the authorized business account.
        # A key for a different Stripe account must never probe the record ID.
        account_response = reader("https://api.stripe.com/v1/account", key)
        if account_response.get("object") != "account" or account_response.get("id") != account:
            return _unknown("stripe_account_identity_mismatch")
        object_id = match.group(1)
        path = "invoices" if kind == "invoice" else "refunds"
        response = reader(f"https://api.stripe.com/v1/{path}/{object_id}", key)
        if response.get("id") != object_id or response.get("object") != kind:
            return _unknown("stripe_record_identity_mismatch")
        state = str(response.get("status") or "")
        if kind == "invoice":
            if state not in {"draft", "open", "paid", "uncollectible", "void"}:
                return _unknown("stripe_unknown_invoice_status")
            if state == "paid" and response.get("paid") is not True:
                return _unknown("stripe_invoice_paid_flag_conflict")
            verified = state == "paid" and response.get("paid") is True
            outcome = "stripe_invoice_paid" if verified else "unverified_invoice_payment"
        else:
            if state not in {"pending", "requires_action", "succeeded", "failed", "canceled"}:
                return _unknown("stripe_unknown_refund_status")
            verified = state == "succeeded"
            outcome = "stripe_refund_processor_succeeded" if verified else "unverified_refund_settlement"
        receipt = {
            "provider": "stripe", "availability": "source_reported",
            "source_state_reported": True, "source_state": state,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verification_method": "opted_in_restricted_key_stripe_account_and_record_get",
            "verified_business_outcome": verified,
            "verified_operational_outcome": {
                "kind": outcome, "verified": verified,
                "scope": "stripe_processor_only",
            },
            "native_business_id_included": False, "names_and_amounts_included": False,
            "not_a_claim_of_customer_bank_settlement": True,
            "handoff_verified": False, "source_audit_history_available": False,
        }
        if include_lifecycle is True and kind=='invoice':receipt.update(_invoice_lifecycle(response,resource_ref))
        return receipt
    except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError):
        return _unknown("stripe_source_unavailable")


__all__ = ["stripe_record_receipt"]
