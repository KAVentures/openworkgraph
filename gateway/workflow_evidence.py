from __future__ import annotations

"""Derived workflow navigation over privacy-hardened Gateway evidence.

The local and Gateway paths intentionally reuse the same deterministic workflow
projection. Gateway data is only the subset the endpoint already opted to sync;
canonical local evidence remains authoritative when richer local evidence exists.
"""

from typing import Any

from server.workflow_evidence import (
    WorkflowEvidenceError,
    build_workflow_evidence,
    list_workflow_evidence_candidates,
)
from .query import workflow_trace


def load_gateway_evidence(
    db: Any,
    *,
    organization_id: str,
    actor_id: str | None,
    since: str | None,
    until: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100_000))
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(rows) < bounded:
        page = workflow_trace(
            db,
            organization_id=organization_id,
            actor_id=actor_id,
            since=since,
            until=until,
            cursor=cursor,
            limit=min(500, bounded - len(rows)),
        )
        rows.extend(item for item in page.get("rows", []) if isinstance(item, dict))
        cursor = str(page.get("next_cursor") or "") or None
        if not cursor:
            break
    return rows


def repeated_workflows(
    db: Any,
    *,
    organization_id: str,
    actor_id: str | None,
    since: str | None = None,
    until: str | None = None,
    source_event_limit: int = 25_000,
    min_runs: int = 2,
    limit: int = 20,
) -> dict[str, Any]:
    raw = load_gateway_evidence(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        since=since,
        until=until,
        limit=source_event_limit,
    )
    result = list_workflow_evidence_candidates(
        since=since,
        until=until,
        source_event_limit=source_event_limit,
        min_runs=min_runs,
        limit=limit,
        _raw_events=raw,
    )
    result["gateway_projection"] = True
    result["source"] = "gateway_synced_privacy_hardened_evidence"
    result["local_evidence_may_be_richer"] = True
    return result


def workflow_evidence(
    db: Any,
    *,
    organization_id: str,
    actor_id: str | None,
    family_key: str = "",
    execution_ids: str = "",
    since: str | None = None,
    until: str | None = None,
    source_event_limit: int = 25_000,
    max_runs: int = 12,
    max_events_per_run: int = 100,
) -> dict[str, Any]:
    raw = load_gateway_evidence(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        since=since,
        until=until,
        limit=source_event_limit,
    )
    result = build_workflow_evidence(
        family_key=family_key,
        execution_ids=execution_ids,
        since=since,
        until=until,
        source_event_limit=source_event_limit,
        max_runs=max_runs,
        max_events_per_run=max_events_per_run,
        include_canonical_evidence=True,
        _raw_events=raw,
    )
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    provenance["source"] = "gateway_synced_privacy_hardened_evidence"
    provenance["canonical_local_store_may_be_richer"] = True
    result["provenance"] = provenance
    result["gateway_projection"] = True
    return result


__all__ = [
    "WorkflowEvidenceError",
    "load_gateway_evidence",
    "repeated_workflows",
    "workflow_evidence",
]
