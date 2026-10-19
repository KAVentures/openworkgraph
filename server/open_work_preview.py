from __future__ import annotations

"""Opt-in, source-backed Open Work preview for *unobserved* Stripe invoices.

This module never modifies canonical OWG evidence, workflow maps, or a provider.
It does not infer a duty to contact a customer merely from an open invoice.
Invoice IDs and financial/customer values remain in memory only while reading,
not in the source-projected response. This is a local dashboard preview, not a
general company-wide agent permission or a hosted connector.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx

from .local_source_credentials import load_stripe_source
from .stripe_source_evidence import _get as account_read
from .stripe_source_evidence import STRIPE_READ_API_VERSION
from .local_auth import _read_or_create_secret

_ACCOUNT = re.compile(r"^acct_[A-Za-z0-9]{8,90}$")
_INVOICE = re.compile(r"^in_[A-Za-z0-9]{10,90}$")
_PAGE_SIZE = 100
_MAX_PAGES = 3
# A 100-item Stripe invoice page may legitimately exceed 256 KB because the
# API includes nested line and tax structures, even when OWG projects only
# metadata. Keep a strict, per-page 2 MiB cap and reject anything larger.
_MAX_BODY_BYTES = 2_097_152


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "format": "openworkgraph.open-work-preview.v1",
        "provider": "stripe",
        "availability": "unknown",
        "reason": reason,
        "candidates": [],
        "source_coverage_complete": False,
        "source_reported_not_human_observed": True,
        "not_a_verified_business_obligation": True,
        "read_only": True,
        "persisted": False,
    }


def _read_invoice_list(url: str, key: str) -> dict[str, Any]:
    """Provider GET with an exact endpoint/query allowlist and bounded body."""
    parsed = urlsplit(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "https" or parsed.netloc != "api.stripe.com" or
        parsed.path != "/v1/invoices" or parsed.fragment or
        set(qs) - {"status", "limit", "starting_after"} or
        qs.get("status") != ["open"] or qs.get("limit") != [str(_PAGE_SIZE)] or
        any(len(items) != 1 for items in qs.values())):
        raise ValueError("unexpected_stripe_list_resource")
    if "starting_after" in qs and not _INVOICE.fullmatch(qs["starting_after"][0]):
        raise ValueError("invalid_invoice_pagination_cursor")

    with httpx.Client(follow_redirects=False, trust_env=False, timeout=8.0,
                      headers={"Authorization": "Bearer " + key,
                               "Accept": "application/json",
                               "Stripe-Version": STRIPE_READ_API_VERSION}) as client:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise ValueError("stripe_invoice_list_unavailable")
            chunks = bytearray()
            for part in response.iter_bytes():
                chunks.extend(part)
                if len(chunks) > _MAX_BODY_BYTES:
                    raise ValueError("oversize_invoice_inventory")
    result = json.loads(chunks)
    if not isinstance(result, dict):
        raise ValueError("invalid_stripe_invoice_inventory")
    return result


def _candidate_reference(reference_key: bytes, account: str, invoice_id: str) -> str:
    # Use a stable per-installation OWG secret, NEVER the replaceable Stripe key.
    # Domain separation prevents cross-feature correlation of keyed hashes.
    digest = hmac.new(reference_key,
                      ("open-work:stripe:invoice:v1:" + account + ":" + invoice_id).encode("utf-8"),
                      hashlib.sha256).hexdigest()[:32]
    return "owg:w:" + digest


def stripe_open_work_preview(
    *, explicit_scan_authorized: bool = False,
    environment: dict[str, str] | None = None,
    account_reader: Callable[[str, str], dict[str, Any]] = account_read,
    list_reader: Callable[[str, str], dict[str, Any]] = _read_invoice_list,
    max_pages: int = 1,
    now: datetime | None = None,
    candidate_enricher: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    reference_key: bytes | None = None,
) -> dict[str, Any]:
    """Read source-reported open invoice candidates, never infer payment duties.

    Requires a fresh explicit dashboard request in addition to the existing
    separately configured restricted read-only Stripe source connection.
    Can discover invoices not previously observed by OWG. No scheduler,
    AI-access rights, write action, background polling, or persistent copies.
    """
    if explicit_scan_authorized is not True:
        return _unavailable("explicit_inventory_scan_not_authorized")
    env = os.environ if environment is None else environment
    if environment is None and "OWG_STRIPE_SOURCE_READ_ENABLED" not in env:
        saved = load_stripe_source()
        if saved is not None:
            env = saved
    if str(env.get("OWG_STRIPE_SOURCE_READ_ENABLED") or "").lower() not in {"1", "true", "on", "yes"}:
        return _unavailable("stripe_source_read_opt_in_off")
    account = str(env.get("OWG_STRIPE_ACCOUNT_ID") or "")
    key = str(env.get("OWG_STRIPE_RESTRICTED_READ_KEY") or "")
    if (not _ACCOUNT.fullmatch(account) or
        not key.startswith(("rk_live_", "rk_test_")) or len(key) > 256 or
        "\n" in key or "\r" in key):
        return _unavailable("stripe_restricted_read_config_missing")
    if type(max_pages) is not int or not (1 <= max_pages <= _MAX_PAGES):
        raise ValueError("max_pages must be between 1 and 3")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must have a timezone")
    timestamp = int(current.timestamp())
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    previous: str | None = None
    has_more = True
    pages_read = 0
    try:
        source_account = account_reader("https://api.stripe.com/v1/account", key)
        if not isinstance(source_account, dict) or source_account.get("object") != "account" or source_account.get("id") != account:
            return _unavailable("stripe_account_identity_mismatch")
        local_key = reference_key if reference_key is not None else _read_or_create_secret(".open_work_reference_key").encode("utf-8")
        if not isinstance(local_key, bytes) or len(local_key) < 16:
            return _unavailable("invalid_local_reference_secret")
        for _ in range(max_pages):
            url = "https://api.stripe.com/v1/invoices?status=open&limit=100"
            if previous is not None:
                url += "&starting_after=" + previous  # validated ID from prior page
            result = list_reader(url, key)
            if not isinstance(result, dict) or result.get("object") != "list" or not isinstance(result.get("data"), list) or type(result.get("has_more")) is not bool:
                return _unavailable("invalid_stripe_invoice_page")
            invoices = result["data"]
            if len(invoices) > _PAGE_SIZE:
                return _unavailable("oversize_stripe_invoice_page")
            pages_read += 1
            for invoice in invoices:
                if not isinstance(invoice, dict):
                    return _unavailable("invalid_stripe_invoice_item")
                iid = str(invoice.get("id") or "")
                if not _INVOICE.fullmatch(iid) or iid in seen:
                    return _unavailable("invalid_or_duplicate_invoice_identifier")
                seen.add(iid)
                if invoice.get("object") != "invoice" or invoice.get("status") != "open" or invoice.get("paid") is not False:
                    # Fail closed on an inconsistent provider page: do not
                    # report a misleading partially verified invoice inventory.
                    return _unavailable("conflicting_stripe_invoice_status")
                due = invoice.get("due_date")
                due_valid = type(due) is int and 0 < due <= 4_102_444_800
                overdue = bool(due_valid and due < timestamp)
                candidate = {
                    "work_item_ref": _candidate_reference(local_key, account, iid),
                    "kind": "open_invoice",
                    "source_status": "open",
                    "attention_reason": "past_source_due_date" if overdue else "open_invoice_review_candidate",
                    "source_due_date_known": due_valid,
                    "past_due_date": overdue,
                    "priority": "unassigned",
                    "verification_method": "account_pinned_stripe_invoice_list",
                    "business_obligation_verified": False,
                    "customer_contact_authorized": False,
                    "historically_observed_in_owg": "not_checked",
                }
                if candidate_enricher is not None:
                    # Internal enrichment sees a source-validated record ID.
                    # It must not return that ID, customer values or credentials.
                    candidate = candidate_enricher(iid, candidate)
                    if not isinstance(candidate, dict) or candidate.get("work_item_ref") != _candidate_reference(local_key, account, iid):
                        return _unavailable("invalid_case_context_projection")
                candidates.append(candidate)
            has_more = result["has_more"]
            if not has_more:
                break
            if not invoices:
                return _unavailable("invalid_empty_continuation_page")
            previous = str(invoices[-1]["id"])
        return {
            "format": "openworkgraph.open-work-preview.v1",
            "provider": "stripe",
            "availability": "source_reported",
            "verified_at": current.astimezone(timezone.utc).isoformat(),
            "candidates": candidates,
            "returned": len(candidates),
            "pages_read": pages_read,
            "source_coverage_complete": not has_more,
            "source_limit_reached": has_more,
            "source_reported_not_human_observed": True,
            "not_a_verified_business_obligation": True,
            "no_inference_of_customer_response_or_payment_permission": True,
            "customer_identifiers_included": False,
            "financial_amounts_included": False,
            "native_invoice_ids_included": False,
            "read_only": True,
            "persisted": False,
            "automatic_actions": False,
        }
    except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError):
        return _unavailable("stripe_invoice_inventory_unavailable")


__all__ = ["stripe_open_work_preview"]
