from __future__ import annotations

"""Derived workflow navigation over privacy-hardened Gateway evidence.

The local and Gateway paths intentionally reuse the same deterministic workflow
projection. Gateway data is only the subset the endpoint already opted to sync;
canonical local evidence remains authoritative when richer local evidence exists.
"""

from typing import Any

from server.workflow_evidence import (
    WorkflowEvidenceError,
    LOW_SPECIFICITY_QUALITY_FLOOR,
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
    include_low_specificity: bool = True,
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
        min_cluster_quality=0.0 if include_low_specificity else LOW_SPECIFICITY_QUALITY_FLOOR,
        # Preserve cross-app sequences: host changes do not prove task changes.
        partition_host_switches=False,
        partition_idle_gaps=True,
        rank_by_quality=True,
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
    def _build(*, mode: str) -> dict[str, Any]:
        return build_workflow_evidence(
            family_key=family_key,
            execution_ids=execution_ids,
            since=since,
            until=until,
            source_event_limit=source_event_limit,
            max_runs=max_runs,
            max_events_per_run=max_events_per_run,
            include_canonical_evidence=True,
            partition_host_switches=mode == "host",
            partition_idle_gaps=mode == "idle",
            _raw_events=raw,
        )

    # New discovery issues idle-partitioned IDs. Preserve older host-partition
    # and original unsplit IDs so stored references do not silently disappear.
    # All three modes operate on identical actor-scoped, bounded evidence.
    partition_mode = "idle"
    for candidate_mode in ("idle", "host", "unsplit"):
        try:
            result = _build(mode=candidate_mode)
            partition_mode = candidate_mode
            break
        except WorkflowEvidenceError as exc:
            recoverable = (
                (execution_ids or family_key) and
                ("unavailable in this bounded" in str(exc)
                 or "workflow family was not found" in str(exc))
            )
            if not recoverable or candidate_mode == "unsplit":
                raise
    legacy_reference_used = partition_mode == "unsplit"
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    provenance["source"] = "gateway_synced_privacy_hardened_evidence"
    provenance["canonical_local_store_may_be_richer"] = True
    provenance["legacy_unsplit_execution_reference_used"] = legacy_reference_used
    provenance["legacy_host_partition_reference_used"] = partition_mode == "host"
    provenance["execution_partition_mode"] = partition_mode
    result["provenance"] = provenance
    result["gateway_projection"] = True
    return result


__all__ = [
    "WorkflowEvidenceError",
    "load_gateway_evidence",
    "repeated_workflows",
    "workflow_evidence",
]
