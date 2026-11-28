"""Exact directed O2O topology, without inferring temporal or business causality."""
from collections import Counter


def analyze(objects, assertions):
    """Iterative Kosaraju SCCs and condensation counts; no transitive closure."""
    types = dict(objects)
    if len(types) != len(objects):
        raise ValueError("Ambiguous retained object identifiers")
    outgoing = {oid: set() for oid in types}
    incoming = {oid: set() for oid in types}
    seen = set()
    for a, b, q in assertions:
        if a not in types or b not in types or (a, b, q) in seen:
            raise ValueError("Invalid retained assertion population")
        seen.add((a, b, q))
        outgoing[a].add(b)
        incoming[b].add(a)
    visited, finish = set(), []
    for root in sorted(types):
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(outgoing[root]))]
        while stack:
            u, children = stack[-1]
            v = next(children, None)
            if v is None:
                finish.append(u)
                stack.pop()
            elif v not in visited:
                visited.add(v)
                stack.append((v, iter(outgoing[v])))
    owner, groups = {}, {}
    for root in reversed(finish):
        if root in owner:
            continue
        members, frontier = [], [root]
        owner[root] = root
        while frontier:
            u = frontier.pop()
            members.append(u)
            for v in incoming[u]:
                if v not in owner:
                    owner[v] = root
                    frontier.append(v)
        cid = min(members)
        groups[cid] = members
        for u in members:
            owner[u] = cid
    inter, internal = Counter(), Counter()
    literal_internal, literal_external = Counter(), Counter()
    for u, adj in outgoing.items():
        for v in adj:
            a, b = owner[u], owner[v]
            if a == b:
                internal[a] += 1
            else:
                inter[a, b] += 1
    for u, v, _ in assertions:
        a, b = owner[u], owner[v]
        if a == b:
            literal_internal[a] += 1
        else:
            literal_external[a] += 1
    predecessors, successors = Counter(), Counter()
    for a, b in inter:
        successors[a] += 1
        predecessors[b] += 1
    components, members = [], []
    for cid, group in sorted(groups.items()):
        loops = sum(u in outgoing[u] for u in group)
        cyclic = len(group) > 1 or loops > 0
        components.append({"component_id": cid, "objects": len(group),
                           "object_types": len({types[u] for u in group}),
                           "cyclic": cyclic, "self_loop_objects": loops,
                           "internal_directed_pairs": internal[cid],
                           "internal_literal_assertions": literal_internal[cid],
                           "outgoing_literal_assertions": literal_external[cid],
                           "predecessor_components": predecessors[cid],
                           "successor_components": successors[cid],
                           "source_component": predecessors[cid] == 0,
                           "sink_component": successors[cid] == 0})
        for u in sorted(group):
            members.append({"component_id": cid, "object_id": u, "object_type": types[u],
                            "component_objects": len(group), "cyclic_component": cyclic,
                            "incoming_neighbors": len(incoming[u] - {u}),
                            "outgoing_neighbors": len(outgoing[u] - {u}),
                            "self_loop": u in outgoing[u]})
    if len(members) != len(types) or sum(literal_internal.values()) + sum(literal_external.values()) != len(assertions):
        raise ValueError("Directed topology population accounting failed")
    return {"counts": {"objects": len(types), "literal_assertions": len(assertions),
                       "directed_pairs_including_self": sum(map(len, outgoing.values())),
                       "strong_components": len(components),
                       "cyclic_components": sum(c["cyclic"] for c in components),
                       "objects_in_cyclic_components": sum(c["objects"] for c in components if c["cyclic"]),
                       "self_loop_objects": sum(c["self_loop_objects"] for c in components),
                       "source_components": sum(c["source_component"] for c in components),
                       "sink_components": sum(c["sink_component"] for c in components),
                       "condensation_edges": len(inter),
                       "largest_strong_component_objects": max((c["objects"] for c in components), default=0)},
            "directed_components": sorted(components, key=lambda c: (-c["objects"], c["component_id"])),
            "directed_members": members}
