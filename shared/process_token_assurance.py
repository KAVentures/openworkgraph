"""Bounded design assurance using OWG's existing edge-token semantics.

Explores structural possibilities, never evaluates rules or executes tools.
Every positive witness contains a complete token schedule when one was found.
Negative conclusions require a complete finite marking exploration.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .process_model import ProcessModel
from .process_parallel import EdgeTokenModel

MAX_STATES = 10_000
MAX_FIRINGS = 100_000
MAX_WITNESS_STEPS = 80
MAX_EXAMPLES = 12


def _path(parent, state):
    result = []
    while state in parent:
        state, node, edges = parent[state]
        result.append((node, edges))
    return list(reversed(result))


def _suffix(successor, state):
    result = []
    while state in successor:
        state, node, edges = successor[state]
        result.append((node, edges))
    return result


def _witness(path):
    return {"node_ids": [node for node, _ in path[:MAX_WITNESS_STEPS]],
            "transition_indices": sorted({edge for _, edges in path for edge in edges}),
            "firing_count": len(path), "truncated": len(path) > MAX_WITNESS_STEPS}


def _cyclic_states(adjacency, reverse, states):
    """Kosaraju SCCs without recursion; mark precisely states on directed cycles."""
    seen, order = set(), []
    for root in states:
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, iter(adjacency[root]))]
        while stack:
            node, children = stack[-1]
            try:
                child = next(children)
            except StopIteration:
                order.append(node)
                stack.pop()
                continue
            if child not in seen:
                seen.add(child)
                stack.append((child, iter(adjacency[child])))
    seen.clear()
    components = []
    for root in reversed(order):
        if root in seen:
            continue
        component = {root}
        seen.add(root)
        pending = [root]
        while pending:
            for predecessor in reverse[pending.pop()]:
                if predecessor not in seen:
                    seen.add(predecessor)
                    component.add(predecessor)
                    pending.append(predecessor)
        if len(component) > 1 or root in adjacency[root]:
            components.append(component)
    return components


def analyze(model: ProcessModel, *, max_states: int = MAX_STATES,
            max_firings: int = MAX_FIRINGS) -> dict[str, Any]:
    """Explore markings by BFS, retaining concrete shortest-prefix/suffix witnesses.

    A state or firing budget never becomes a negative reachability verdict.
    Rules are structurally possible, not evaluated facts; inclusive selections
    are the existing engine's nonempty subsets, not predicted decisions.
    """
    if type(max_states) is not int or not 1 <= max_states <= MAX_STATES:
        raise ValueError("invalid marking state budget")
    if type(max_firings) is not int or not 1 <= max_firings <= MAX_FIRINGS:
        raise ValueError("invalid firing budget")
    automaton = EdgeTokenModel(model)
    initial, done = automaton.initial, automaton.done
    # Compact integer indices make graph analysis deterministic and bounded.
    markings = [initial]
    ids = {initial: 0}
    parent = {}
    pending = deque([0])
    adjacency, reverse = defaultdict(set), defaultdict(set)
    incoming_arcs = defaultdict(list)
    arcs = []
    deadlocks = []
    firing_count = 0
    limit = None
    while pending and limit is None:
        source = pending.popleft()
        marking = markings[source]
        enabled = False
        for node, following, emitted in automaton.firings(marking):
            enabled = True
            if firing_count >= max_firings:
                limit = "firing_budget"
                break
            firing_count += 1
            if following not in ids:
                if len(markings) >= max_states:
                    limit = "marking_budget"
                    break
                target = len(markings)
                ids[following] = target
                markings.append(following)
                pending.append(target)
                parent[target] = (source, node.id, tuple(sorted(p for p in emitted if p >= 0)))
            target = ids[following]
            edges = tuple(sorted(p for p in emitted if p >= 0))
            arc = (source, target, node.id, edges)
            arcs.append(arc)
            adjacency[source].add(target)
            reverse[target].add(source)
            incoming_arcs[target].append(arc)
        if not enabled and marking != done and limit is None:
            deadlocks.append(source)
    complete = limit is None
    # Reverse BFS from sole completion marking gives shortest known suffixes.
    completion = ids.get(done)
    can_complete = set()
    successor = {}
    if completion is not None:
        can_complete.add(completion)
        queue = deque([completion])
        while queue:
            target = queue.popleft()
            for source, _, node, edges in incoming_arcs[target]:
                if source not in can_complete:
                    can_complete.add(source)
                    successor[source] = (target, node, edges)
                    queue.append(source)

    # Choose the shortest known completed schedule for each node and edge.
    # Predecessor/successor trees never carry an unbounded history per state.
    depth = {0: 0}
    for state in range(1, len(markings)):
        depth[state] = depth[parent[state][0]] + 1
    distance = {}
    if completion is not None:
        distance[completion] = 0
        for source, (target, _, _) in successor.items():
            distance[source] = distance[target] + 1
    node_best, edge_best = {}, {}
    node_seen, edge_seen = set(), set()
    for i, (source, target, node, edges) in enumerate(arcs):
        node_seen.add(node)
        edge_seen.update(edges)
        if target not in can_complete:
            continue
        score = (depth[source] + 1 + distance[target], i)
        if node not in node_best or score < node_best[node]:
            node_best[node] = score
        for edge in edges:
            if edge not in edge_best or score < edge_best[edge]:
                edge_best[edge] = score

    def row(best, seen, key):
        if key in best:
            source, target, node, edges = arcs[best[key][1]]
            route = _path(parent, source) + [(node, edges)] + _suffix(successor, target)
            return {"status": "complete_witness", "witness": _witness(route)}
        return {"status": ("no_complete_witness" if complete else "unknown_budget"),
                "observed_firing": key in seen, "witness": None}

    nodes = [{"activity_id": node.id, "kind": node.kind,
              **row(node_best, node_seen, node.id)} for node in model.activities]
    transitions = [{"index": i, "source": edge.source, "target": edge.target,
                    "rule_id": edge.rule_id, **row(edge_best, edge_seen, i)}
                   for i, edge in enumerate(model.transitions)]
    terminal_rows = [item for item in nodes if item["activity_id"] in model.terminal_activity_ids]
    components = _cyclic_states(adjacency, reverse, range(len(markings)))
    loop_examples = []
    for component in sorted(components, key=min)[:MAX_EXAMPLES]:
        # A concrete cycle: pick a state and one internal edge, then BFS back.
        root = min(component)
        first = next(a for a in arcs if a[0] == root and a[1] in component)
        _, next_state, first_node, first_edges = first
        q = deque([next_state])
        cycle_parent = {}
        visited = {next_state}
        while q and root not in visited:
            u = q.popleft()
            for v in sorted(adjacency[u]):
                if v in component and v not in visited:
                    visited.add(v)
                    # Arc labels may differ for the same two states: retain one actual firing.
                    a = next(a for a in incoming_arcs[v] if a[0] == u)
                    cycle_parent[v] = (u, a[2], a[3])
                    q.append(v)
        cycle = [(first_node, first_edges)] + _path(cycle_parent, root)
        loop_examples.append({"prefix": _witness(_path(parent, root)),
                              "cycle": _witness(cycle),
                              "known_completion_from_cycle": root in can_complete})
    return {
        "format": "openworkgraph.token-flow-assurance.v1",
        "semantics": "structural_edge_token_possibilities",
        "complete": complete, "incomplete_reason": limit,
        "states_discovered": len(markings), "firings_examined": firing_count,
        "state_budget": max_states, "firing_budget": max_firings,
        "completion_reachable": True if completion is not None else (False if complete else None),
        "every_reachable_marking_can_complete": (len(can_complete) == len(markings)) if complete else None,
        "known_deadlock_count": len(deadlocks),
        "deadlock_count_exact": complete,
        "deadlock_examples": [{"prefix": _witness(_path(parent, state))}
                              for state in deadlocks[:MAX_EXAMPLES]],
        "noncompletable_marking_count": len(markings) - len(can_complete) if complete else None,
        "known_cyclic_component_count": len(components),
        "cyclic_component_count_exact": complete,
        "cycle_examples": loop_examples,
        "activities": nodes, "transitions": transitions, "terminals": terminal_rows,
        "activities_with_complete_witness": len(node_best),
        "transitions_with_complete_witness": len(edge_best),
        "shortest_completion": _witness(_path(parent, completion)) if completion is not None else None,
        "business_rules_evaluated": False, "policy_compliance_verified": False,
        "execution_authorized": False, "outcomes_verified": False,
    }


def analyze_saved(packet: dict[str, Any], **budgets) -> dict[str, Any]:
    model = ProcessModel.model_validate(packet["process_model"])
    return {"reference": {k: packet[k] for k in ("workflow_id", "revision", "content_sha256")},
            **analyze(model, **budgets)}
