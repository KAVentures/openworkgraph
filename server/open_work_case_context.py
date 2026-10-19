from __future__ import annotations

"""Local, source-first Open Work case context.

A Stripe preview item is not an OWG recording and does not imply a duty to
collect payment. This joins one explicitly requested, currently source-reported
item to *retained* OWG evidence only when the same native Stripe invoice ID
was previously registered through opt-in resource-reference capture.
"""
import re
from typing import Any, Callable

from .local_reference_lookup import find_observed_resource_references
from .record_graph import record_graph
from .open_work_preview import stripe_open_work_preview

_ITEM = re.compile(r"^owg:w:[0-9a-f]{32}$")


def stripe_open_work_case_context(
    work_item_ref: str, *, explicit_scan_authorized: bool = False,
    inventory_scan: Callable[..., dict[str, Any]] = stripe_open_work_preview,
    history_lookup: Callable[..., list[str]] = find_observed_resource_references,
    record_reader: Callable[..., dict[str, Any]] = record_graph,
    max_pages: int = 3,
    **scan_options: Any,
) -> dict[str, Any]:
    """Return a bounded, read-only case handoff for a locally authorized human.

    Authorization for the inventory scan is re-evaluated on every call.
    A caller cannot supply a native provider record ID, choose another account
    or infer unknown cases by guessing an OWG reference.
    """
    if not _ITEM.fullmatch(str(work_item_ref or "")):
        raise ValueError("work_item_ref must be a previously returned opaque owg:w: reference")
    if explicit_scan_authorized is not True:
        return _not_available(work_item_ref, "explicit_inventory_scan_not_authorized")

    matched: list[dict[str, Any]] = []

    def enrich(invoice_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        if candidate["work_item_ref"] != work_item_ref:
            return candidate
        links = history_lookup(
            provider="stripe", resource_kind="invoice",
            host="dashboard.stripe.com", locator="invoice:" + invoice_id,
            limit=8,
        )
        observed = []
        history_incomplete = False
        for ref in links[:8]:
            if not re.fullmatch(r"owg:r:[0-9a-f]{32}", str(ref)):
                continue
            # Important: no second provider call. Stripe account was checked by
            # inventory_scan; this only reads retained canonical OWG evidence.
            graph = record_reader(ref, include_source_status=False)
            if not isinstance(graph, dict) or not graph.get("record_observed"):
                continue
            if graph.get("record_type") != {"provider": "stripe", "kind": "invoice"}:
                continue
            if graph.get("source_event_limit_reached") or not graph.get("record_identity_complete_within_retained_window", False):
                history_incomplete = True
                continue
            observed.append({
                "resource_ref": ref,
                "observed_event_count": int(graph.get("observed_event_count") or 0),
                "observed_session_count": int(graph.get("observed_session_count") or 0),
                "observed_actor_count": int(graph.get("observed_actor_count") or 0),
                "timeline": (graph.get("timeline") or [])[:50],
                "timeline_truncated": bool(graph.get("timeline_truncated")),
                "observed_not_verified_handoff": True,
                "recorded_behavior_not_policy": True,
            })
        candidate["historically_observed_in_owg"] = ("yes" if observed else
            "uncertain" if history_incomplete else "no_matching_retained_reference")
        matched.append({
            "item": candidate,
            "observed_record_context": observed,
            "related_record_history_incomplete": history_incomplete,
        })
        return candidate

    inventory = inventory_scan(
        explicit_scan_authorized=True, max_pages=max_pages,
        candidate_enricher=enrich, **scan_options,
    )
    if inventory.get("availability") != "source_reported":
        return _not_available(work_item_ref, str(inventory.get("reason") or "source_unavailable"))
    if not matched:
        reason = ("case_not_in_bounded_inventory" if not inventory.get("source_coverage_complete")
                  else "case_no_longer_open_or_reference_invalid")
        return _not_available(work_item_ref, reason)

    item = matched[0]
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "availability": "source_reported",
        "work_item_ref": work_item_ref,
        "source_evidence": {
            "provider": "stripe",
            "source_state": "open",
            "verified_at": inventory.get("verified_at"),
            "status_scope": "stripe_invoice_open",
            "inventory_complete": inventory.get("source_coverage_complete"),
            "no_claim_of_customer_payment_obligation": True,
        },
        "candidate": item["item"],
        "related_owg_evidence": item["observed_record_context"],
        "related_record_history_incomplete": item["related_record_history_incomplete"],
        "workflow_context": {
            "mapping_still_available": True,
            "how_to_find_prior_workflows": (
                "Use existing OWG workflow family discovery and inspect evidence "
                "before treating a pattern as suitable for this case."
            ),
            "historical_behavior_establishes_policy": False,
            "procedure_approved_for_execution": False,
        },
        "proposed_goal": "Review the current source-reported open invoice; determine whether follow-up is appropriate.",
        "declared_business_policy": "not_supplied",
        "customer_contact_authorized": False,
        "agent_execution_authorized": False,
        "verified_customer_outcome": False,
        "obligations_and_exceptions": "unknown",
        "privacy": {
            "native_provider_ids_included": False,
            "customer_data_included": False,
            "amounts_included": False,
            "source_read_only": True,
            "no_new_capture_or_upload": True,
        },
        "agent_handoff_advisory_only": True,
    }


def _not_available(ref: str, reason: str) -> dict[str, Any]:
    return {
        "format": "openworkgraph.open-work-case-context.v1",
        "work_item_ref": ref,
        "availability": "unknown",
        "reason": reason,
        "source_evidence": {"source_state": "unknown"},
        "related_owg_evidence": [],
        "agent_execution_authorized": False,
        "verified_customer_outcome": False,
        "read_only": True,
    }


__all__ = ["stripe_open_work_case_context"]
