from __future__ import annotations

"""Tenant-scoped, content-minimal record export over already-synced Gateway data.

No source credentials, source adapters, inference-based identity matching or
new storage. This view is deliberately distinct from a source-verified outcome.
The Gateway never resolves employees' device-specific opaque references across
devices or calls a local user's GitHub/Jira credentials.
"""
import re
from typing import Any

from server.record_graph import record_graph
from .workflow_evidence import load_gateway_evidence

_RECORD = re.compile(r"^owg:r:[0-9a-f]{32}$")


def organization_record_export(
    db: Any, *,
    organization_id: str, resource_ref: str,
    actor_id: str | None = None,
    since: str | None = None, until: str | None = None,
    source_event_limit: int = 5_000,
) -> dict[str, Any]:
    if not _RECORD.fullmatch(str(resource_ref or "")):
        raise ValueError("an observed normalized opaque resource_ref is required")
    if not str(organization_id or "").strip():
        raise ValueError("organization identity is required")
    limit = max(1, min(int(source_event_limit), 25_000))
    # Gateway query enforces the org filter and re-applies retention policy
    # to every page, including when someone changes policy during pagination.
    # Integration actor restrictions are enforced in the calling route.
    rows = load_gateway_evidence(
        db, organization_id=organization_id,
        actor_id=actor_id, since=since, until=until, limit=limit,
    )
    result = record_graph(
        resource_ref, since=since, until=until,
        include_source_status=False, source_event_limit=limit,
        _raw_events=rows,
    )
    result.update({
        "gateway_projection": True,
        "source_evidence": {
            "availability": "unknown",
            "reason": "gateway_has_no_authorized_source_adapter",
            "source_state_reported": False,
            "verified_business_outcome": False,
            "not_a_claim_of_business_success": True,
        },
        "scope": {
            "tenant_enforced_by_authenticated_gateway_token": True,
            "actor_restriction_applied": bool(actor_id),
            "retention_enforced_by_gateway_query": True,
            "limited_to_synced_evidence": True,
        },
        "portable_export": {
            "schema": "openworkgraph.record-graph.v1",
            "format": "json",
            "read_only": True,
            "source_of_truth_outcomes_not_included": True,
            "native_record_identifiers_included": False,
            "use_authorized_live_apis_for_missing_fields": True,
        },
        "cross_device_same_business_record_joined": False,
    })
    # A complete return of the scanning limit is still only a bounded subset.
    result["source_event_limit_reached"] = len(rows) >= limit
    return result


__all__ = ["organization_record_export"]
