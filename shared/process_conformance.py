"""Bounded structural graph alignment. No policy, condition or outcome inference."""
from __future__ import annotations

from heapq import heappop, heappush
from itertools import count
from typing import Any

from .process_model import ProcessModel


def compare_path(path: tuple[str, ...], model: ProcessModel) -> dict[str, Any]:
    """Minimum unit-cost insert/delete alignment against the graph's label language.

    Start/end/decision nodes are routing nodes, not source-log activities. Rules
    annotate branches but are never evaluated against missing business payloads.
    Cycles are safe because each (log position, model node) state is visited at
    most once at its minimum distance. Input bounds come from the CSV/model schema.
    """
    if len(path) > 5000:
        raise ValueError('Conformance path exceeds the 5,000 event limit')
    nodes = {activity.id: activity for activity in model.activities}
    outgoing: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in model.transitions:
        outgoing[edge.source].append(edge.target)
    done = '__done__'  # Cannot collide with schema-constrained activity IDs.
    for terminal in model.terminal_activity_ids:
        outgoing[terminal] = [done]
    start = (0, model.entry_activity_id)
    distances = {start: 0}
    previous: dict[tuple[int, str], tuple[tuple[int, str], dict[str, Any] | None]] = {}
    serial = count()
    queue = [(0, next(serial), start)]
    prefix_possible = False
    target = (len(path), done)

    def push(state, cost, before, operation):
        if cost < distances.get(state, float('inf')):
            distances[state] = cost
            previous[state] = (before, operation)
            heappush(queue, (cost, next(serial), state))

    while queue:
        cost, _, state = heappop(queue)
        if distances[state] != cost:
            continue
        index, node = state
        if index == len(path) and cost == 0 and node != done:
            prefix_possible = True
        if state == target:
            break
        if index < len(path):
            push((index + 1, node), cost + 1, state, {
                'type': 'log_only', 'log_position': index + 1, 'activity': path[index],
            })
        if node == done:
            continue
        activity = nodes[node]
        for successor in outgoing[node]:
            if activity.kind != 'activity':
                push((index, successor), cost, state, None)
            else:
                if index < len(path) and activity.label == path[index]:
                    push((index + 1, successor), cost, state, {
                        'type': 'matched', 'log_position': index + 1,
                        'activity': path[index], 'model_activity_id': node,
                    })
                push((index, successor), cost + 1, state, {
                    'type': 'model_only', 'model_activity_id': node, 'activity': activity.label,
                })
    operations = []
    cursor = target
    while cursor != start:
        cursor, operation = previous[cursor]
        if operation:
            operations.append(operation)
    operations.reverse()
    return {
        'exact_structural_match': distances[target] == 0,
        'minimum_activity_edit_count': distances[target],
        'could_be_incomplete_log_prefix': distances[target] > 0 and prefix_possible,
        'alignment': operations[:80], 'alignment_truncated': len(operations) > 80,
        'alignment_basis': 'one_minimum_edit_alignment_not_a_business_explanation',
        'business_rules_evaluated': False, 'policy_compliance_verified': False,
        'business_outcome_verified': False,
    }
