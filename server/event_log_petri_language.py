"""Exact visible completed-trace comparison for bounded ordinary PNML models.

Build finite marking graphs, then compare their epsilon-NFA languages with
on-demand subset construction. Silent transitions consume no visible label.
Only exact final markings accept. No finite sample is treated as equivalence.
"""
from __future__ import annotations

from collections import deque
from hashlib import sha256

from . import event_log_petri_soundness as soundness
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.petri_language_comparison.v1"
MAX_PRODUCT_STATES = 3000
MAX_PRODUCT_WORK = 500_000
MAX_WITNESS_LABELS = 100
MAX_LABEL_LENGTH = 200


def _graph(data):
    net = soundness._strict_net(data)
    places = sorted(net["places"])
    transitions = sorted(net["transitions"])
    if len(places) > soundness.MAX_PLACES or len(transitions) > soundness.MAX_TRANSITIONS:
        raise EventLogWorkspaceError("net exceeds the supported model comparison dimensions")
    labels = {t: net["transitions"][t] for t in transitions}
    if any(v is not None and len(v) > MAX_LABEL_LENGTH for v in labels.values()):
        raise EventLogWorkspaceError("visible transition labels must be at most 200 characters")
    pos = {p: i for i, p in enumerate(places)}
    start = tuple(net["initial"][p] for p in places)
    final = tuple(net["final"][p] for p in places)
    if max(start + final) > soundness.MAX_TOKENS:
        raise EventLogWorkspaceError("initial or final marking exceeds the token limit")
    pre = [[(pos[p], n) for p, n in net["pre"][t].items()] for t in transitions]
    post = [[(pos[p], n) for p, n in net["post"][t].items()] for t in transitions]
    states = [start]
    ids = {start: 0}
    edges = [{}]
    checks = 0
    limit = None
    cursor = 0
    while cursor < len(states):
        marking = states[cursor]
        for i, t in enumerate(transitions):
            if checks >= soundness.MAX_CHECKS:
                limit = "transition_check_limit"
                break
            checks += 1
            if not all(marking[p] >= n for p, n in pre[i]):
                continue
            nxt = list(marking)
            for p, n in pre[i]:
                nxt[p] -= n
            for p, n in post[i]:
                nxt[p] += n
            if max(nxt) > soundness.MAX_TOKENS:
                limit = "token_limit"
                break
            key = tuple(nxt)
            target = ids.get(key)
            if target is None:
                if len(states) >= soundness.MAX_STATES:
                    limit = "state_limit"
                    break
                target = len(states)
                ids[key] = target
                states.append(key)
                edges.append({})
            edges[cursor].setdefault(labels[t], set()).add(target)
        if limit is not None:
            break
        cursor += 1
    return {"edges": edges, "final": ids.get(final), "complete": limit is None,
            "limit": limit, "reachable_markings": len(states), "transition_checks": checks,
            "pnml_sha256": sha256(data).hexdigest()}


class _BudgetExceeded(Exception):
    pass


class _Budget:
    def __init__(self):
        self.work = 0

    def spend(self, n=1):
        if self.work + n > MAX_PRODUCT_WORK:
            raise _BudgetExceeded()
        self.work += n


def _closure(graph, seeds, budget):
    seen = set(seeds)
    pending = list(seen)
    while pending:
        node = pending.pop()
        budget.spend()
        for target in graph["edges"][node].get(None, ()):
            budget.spend()
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return frozenset(seen)


def _moves(graph, states, budget):
    destinations = {}
    for node in states:
        budget.spend()
        for label, targets in graph["edges"][node].items():
            budget.spend(1 + len(targets))
            if label is not None:
                destinations.setdefault(label, set()).update(targets)
    return destinations


def compare(baseline: bytes, candidate: bytes) -> dict:
    return _compare_graphs(_graph(baseline), _graph(candidate))


def _compare_graphs(a, b):
    budget = _Budget()
    pairs = []
    parents = []
    removed = added = None
    limit = None

    def witness(index):
        labels = []
        while parents[index] is not None:
            index, label = parents[index]
            labels.append(label)
        labels.reverse()
        return {"activities": labels[:MAX_WITNESS_LABELS], "length": len(labels),
                "truncated": len(labels) > MAX_WITNESS_LABELS}

    if not a["complete"] or not b["complete"]:
        limit = "incomplete_marking_graph"
    else:
        try:
            first = (_closure(a, {0}, budget), _closure(b, {0}, budget))
            pairs.append(first)
            parents.append(None)
            visited = {first}
            cursor = 0
            while cursor < len(pairs):
                left, right = pairs[cursor]
                accepts_a = a["final"] is not None and a["final"] in left
                accepts_b = b["final"] is not None and b["final"] in right
                if accepts_a and not accepts_b and removed is None:
                    removed = witness(cursor)
                if accepts_b and not accepts_a and added is None:
                    added = witness(cursor)
                moves_a = _moves(a, left, budget)
                moves_b = _moves(b, right, budget)
                for label in sorted(moves_a.keys() | moves_b.keys()):
                    budget.spend()
                    nxt = (_closure(a, moves_a.get(label, ()), budget),
                           _closure(b, moves_b.get(label, ()), budget))
                    if nxt in visited:
                        continue
                    if len(pairs) >= MAX_PRODUCT_STATES:
                        limit = "product_state_limit"
                        break
                    visited.add(nxt)
                    pairs.append(nxt)
                    parents.append((cursor, label))
                if limit:
                    break
                cursor += 1
        except _BudgetExceeded:
            limit = "product_work_limit"

    complete = limit is None
    # Positive inclusion requires complete search; a discovered mismatch is a
    # proof of non-inclusion even if the remaining search is interrupted.
    preserves = False if removed is not None else (True if complete else None)
    introduces_none = False if added is not None else (True if complete else None)
    equivalent = False if False in (preserves, introduces_none) else (True if complete else None)

    def coverage(graph):
        return {k: v for k, v in graph.items() if k not in {"edges", "final"}}

    return {"format": FORMAT, "baseline": coverage(a), "candidate": coverage(b),
            "complete": complete, "limit": limit, "equivalent": equivalent,
            "candidate_preserves_baseline_language": preserves,
            "candidate_introduces_no_new_language": introduces_none,
            "removed_trace": removed, "added_trace": added,
            "product_states": len(pairs), "product_work": budget.work,
            "limits": {"product_states": MAX_PRODUCT_STATES, "product_work": MAX_PRODUCT_WORK,
                       "marking_states_per_model": soundness.MAX_STATES,
                       "transition_checks_per_model": soundness.MAX_CHECKS,
                       "tokens_per_place": soundness.MAX_TOKENS},
            "caveat": "Compares completed visible activity sequences only; not timing, data, resource, fairness, deadlock or business outcome equivalence."}
