from __future__ import annotations

"""Typed, bounded evidence graph over the existing reviewed case DAG and source scans.

This is a *projection*, not another case ledger, source connector, or outcome
verifier. The caller authenticates the dashboard session, obtains the current
consent/retention-filtered declaration signature, explicitly scans sources,
and rechecks that signature after every source read.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from .open_work_case_declarations import CaseDeclarationError
from .open_work_dependency_dossier import compose_dependency_status_snapshot

_MAX_CASES = 50
_MAX_CHECKED = 4
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_MAX_FRESHNESS = timedelta(minutes=15)


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not 20 <= len(value) <= 40:
        raise CaseDeclarationError("invalid source evidence clock")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise CaseDeclarationError("invalid source evidence clock") from None
    if parsed.tzinfo is None:
        raise CaseDeclarationError("unscoped source evidence clock")
    return parsed.astimezone(timezone.utc)


def compose_case_evidence_graph(
    admitted: dict[str, Any],
    signature: tuple[tuple[Any, ...], ...],
    selected: list[dict[str, Any]],
    contexts: list[dict[str, Any]],
    *,
    unscanned_prerequisites: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Join declared edges to separately typed source facts and explicit gaps.

    Never infer completion, readiness, customer satisfaction, payment, policy
    or agent causality from a source-open inventory or a missing source record.
    """
    current = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise CaseDeclarationError("invalid evidence evaluation clock")
    current = current.astimezone(timezone.utc)
    if (not isinstance(signature, tuple) or not 1 <= len(signature) <= _MAX_CASES
        or not isinstance(selected, list) or not 1 <= len(selected) <= _MAX_CHECKED
        or not isinstance(contexts, list) or len(selected) != len(contexts)
        or type(unscanned_prerequisites) is not int or unscanned_prerequisites < 0
        or not isinstance(admitted, dict)):
        raise CaseDeclarationError("invalid bounded case evidence graph")

    # The source read and its minimization are delegated to the existing
    # dependency-status projection, so raw provider fields cannot pass through.
    status = compose_dependency_status_snapshot(
        admitted, selected, contexts,
        unscanned_prerequisites=unscanned_prerequisites,
    )
    refs = []
    rows = {}
    for row in signature:
        if (not isinstance(row, tuple) or len(row) != 8
            or not isinstance(row[0], str) or row[0] in rows
            or type(row[1]) is not int or row[1] < 1
            or not isinstance(row[3], str) or not isinstance(row[4], str)
            or not isinstance(row[7], tuple) or len(row[7]) > 8):
            raise CaseDeclarationError("invalid reviewed evidence graph signature")
        refs.append(row[0])
        rows[row[0]] = row
    target = admitted.get("work_item_ref")
    if (target not in rows or refs[-1] != target
        or admitted.get("declared_prerequisite_count") != len(refs) - 1
        or unscanned_prerequisites != len(refs) - len(selected)):
        raise CaseDeclarationError("inconsistent reviewed evidence graph")
    checked = {}
    for item in status["case_statuses"]:
        ref = item["work_item_ref"]
        if (ref not in rows or ref in checked
            or item["owner_declaration_revision"] != rows[ref][1]):
            raise CaseDeclarationError("inconsistent selected case revision")
        checked[ref] = item
    if (len(checked) != len(selected) or target not in checked
        or [item["work_item_ref"] for item in status["case_statuses"]]
           != [item["work_item_ref"] for item in selected]):
        raise CaseDeclarationError("inconsistent selected case ordering")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    counts = {"fresh_source_open": 0, "source_unknown": 0,
              "source_stale": 0, "source_clock_conflict": 0,
              "unscanned": 0}
    seen = set()
    for index, ref in enumerate(refs):
        row = rows[ref]
        deps = row[7]
        if (len(set(deps)) != len(deps) or ref in deps
            or any(dep not in seen for dep in deps)):
            raise CaseDeclarationError("invalid reviewed prerequisite order")
        for dep in deps:
            edges.append({
                "from": dep, "to": ref, "type": "declared_prerequisite",
                "provenance": "explicit_owner_declaration",
                "source_verified": False, "business_obligation_inferred": False,
            })
        seen.add(ref)
        nodes.append({
            "id": ref, "type": "reviewed_case", "work_item_ref": ref,
            "revision": row[1], "goal_code": row[3],
            "expected_outcome_code": row[4],
            "owner_declaration_provenance": "explicit_owner_declaration",
            "source_state": "separately_checked" if ref in checked else "not_scanned",
            "expected_outcome_verified": False,
        })
        expected_id = f"expected:{index}"
        nodes.append({
            "id": expected_id, "type": "declared_expected_outcome",
            "code": row[4], "verified": False,
            "customer_outcome_verified": False,
        })
        edges.append({
            "from": ref, "to": expected_id, "type": "declared_expected_outcome",
            "provenance": "explicit_owner_declaration",
            "source_verified": False, "business_obligation_inferred": False,
        })
        if ref not in checked:
            counts["unscanned"] += 1
            gaps.append({"work_item_ref": ref, "kind": "prerequisite_not_scanned"})
            continue

        evidence = checked[ref]["source_reported_status"]
        raw_state = evidence["state"]
        freshness = "unknown"
        reason = evidence.get("reason")
        verified_at = None
        if raw_state == "open":
            verified = _utc(evidence["verified_at"])
            age = current - verified
            if age < -_MAX_CLOCK_SKEW:
                freshness, reason = "clock_conflict", "source_clock_conflict"
                counts["source_clock_conflict"] += 1
            elif age > _MAX_FRESHNESS:
                freshness, reason = "stale", "source_stale"
                counts["source_stale"] += 1
            else:
                freshness, reason = "fresh", None
                verified_at = verified.isoformat()
                counts["fresh_source_open"] += 1
        elif raw_state == "unknown":
            counts["source_unknown"] += 1
        else:
            raise CaseDeclarationError("conflicting source state")

        source_id = f"source:{index}"
        nodes.append({
            "id": source_id, "type": "provider_status_evidence",
            "provider": "stripe", "state": "open" if freshness == "fresh" else "unknown",
            "freshness": freshness, "verified_at": verified_at,
            "bounded_inventory_complete": (
                evidence["bounded_inventory_complete"] if freshness == "fresh" else False
            ),
            "reason": reason, "customer_outcome_verified": False,
            "operation_verified": False,
        })
        edges.append({
            "from": ref, "to": source_id,
            "type": ("source_reported_open" if freshness == "fresh"
                     else "source_evidence_unavailable"),
            "provenance": ("fresh_explicit_provider_read" if freshness == "fresh"
                           else "unknown_stale_or_conflicted_evidence"),
            "source_verified": freshness == "fresh",
            "business_obligation_inferred": False,
        })
        if freshness != "fresh":
            gaps.append({
                "work_item_ref": ref,
                "kind": ("source_" + freshness),
                "reason": reason,
            })

    # A fresh provider-open observation is still NOT proof of an expected
    # outcome or of the absence/presence of a contractual obligation.
    return {
        "format": "openworkgraph.local-case-outcome-evidence-graph.v1",
        "work_item_ref": target,
        "case_count": len(refs),
        "checked_case_count": len(checked),
        "unscanned_prerequisite_count": unscanned_prerequisites,
        "nodes": nodes, "edges": edges, "evidence_gaps": gaps,
        "source_evidence_counts": counts,
        "edge_provenance_classes": [
            "explicit_owner_declaration", "fresh_explicit_provider_read",
            "unknown_stale_or_conflicted_evidence",
        ],
        "inferred_edge_count": 0,
        "source_checks_sequential_not_atomic": True,
        "source_inventory_globally_complete": False,
        "source_scan_explicitly_requested": True,
        "source_write_performed": False,
        "business_obligation_verified": False,
        "provider_operation_verified": False,
        "expected_outcome_verified": False,
        "customer_outcome_verified": False,
        "agent_caused_operation_verified": False,
        "agent_execution_authorized": False,
        "not_synced": True,
    }


__all__ = ["compose_case_evidence_graph"]
