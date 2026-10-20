"""Minimize an explicitly authorized Jira issue's source audit into a case path.

No new credentials, provider reads, native issue IDs, actor identifiers or
business-outcome claims. Calls to Jira are performed only by the separate,
previously opt-in source receipt adapter at a deliberate dashboard request.
"""
from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from .process_mining_csv import mine_csv_events

_ALLOWED = {"status_transition": "Status transition",
            "assignee_transition": "Assignee transition"}
_MAX_AUDIT_EVENTS = 50


def jira_audit_process_preview(receipt: Any) -> dict[str, Any]:
    """Convert already obtained/minimized Jira audit rows; no provider I/O."""
    if not isinstance(receipt, dict) or receipt.get("availability") != "source_reported" or receipt.get("provider") != "jira":
        return {
            "format": "openworkgraph.jira-source-process-preview.v1",
            "availability": "unavailable",
            "reason": "no_authorized_jira_source_receipt",
            "process": None,
            "source_audit_complete": False,
            "verified_business_outcome": False,
        }
    rows = receipt.get("review_audit_events")
    if receipt.get("audit_available") is not True or not isinstance(rows, list):
        return {
            "format": "openworkgraph.jira-source-process-preview.v1",
            "availability": "source_status_only",
            "reason": "source_audit_unavailable",
            "process": None,
            "source_audit_complete": False,
            "verified_business_outcome": False,
        }
    accepted: list[tuple[str, str, str]] = []
    used_refs: set[str] = set()
    for row in rows[:_MAX_AUDIT_EVENTS]:
        if not isinstance(row, dict):
            continue
        label = _ALLOWED.get(row.get("action"))
        stamp = row.get("observed_at")
        evidence_ref = str(row.get("source_event_ref") or "")
        if not label or not isinstance(stamp, str) or not stamp or not evidence_ref:
            continue
        # Deduplicate only strict opaque provider-issued audit references.
        if evidence_ref in used_refs:
            continue
        used_refs.add(evidence_ref)
        accepted.append(("Case 1", label, stamp))
    if not accepted:
        return {
            "format": "openworkgraph.jira-source-process-preview.v1",
            "availability": "no_bounded_audit_events",
            "process": None,
            "source_audit_complete": bool(receipt.get("audit_complete")),
            "verified_business_outcome": False,
        }
    csv_data = StringIO(newline="")
    writer = csv.writer(csv_data, lineterminator="\n")
    writer.writerow(("case_id", "activity", "timestamp"))
    writer.writerows(accepted)
    # Reuse the exact same bounded and tested case/variant analytics. The
    # intermediate CSV exists only in process memory.
    result = mine_csv_events(csv_data.getvalue().encode("utf-8"))
    result["source"] = "opted_in_authenticated_jira_single_issue_changelog"
    result["source_audit_complete"] = bool(receipt.get("audit_complete"))
    result["source_event_window_may_be_partial"] = not result["source_audit_complete"]
    result["source_case_ids_included"] = False
    result["source_user_ids_included"] = False
    result["independent_jira_operational_outcome"] = receipt.get(
        "verified_operational_outcome", {}
    ).get("verified") is True if isinstance(receipt.get("verified_operational_outcome"), dict) else False
    # A resolved ticket is NEVER automatically proof of customer business success.
    result["business_outcomes_verified"] = False
    return {
        "format": "openworkgraph.jira-source-process-preview.v1",
        "availability": "source_audit",
        "process": result,
        "source_audit_complete": result["source_audit_complete"],
        "verified_business_outcome": False,
        "source_records_included": False,
        "source_case_ids_included": False,
        "provider_account_ids_included": False,
    }


__all__ = ["jira_audit_process_preview"]
