from __future__ import annotations

"""Batch read-only annotation of source candidates with retained local evidence.

An absence means only 'no *matching retained OWG observation*'; it must never
be called 'untouched', 'unanswered', 'unworked', or a verified obligation.
Exactly matched source identities, canonical retained events, and local-only
resolver entries are required. One bounded SQL pass, not one scan per invoice.
"""
from typing import Any
import re

from . import local_reference_lookup, db

_RESOURCE = re.compile(r"^owg:r:[0-9a-f]{32}$")
_INVOICE = re.compile(r"^in_[A-Za-z0-9]{10,90}$")
_MAX_ITEMS = 300


def stripe_invoice_observation_states(invoice_ids: list[str]) -> dict[str, str]:
    """Return observed / not_in_retained_owg / uncertain for valid IDs.

    Both native invoice IDs and resolver locators remain strictly server-side.
    The caller projects the states only; never serialize a native ID or the
    local resolver map in the user-facing preview.
    """
    if not isinstance(invoice_ids, list) or len(invoice_ids) > _MAX_ITEMS:
        return {}
    wanted = {i for i in invoice_ids if isinstance(i, str) and _INVOICE.fullmatch(i)}
    if not wanted:
        return {}
    default = {invoice: "not_in_retained_owg" for invoice in wanted}
    try:
        refs = local_reference_lookup._read().get("resources", {})
        if not isinstance(refs, dict):
            return {invoice: "uncertain" for invoice in wanted}
        by_ref: dict[str, str] = {}
        for ref, info in refs.items():
            if (not isinstance(ref, str) or not _RESOURCE.fullmatch(ref) or
                not isinstance(info, dict) or
                info.get("provider") != "stripe" or
                info.get("host") != "dashboard.stripe.com" or
                info.get("resource_kind") != "invoice"):
                continue
            locator = info.get("resolver_locator")
            if not isinstance(locator, str) or not locator.startswith("invoice:"):
                continue
            invoice = locator[len("invoice:"):]
            if invoice in wanted:
                by_ref[ref] = invoice
        if not by_ref:
            return default

        # SQLite JSON1 is already required by record_graph. Exact identity
        # matching rules out a resource ID merely mentioned inside free text.
        # One query across all selected candidates avoids a 300x history scan.
        ids = sorted(by_ref)
        placeholders = ",".join("?" for _ in ids)
        query = (
            "SELECT lower(CASE WHEN json_valid(metadata_json) "
            "THEN json_extract(metadata_json, '$.resource_reference.resource_ref') "
            "ELSE NULL END) AS ref, "
            "json_extract(metadata_json, '$.resource_reference.provider') AS provider, "
            "json_extract(metadata_json, '$.resource_reference.resource_kind') AS kind, "
            "json_extract(metadata_json, '$.resource_reference.host') AS host, "
            "count(*) AS observations FROM events WHERE "
            "json_valid(metadata_json) AND "
            "lower(json_extract(metadata_json, '$.resource_reference.resource_ref')) "
            f"IN ({placeholders}) "
            "GROUP BY ref, provider, kind, host"
        )
        rows = db.rows(query, tuple(ids))
        # All rows referring to the same opaque record must claim one
        # consistent source identity. A conflict is 'uncertain', not 'seen'.
        observed: set[str] = set()
        conflicts: set[str] = set()
        for row in rows:
            ref = row.get("ref")
            if ref not in by_ref:
                continue
            invoice = by_ref[ref]
            if (row.get("provider"), row.get("kind"), row.get("host")) == (
                "stripe", "invoice", "dashboard.stripe.com"):
                observed.add(invoice)
            else:
                conflicts.add(invoice)
        for invoice in wanted:
            if invoice in conflicts:
                default[invoice] = "uncertain"
            elif invoice in observed:
                default[invoice] = "observed"
        return default
    except (OSError, TypeError, ValueError, RuntimeError):
        return {invoice: "uncertain" for invoice in wanted}


__all__ = ["stripe_invoice_observation_states"]
