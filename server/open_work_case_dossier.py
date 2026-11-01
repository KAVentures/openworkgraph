from __future__ import annotations

"""A source-scanned, owner-reviewed case dossier from existing Open Work engines.

Pure projection only: the REST caller owns dashboard authentication, the
explicit Stripe scan grant and fresh source/review rechecks. Neither this
projection nor a declared dependency is an obligation or execution grant.
"""
from datetime import datetime
import hmac
import re
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_case_graph import project_declared_case_graph

_ITEM = re.compile(r"^owg:w:[0-9a-f]{32}$")
_RECORD = re.compile(r"^owg:r:[0-9a-f]{32}$")
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_MAX_OBSERVED = 8
_UNKNOWN_REASONS = frozenset({
    "explicit_inventory_scan_not_authorized",
    "stripe_source_read_opt_in_off",
    "stripe_restricted_read_config_missing",
    "stripe_account_identity_mismatch",
    "invalid_local_reference_secret",
    "invalid_stripe_invoice_page",
    "oversize_stripe_invoice_page",
    "invalid_stripe_invoice_item",
    "invalid_or_duplicate_invoice_identifier",
    "conflicting_stripe_invoice_status",
    "invalid_empty_continuation_page",
    "invalid_case_context_projection",
    "stripe_invoice_inventory_unavailable",
    "source_unavailable",
    "case_not_in_bounded_inventory",
    "case_no_longer_open_or_reference_invalid",
})


def admitted_case_dossier_snapshot(
    snapshot: dict[str, Any], *, work_item_ref: str,
    expected_revision: int, expected_edit_token: str,
) -> tuple[dict[str, Any], tuple[tuple[Any, ...], ...]]:
    """Pin the target and every transitive owner-declared prerequisite.

    A signature including incarnation tokens is internal to the request.
    Recomputing it after a source scan prevents stale approvals or a deleted
    and re-created revision-1 declaration from being returned as current.
    """
    if (not isinstance(work_item_ref, str) or not _ITEM.fullmatch(work_item_ref)
        or type(expected_revision) is not int or expected_revision < 1
        or not isinstance(expected_edit_token, str)
        or not _TOKEN.fullmatch(expected_edit_token)):
        raise CaseDeclarationError("invalid case dossier revision or reference")

    graph = project_declared_case_graph(snapshot)
    rows = {row["work_item_ref"]: row for row in snapshot["items"]}
    selected = rows.get(work_item_ref)
    if selected is None:
        raise CaseDeclarationError("stale declaration: case not currently reviewed")
    token = selected.get("edit_token")
    if (type(selected.get("revision")) is not int
        or selected["revision"] != expected_revision
        or not isinstance(token, str) or not _TOKEN.fullmatch(token)
        or not hmac.compare_digest(token, expected_edit_token)):
        raise CaseDeclarationError("stale declaration version or incarnation")

    parents: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        parents.setdefault(edge["dependent_ref"], []).append(edge["prerequisite_ref"])
    admitted = {work_item_ref}
    pending = [work_item_ref]
    while pending:
        for prerequisite in parents.get(pending.pop(), []):
            if prerequisite not in admitted:
                admitted.add(prerequisite)
                pending.append(prerequisite)
    # The existing graph is prerequisite-first and deterministic.
    ordered = [node["work_item_ref"] for node in graph["nodes"]
               if node["work_item_ref"] in admitted]
    signature = tuple((
        ref, rows[ref]["revision"], rows[ref].get("edit_token"),
        rows[ref]["goal_code"], rows[ref]["expected_outcome_code"],
        rows[ref]["updated_at"], rows[ref]["expires_at"],
        tuple(rows[ref]["depends_on_work_item_refs"]),
    ) for ref in ordered)
    prerequisites = [{
        "work_item_ref": ref,
        "revision": rows[ref]["revision"],
        "goal_code": rows[ref]["goal_code"],
        "expected_outcome_code": rows[ref]["expected_outcome_code"],
        "provenance": "explicit_owner_declaration_not_source_verified",
        "expected_outcome_verified": False,
        "source_state": "unknown_until_explicit_recheck",
    } for ref in ordered if ref != work_item_ref]
    return ({
        "work_item_ref": work_item_ref,
        "owner_declaration": {
            "goal_code": selected["goal_code"],
            "expected_outcome_code": selected["expected_outcome_code"],
            "revision": selected["revision"],
            "updated_at": selected["updated_at"],
            "expires_at": selected["expires_at"],
            "provenance": "explicit_owner_declaration_not_source_verified",
        },
        "declared_prerequisites": prerequisites,
        "declared_prerequisite_count": len(prerequisites),
        "dependency_graph_reviewed": True,
    }, signature)


def compose_reviewed_case_dossier(
    admitted: dict[str, Any], source_context: dict[str, Any],
) -> dict[str, Any]:
    """Minimize an existing explicitly scanned Stripe case-context response.

    Do not pass through arbitrary candidate, timeline, provider IDs, customer
    data, labels or source metadata. A missing open-invoice match is UNKNOWN,
    never proof of payment or customer success.
    """
    ref = admitted["work_item_ref"]
    if (not isinstance(source_context, dict)
        or source_context.get("format") != "openworkgraph.open-work-case-context.v1"
        or source_context.get("work_item_ref") != ref):
        raise CaseDeclarationError("invalid scanned case context identity")
    availability = source_context.get("availability")
    if availability not in ("source_reported", "unknown"):
        raise CaseDeclarationError("invalid scanned case availability")
    evidence = source_context.get("source_evidence")
    if not isinstance(evidence, dict):
        raise CaseDeclarationError("invalid scanned source evidence")
    verified_at = None
    complete = False
    state = "unknown"
    reason = None
    observations: list[dict[str, Any]] = []
    if availability == "source_reported":
        if (evidence.get("provider") != "stripe"
            or evidence.get("source_state") != "open"
            or evidence.get("status_scope") != "stripe_invoice_open"
            or type(evidence.get("inventory_complete")) is not bool):
            raise CaseDeclarationError("conflicting scanned source status")
        stamp = evidence.get("verified_at")
        if not isinstance(stamp, str) or len(stamp) > 40:
            raise CaseDeclarationError("invalid scanned source clock")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            raise CaseDeclarationError("invalid scanned source clock") from None
        if parsed.tzinfo is None:
            raise CaseDeclarationError("unscoped scanned source clock")
        verified_at = stamp
        complete = evidence["inventory_complete"]
        state = "open"
        observed = source_context.get("related_owg_evidence", [])
        if not isinstance(observed, list) or len(observed) > _MAX_OBSERVED:
            raise CaseDeclarationError("invalid bounded retained evidence")
        for item in observed:
            if not isinstance(item, dict) or not isinstance(item.get("resource_ref"), str) or not _RECORD.fullmatch(item["resource_ref"]):
                raise CaseDeclarationError("invalid retained evidence reference")
            counts = {}
            for name in ("observed_event_count", "observed_session_count", "observed_actor_count"):
                value = item.get(name)
                if type(value) is not int or not 0 <= value <= 10_000_000:
                    raise CaseDeclarationError("invalid retained evidence count")
                counts[name] = value
            observations.append({
                "resource_ref": item["resource_ref"], **counts,
                "timeline_truncated": item.get("timeline_truncated") is True,
                "observed_not_verified_handoff": True,
            })
    else:
        raw_reason = source_context.get("reason")
        reason = raw_reason if raw_reason in _UNKNOWN_REASONS else "source_unavailable"
    return {
        "format": "openworkgraph.local-reviewed-case-dossier.v1",
        **admitted,
        "source_reported_status": {
            "availability": availability,
            "state": state,
            "verified_at": verified_at,
            "bounded_inventory_complete": complete,
            "reason": reason,
            "provider_status_not_customer_outcome": True,
        },
        "retained_observation_summary": observations,
        "related_record_history_incomplete": (
            source_context.get("related_record_history_incomplete") is True
            if availability == "source_reported" else True
        ),
        "absence_of_observation_proves_no_work": False,
        "business_obligation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "agent_execution_authorized": False,
        "source_scan_explicitly_requested": True,
        "source_write_performed": False,
        "not_synced": True,
    }


__all__ = ["admitted_case_dossier_snapshot", "compose_reviewed_case_dossier"]
