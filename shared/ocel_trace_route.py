"""Explain shortest routes in a bounded directed assertion trace, not inferred joins."""
from collections import defaultdict


def explain(nodes, edges, source, target, direction, truncated, requested_hops):
    distances = {n["object_id"]: n["hop"] for n in nodes}
    if target not in distances:
        return {"target_id": target, "status": "unknown_due_to_limit" if truncated else "not_reached_within_depth",
                "shortest_hops": None, "shortest_assertion_routes": None,
                "route_count_complete": False if truncated else True, "example_route": []}
    # A shortest route advances exactly one BFS layer per assertion.
    # Different qualifiers and opposite directed assertions are distinct.
    incoming = defaultdict(list)
    for edge in edges:
        a, b = edge["source_id"], edge["target_id"]
        for start, end, traversal in (
            ([(a, b, "forward")] if direction != "incoming" else [])
            + ([(b, a, "reverse")] if direction != "outgoing" else [])
        ):
            if distances[end] == distances[start] + 1:
                incoming[end].append((start, traversal, edge))
    counts, routes = {source: 1}, {source: []}
    for node in sorted(distances, key=lambda n: (distances[n], n)):
        if node == source:
            continue
        options = sorted(incoming[node], key=lambda x: (x[0], x[2]["source_id"],
                                                        x[2]["target_id"], x[2]["qualifier"], x[1]))
        counts[node] = sum(counts[start] for start, _, _ in options)
        start, traversal, edge = options[0]
        routes[node] = routes[start] + [{"from_id": start, "to_id": node,
            "traversal": traversal, "source_id": edge["source_id"],
            "target_id": edge["target_id"], "qualifier": edge["qualifier"]}]
    return {"target_id": target, "status": "reached", "shortest_hops": distances[target],
            "shortest_assertion_routes": counts[target],
            "route_count_complete": not truncated or target == source,
            "example_route": routes[target]}
