"""Exact cycle membership and downstream reachability in a retained dependency graph."""
from collections import deque


def cycle_inventory(dependencies):
    """Iterative Kosaraju, followed by reverse reachability from cyclic SCCs.

    Edges point from consumer to unfinished retained prerequisite. Each
    strongly connected component is either a genuine cycle (including
    self-loops) or a single acyclic vertex. State blockers do not mask cycles.
    """
    reverse = {node: [] for node in dependencies}
    for node, prerequisites in dependencies.items():
        for prerequisite in prerequisites:
            reverse[prerequisite].append(node)
    visited, finished = set(), []
    for root in sorted(dependencies):
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(dependencies[root]))]
        while stack:
            node, neighbors = stack[-1]
            child = next(neighbors, None)
            if child is None:
                finished.append(node)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(dependencies[child])))
    assigned, groups = set(), []
    for root in reversed(finished):
        if root in assigned:
            continue
        members = []
        stack = [root]
        assigned.add(root)
        while stack:
            node = stack.pop()
            members.append(node)
            for consumer in reverse[node]:
                if consumer not in assigned:
                    assigned.add(consumer)
                    stack.append(consumer)
        if len(members) > 1 or root in dependencies[root]:
            groups.append(sorted(members))
    groups.sort()
    membership = {node: index + 1 for index, group in enumerate(groups) for node in group}
    affected = set(membership)
    pending = deque(sorted(affected))
    while pending:
        for consumer in reverse[pending.popleft()]:
            if consumer not in affected:
                affected.add(consumer)
                pending.append(consumer)
    return groups, membership, affected - membership.keys()
