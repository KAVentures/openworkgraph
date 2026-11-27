"""Exact bipartite feasibility for bounded reviewed correspondence choices.

An allowed edge belongs to at least one left-saturating injection. It is not
an inferred identity or a recommended correspondence.
"""
from collections import deque

MAX_LEFT = 50
MAX_RIGHT = 500


def _reachable(starts, adjacency):
    seen = set(starts)
    queue = deque(seen)
    while queue:
        for node in adjacency[queue.popleft()]:
            if node not in seen:
                seen.add(node)
                queue.append(node)
    return seen


def assess(candidates, fixed=None):
    """Return exact feasible options and a Hall deficiency when impossible.

    ``candidates`` maps left IDs to iterable right IDs. ``fixed`` contains
    explicitly chosen edges. No matching assignment is returned to callers.
    """
    fixed = dict(fixed or {})
    if len(candidates) > MAX_LEFT:
        raise ValueError("left mapping budget exceeded")
    original = {key: sorted(set(values)) for key, values in candidates.items()}
    rights = {r for values in original.values() for r in values}
    if len(rights) > MAX_RIGHT:
        raise ValueError("right mapping budget exceeded")
    if any(k not in original or r not in original[k] for k, r in fixed.items()):
        raise ValueError("fixed mapping outside candidate graph")
    if len(set(fixed.values())) != len(fixed):
        raise ValueError("duplicate fixed mapping")
    reserved = set(fixed.values())
    graph = {k: [r for r in original[k] if r not in reserved]
             for k in sorted(original) if k not in fixed}
    # Augmenting paths find a maximum matching (at most 50 left vertices).
    right_to_left = {}

    def augment(left, seen):
        for right in graph[left]:
            if right in seen:
                continue
            seen.add(right)
            if right not in right_to_left or augment(right_to_left[right], seen):
                right_to_left[right] = left
                return True
        return False

    for left in graph:
        augment(left, set())
    matched = {left: right for right, left in right_to_left.items()}
    size = len(matched) + len(fixed)
    allowed = {k: [fixed[k]] if k in fixed else [] for k in sorted(original)}
    if len(matched) < len(graph):
        # Alternating reachability from unmatched lefts provides a Hall
        # witness: the reachable left set has fewer neighbors than members.
        lefts = set(graph) - set(matched)
        reached = set()
        queue = deque(sorted(lefts))
        while queue:
            left = queue.popleft()
            for right in graph[left]:
                if right in reached:
                    continue
                reached.add(right)
                other = right_to_left.get(right)
                if other is not None and other not in lefts:
                    lefts.add(other)
                    queue.append(other)
        return {"feasible": False, "matched_count": size,
                "required_count": len(original), "allowed": allowed,
                "hall_witness": {"left": sorted(lefts), "neighbors": sorted(reached),
                                 "deficit": len(lefts)-len(reached)}}
    # Contract every matched left-right pair into its matched right vertex.
    # An unmatched edge can be used in a full matching exactly when it lies
    # on an alternating cycle or leads to an unused right vertex.
    available = rights - reserved
    reverse = {r: [] for r in available}
    for left, options in graph.items():
        for right in options:
            reverse[right].append(matched[left])
    to_free = _reachable(available-set(right_to_left), reverse)
    for left, options in graph.items():
        cycle = _reachable([matched[left]], reverse)
        allowed[left] = [r for r in options if r in cycle or r in to_free]
    return {"feasible": True, "matched_count": size,
            "required_count": len(original), "allowed": allowed,
            "hall_witness": None}
