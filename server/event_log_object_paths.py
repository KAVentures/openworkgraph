"""Bounded two/three-hop inspection of retained caller-supplied OCEL O2O assertions.

The output is a navigational subgraph, NEVER inferred business-case membership,
a source-authoritative identity join, a validity-time claim, or automation.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from shared.event_log_formats import _exact_key
from . import event_log_object_relationships as relationships
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.ocel-object-relationship-trace.v1"
MAX_HOPS = 3
MAX_NODES = 100
MAX_EDGES = 200


class TraceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    object_id: str = Field(min_length=1, max_length=200)
    direction: Literal["both", "incoming", "outgoing"] = "both"
    max_hops: StrictInt = Field(default=2, ge=1, le=MAX_HOPS)
    max_nodes: StrictInt = Field(default=60, ge=2, le=MAX_NODES)
    max_edges: StrictInt = Field(default=100, ge=1, le=MAX_EDGES)
    qualifier: str | None = Field(default=None, max_length=80)

    def model_post_init(self, __context: Any) -> None:
        _exact_key(self.object_id)
        if self.qualifier is not None:
            _exact_key(self.qualifier, 80)


def _neighbors(conn, log_id: str, frontier: list[str], direction: str,
               qualifier: str | None, limit: int):
    """Fetch one ordered bounded page of raw graph links touching the frontier."""
    placeholders = ",".join("?" for _ in frontier)
    scopes, params = [], [log_id]
    if direction in ("outgoing", "both"):
        scopes.append("r.source_object_id IN (" + placeholders + ")")
        params.extend(frontier)
    if direction in ("incoming", "both"):
        scopes.append("r.target_object_id IN (" + placeholders + ")")
        params.extend(frontier)
    condition = " OR ".join(scopes)
    where = " AND r.qualifier=?" if qualifier is not None else ""
    if qualifier is not None:
        params.append(qualifier)
    return conn.execute(
        """SELECT r.source_object_id,r.target_object_id,r.qualifier,
                  s.object_type,t.object_type
           FROM object_relationships r
           JOIN objects s ON s.log_id=r.log_id AND s.object_id=r.source_object_id
           JOIN objects t ON t.log_id=r.log_id AND t.object_id=r.target_object_id
           WHERE r.log_id=? AND (""" + condition + ")" + where +
        " ORDER BY r.source_object_id,r.target_object_id,r.qualifier LIMIT ?",
        [*params, limit],
    ).fetchall()


def trace(log_id: str, spec: Any) -> dict[str, Any]:
    try:
        settings = TraceSpec.model_validate(spec)
    except (ValidationError, ValueError):
        raise workspace.EventLogWorkspaceError(
            "Trace requires an existing literal object ID, direction, 1–3 hops, "
            "2–100 nodes and 1–200 links"
        ) from None

    with relationships._read_log(log_id) as (conn, info):
        original = conn.execute(
            "SELECT object_type FROM objects WHERE log_id=? AND object_id=?",
            [log_id, settings.object_id],
        ).fetchone()
        if original is None:
            raise workspace.EventLogWorkspaceError("The selected object was not found in this log")
        total_assertions = relationships.count(conn, log_id)
        revision = relationships._revision(conn, log_id)
        seen_nodes: dict[str, dict[str, Any]] = {
            settings.object_id: {
                "object_id": settings.object_id, "object_type": original[0], "hop": 0,
            }
        }
        seen_edges: set[tuple[str, str, str]] = set()
        edges: list[dict[str, Any]] = []
        frontier = [settings.object_id]
        truncated_reason = None
        hops_visited = 0

        for hop in range(1, settings.max_hops + 1):
            if not frontier:
                break
            available = settings.max_edges - len(edges)
            if available <= 0:
                truncated_reason = "max_edges_reached"
                break
            candidates = _neighbors(conn, log_id, sorted(frontier), settings.direction,
                                    settings.qualifier, available + 1)
            exceeded = len(candidates) > available
            next_frontier: list[str] = []
            for source, target, qualifier, source_type, target_type in candidates[:available]:
                edge_key = source, target, qualifier
                if edge_key in seen_edges:
                    continue
                first, last = ((source, source_type), (target, target_type))
                for node_id, kind in (first, last):
                    if node_id not in seen_nodes and len(seen_nodes) >= settings.max_nodes:
                        truncated_reason = "max_nodes_reached"
                        break
                    if node_id not in seen_nodes:
                        seen_nodes[node_id] = {"object_id": node_id, "object_type": kind, "hop": hop}
                        next_frontier.append(node_id)
                if truncated_reason:
                    break
                seen_edges.add(edge_key)
                edges.append({
                    "source_id": source, "target_id": target,
                    "source_type": source_type, "target_type": target_type,
                    "qualifier": qualifier, "encountered_at_hop": hop,
                })
            hops_visited = hop
            if truncated_reason:
                break
            if exceeded:
                truncated_reason = "edge_scan_limit_reached"
                break
            frontier = next_frontier
        nodes = sorted(seen_nodes.values(), key=lambda item: (item["hop"], item["object_id"]))
    return {
        "format": FORMAT,
        "log": info,
        "relationship_revision": revision,
        "revision_basis": "local_log_append_batch",
        "selected_object": {
            "object_id": settings.object_id, "object_type": original[0],
        },
        "direction": settings.direction,
        "qualifier": settings.qualifier,
        "requested_hops": settings.max_hops,
        "hops_visited": hops_visited,
        "retained_assertions_in_log": int(total_assertions),
        "nodes": nodes,
        "edges": edges,
        "shown_nodes": len(nodes),
        "shown_edges": len(edges),
        "truncated": truncated_reason is not None,
        "truncation_reason": truncated_reason,
        "all_requested_hops_fully_traversed": truncated_reason is None,
        "scope": "selected_saved_log_caller_supplied_static_object_assertions",
        "independently_verified": False,
        "case_membership_inferred": False,
        "shared_event_correlation_performed": False,
        "historical_validity_known": False,
        "causal_claims": False,
        "source_reads_performed": False,
        "execution_authorized": False,
        "interpretation": (
            "Only bounded stored directed object-to-object assertions were traversed. "
            "Connections are not proof of case membership, valid-time relations, "
            "source identity, causal handoff or a complete business process."
        ),
    }


__all__ = ["FORMAT", "MAX_HOPS", "MAX_NODES", "MAX_EDGES", "trace"]
