"""Exact weak connectivity and single-removal impact of a supplied O2O graph.

This is graph mathematics on a static undirected projection, not evidence
of causality, temporal validity, operational failure or real case identity.
"""
from collections import Counter, defaultdict


def analyze(objects, assertions):
    """Iterative low-link DFS; linear traversal, no recursion depth dependency."""
    types = dict(objects)
    if len(types) != len(objects):
        raise ValueError("Ambiguous retained object identifiers")
    neighbors = {oid: set() for oid in types}
    pairs = Counter()
    directions = Counter()
    loops = Counter()
    seen = set()
    for a, b, q in assertions:
        if a not in types or b not in types or (a, b, q) in seen:
            raise ValueError("Invalid retained assertion population")
        seen.add((a, b, q))
        if a == b:
            loops[a] += 1
            continue
        pair = tuple(sorted((a, b)))
        pairs[pair] += 1
        directions[(a, b)] += 1
        neighbors[a].add(b)
        neighbors[b].add(a)
    order = {oid: sorted(adj) for oid, adj in neighbors.items()}
    discovery, low, size, parent = {}, {}, {}, {}
    components, cuts, bridges = [], [], []
    for root in sorted(types):
        if root in discovery:
            continue
        discovery[root] = low[root] = len(discovery)
        size[root] = 1
        parent[root] = None
        members = [root]
        separated = defaultdict(list)
        bridge_children = []
        stack = [(root, iter(order[root]))]
        while stack:
            u, children = stack[-1]
            v = next(children, None)
            if v is not None:
                if v == parent[u]:
                    continue
                if v in discovery:
                    low[u] = min(low[u], discovery[v])
                    continue
                parent[v] = u
                discovery[v] = low[v] = len(discovery)
                size[v] = 1
                members.append(v)
                stack.append((v, iter(order[v])))
                continue
            stack.pop()
            p = parent[u]
            if p is not None:
                size[p] += size[u]
                low[p] = min(low[p], low[u])
                if low[u] >= discovery[p]:
                    separated[p].append(size[u])
                if low[u] > discovery[p]:
                    bridge_children.append(u)
        n = len(members)
        component_cut_count = 0
        for u, parts in separated.items():
            rest = n - 1 - sum(parts)
            if rest:
                parts.append(rest)
            if len(parts) < 2:
                continue
            component_cut_count += 1
            lost = ((n - 1)**2 - sum(x*x for x in parts)) // 2
            cuts.append({"object_id": u, "object_type": types[u], "component_id": root,
                         "component_objects": n, "remaining_components": len(parts),
                         "part_size_counts": [{"objects": k, "count": v} for k,v in sorted(Counter(parts).items())],
                         "disconnected_surviving_pairs": lost, "degree": len(neighbors[u])})
        for child in bridge_children:
            a,b = sorted((parent[child], child))
            s = size[child]
            bridges.append({"left_id": a, "right_id": b, "left_type": types[a], "right_type": types[b],
                            "component_id": root, "component_objects": n,
                            "part_sizes": sorted((s,n-s)), "disconnected_pairs": s*(n-s),
                            "assertions_in_pair": pairs[(a,b)], "left_to_right": directions[(a,b)],
                            "right_to_left": directions[(b,a)], "single_assertion_bridge": pairs[(a,b)] == 1})
        edges = sum(len(neighbors[u]) for u in members)//2
        literal = sum(loops[u] for u in members) + sum(
            pairs[(u,v)] for u in members for v in neighbors[u] if u<v)
        components.append({"component_id": root, "objects": n, "projected_pairs": edges,
                           "literal_assertions": literal, "self_assertions": sum(loops[u] for u in members),
                           "articulation_objects": component_cut_count, "bridge_pairs": len(bridge_children)})
    if sum(c["objects"] for c in components) != len(types) or sum(c["literal_assertions"] for c in components) != len(assertions):
        raise ValueError("Topology population accounting failed")
    return {"counts": {"objects": len(types), "literal_assertions": len(assertions),
                       "self_assertions": sum(loops.values()), "projected_pairs": len(pairs),
                       "weak_components": len(components), "singleton_components": sum(c["objects"]==1 for c in components),
                       "largest_component_objects": max((c["objects"] for c in components),default=0),
                       "articulation_objects": len(cuts), "bridge_pairs": len(bridges),
                       "single_assertion_bridges": sum(b["single_assertion_bridge"] for b in bridges)},
            "components": sorted(components,key=lambda c:(-c["objects"],c["component_id"])),
            "objects": sorted(cuts,key=lambda c:(-c["disconnected_surviving_pairs"],c["object_id"])),
            "bridges": sorted(bridges,key=lambda b:(-b["disconnected_pairs"],b["left_id"],b["right_id"]))}
