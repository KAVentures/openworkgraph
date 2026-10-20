"""Ephemeral multi-case Jira process view from explicitly selected source receipts.

Every receipt was read only through a separate authorized, user-clicked
Jira source check. Process case IDs are temporary ordinals in this call.
No tenant inventory scan, arbitrary URL fetch, native IDs or actor values.
"""
from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from .process_mining_csv import mine_csv_events

_LABELS = {"status_transition": "Status transition",
           "assignee_transition": "Assignee transition"}
_MAX_CASES = 8
_MAX_EVENTS_EACH = 50


def jira_selected_cases_process(receipts: list[Any]) -> dict[str, Any]:
    if not isinstance(receipts, list) or not 1 <= len(receipts) <= _MAX_CASES:
        raise ValueError("select between 1 and 8 exact observed Jira records")
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("case_id", "activity", "timestamp"))
    available = 0
    partial = 0
    no_audit = 0
    raw_rows = 0
    # The input order is the only case ordering. No stable record identity,
    # actor or provider event ref ever leaves this ephemeral analysis.
    for ordinal, receipt in enumerate(receipts, 1):
        if (not isinstance(receipt, dict)
                or receipt.get("availability") != "source_reported"
                or receipt.get("provider") != "jira"
                or receipt.get("audit_available") is not True
                or not isinstance(receipt.get("review_audit_events"), list)):
            no_audit += 1
            continue
        entries = receipt["review_audit_events"]
        seen: set[str] = set()
        rows: list[tuple[str, str]] = []
        for value in entries[:_MAX_EVENTS_EACH]:
            if not isinstance(value, dict):
                continue
            kind = value.get("action")
            stamp = value.get("observed_at")
            ref = value.get("source_event_ref")
            # Unlike native provider IDs, this ref was already minimized by
            # OWG's single-issue receipt adapter.
            if (not isinstance(kind, str) or kind not in _LABELS
                    or not isinstance(stamp, str) or not isinstance(ref, str)
                    or not ref or ref in seen):
                continue
            seen.add(ref)
            rows.append((_LABELS[kind], stamp))
        if not rows:
            no_audit += 1
            continue
        available += 1
        if receipt.get("audit_complete") is not True or len(entries) > _MAX_EVENTS_EACH:
            partial += 1
        raw_rows += len(rows)
        for action, stamp in rows:
            writer.writerow((f"Case {ordinal}", action, stamp))
    process = mine_csv_events(stream.getvalue().encode("utf-8")) if raw_rows else None
    if process:
        process["source"] = "explicit_user_selected_jira_issue_changelogs"
        process["source_event_window_may_be_partial"] = bool(partial or no_audit)
        process["source_identity_comparability_verified"] = False
        process["cross_case_links_inferred"] = False
        process["business_outcomes_verified"] = False
        process["source_case_ids_included"] = False
        process["source_user_ids_included"] = False
    return {
        "format": "openworkgraph.jira-selected-cases-process.v1",
        "availability": "source_audit" if process else "no_eligible_source_audits",
        "requested_record_count": len(receipts),
        "audited_record_count": available,
        "unavailable_or_empty_record_count": no_audit,
        "partial_audit_record_count": partial,
        "all_selected_source_audits_complete": available == len(receipts) and partial == 0,
        "process": process,
        "source_case_ids_included": False,
        "source_audit_event_ids_included": False,
        "provider_actor_ids_included": False,
        "business_outcomes_verified": False,
        "tenant_inventory_scanned": False,
        "cross_case_identity_inferred": False,
        "source_reads_performed_by_projector": False,
        "result_persisted": False,
    }


__all__ = ["jira_selected_cases_process"]
