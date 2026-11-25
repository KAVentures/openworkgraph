"""Reassessment worklist over one resolved, complete declared dependency DAG.

Incoming stale links belong to the consumer that declared them. A prerequisite
with its own current declaration is not made stale by somebody else's old pin.
This is a read-only review order, not an execution plan.
"""
from collections import Counter, defaultdict


def _matches(node, pin):
    return (node["available"] and node.get("current") is True
            and node.get("revision") == pin["revision"]
            and node.get("content_sha256") == pin["content_sha256"])


def reassessment(graph, items):
    nodes = {node["workflow_id"]: node for node in graph["nodes"]}
    edges = defaultdict(list)
    for edge in graph["edges"]:
        edges[edge["consumer"]].append(edge)
    rows, causes, levels = {}, {}, {}
    for wid in graph["prerequisite_first_order"]:
        node = nodes[wid]
        declaration = items.get(wid)
        direct = []
        if not node["available"]:
            direct.append("process_unavailable")
        elif not node.get("current"):
            direct.append("process_not_active_current")
        elif not node.get("active_latest_independently_reviewed_model"):
            direct.append("current_model_review_missing")
        if declaration is None:
            direct.append("dependency_declaration_missing")
        elif not _matches(node, declaration["dependencies"]["root_process"]):
            direct.append("root_pin_not_current")
        stale_links = []
        for edge in edges[wid]:
            if not _matches(nodes[edge["prerequisite"]], edge["prerequisite_process"]):
                stale_links.append({"link_id": edge["link_id"], "prerequisite": edge["prerequisite"]})
        if stale_links:
            direct.append("prerequisite_pins_not_current")
        prerequisites = sorted({edge["prerequisite"] for edge in edges[wid]})
        upstream = set().union(*(causes[p] for p in prerequisites))
        inherited = [p for p in prerequisites if causes[p]]
        causes[wid] = upstream | ({wid} if direct else set())
        levels[wid] = 1 + max((levels[p] for p in prerequisites), default=0)
        rows[wid] = {
            "workflow_id": wid, "dependency_layer": levels[wid],
            "declaration_revision": declaration["revision"] if declaration else None,
            "status": ("direct_reassessment_required" if direct else
                       "upstream_reassessment_required" if upstream else "current_declared_basis"),
            "direct_findings": direct, "stale_prerequisite_links": stale_links,
            "upstream_blocking_prerequisite_ids": inherited,
            "distinct_upstream_processes_with_direct_findings": len(upstream),
            "direct_prerequisite_count": len(prerequisites),
            "requires_reassessment": bool(causes[wid]),
        }
    ordered = sorted(rows.values(), key=lambda row: (row["dependency_layer"], row["workflow_id"]))
    counts = Counter(row["status"] for row in ordered)
    return {
        "format": "openworkgraph.dependency-reassessment-worklist.v1",
        "processes": ordered,
        "process_count": len(ordered),
        "status_counts": dict(sorted(counts.items())),
        "review_needed_count": sum(row["requires_reassessment"] for row in ordered),
        "all_declared_processes_current": bool(ordered) and not any(row["requires_reassessment"] for row in ordered),
        "review_order": "prerequisite_first_layers_not_execution_schedule",
        "business_dependency_completeness_verified": False,
        "model_reviews_propagated": False,
        "execution_authorized": False,
    }
