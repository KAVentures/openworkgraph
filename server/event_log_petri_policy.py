"""Model-level policy checking over completed visible traces.

Use the same untimed activity-only rule semantics as event-log compliance,
but quantify over the model rather than observed cases. Each rule is a finite
monitor; search the product of the monitor(s) and reachable Petri markings.
No traces are sampled and no business policy or execution grant is inferred.
"""
from __future__ import annotations

import heapq
import json
from hashlib import sha256

from . import event_log_compliance as compliance
from . import event_log_petri_language as language
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.petri_policy_review.v1"
MAX_RULES = 20
MAX_PRODUCT_STATES = 10_000
MAX_TOTAL_WORK = 600_000
MAX_WITNESS = 100
MAX_RULE_BYTES = 64 * 1024
SUPPORTED = {"response", "chain_response", "precedence", "not_coexistence", "existence"}


def parse_rules(raw):
    if not isinstance(raw, dict) or set(raw) != {"rules"}:
        raise EventLogWorkspaceError("send exactly a rules array")
    parsed = compliance.parse(raw)
    rules = []
    seen = set()
    for rule in parsed.rules:
        if rule.template not in SUPPORTED:
            raise EventLogWorkspaceError(
                "model policy review supports only untimed activity rules; "
                "resources and elapsed time are not present in ordinary P/T nets")
        item = rule.model_dump()
        key = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if key in seen:
            raise EventLogWorkspaceError("duplicate model policy rule")
        seen.add(key)
        rules.append(item)
    if len(rules) > MAX_RULES:
        raise EventLogWorkspaceError("too many model policy rules")
    return rules


def _step(rule, state, label):
    """Finite monitor state; silent firings are identity transitions."""
    if label is None:
        return state
    template, a = rule["template"], rule["a"]
    if template == "existence":
        # Counts saturate at the largest distinguishable value.
        bound = rule["min"] if rule["max"] is None else rule["max"] + 1
        return min(bound, state + (label == a))
    b = rule["b"]
    if template == "response":
        # 0 never activated, 1 pending, 2 activated and discharged.
        return 1 if label == a else (2 if label == b and state else state)
    if template == "chain_response":
        # 3 permanently violated; pending requires the very next visible step.
        if state == 3:
            return 3
        if state == 1:
            return 2 if label == b else 3
        return 1 if label == a else state
    if template == "precedence":
        # bit 0: seen A; bit 1: activated by B; bit 2: violated.
        if label == a:
            return state | 1
        if label == b:
            return state | 2 | (0 if state & 1 else 4)
        return state
    if template == "not_coexistence":
        return state | (1 if label == a else 2 if label == b else 0)
    raise AssertionError("unvalidated rule")


def _outcome(rule, state):
    """Return (activated, violated), matching the existing case checker."""
    template = rule["template"]
    if template == "existence":
        return True, state < rule["min"] or (rule["max"] is not None and state > rule["max"])
    if template == "response":
        return state != 0, state == 1
    if template == "chain_response":
        return state != 0, state in (1, 3)
    if template == "precedence":
        return bool(state & 2), bool(state & 4)
    if template == "not_coexistence":
        return state != 0, state == 3
    raise AssertionError("unvalidated rule")


class _Budget:
    def __init__(self):
        self.work = 0

    def spend(self, n):
        if self.work + n > MAX_TOTAL_WORK:
            return False
        self.work += n
        return True


def _search(graph, rules, budget):
    """Dijkstra with visible length then total firings as lexicographic cost.

    A witness is a real path even in an incomplete marking graph. Absence is
    certified only after complete graph and monitor-product exploration.
    """
    initial = (0, (0,) * len(rules))
    best = {initial: (0, 0)}
    parent = {initial: None}
    queue = [(0, 0, 0, initial)]
    serial = 0
    settled = set()
    found = {}
    limit = None

    def witness(key):
        labels = []
        firings = 0
        cursor = key
        while parent[cursor] is not None:
            cursor, label = parent[cursor]
            firings += 1
            if label is not None:
                labels.append(label)
        labels.reverse()
        return {"activities": labels[:MAX_WITNESS], "length": len(labels),
                "firings": firings, "truncated": len(labels) > MAX_WITNESS}

    while queue:
        visible, firings, _, key = heapq.heappop(queue)
        if key in settled or best[key] != (visible, firings):
            continue
        if not budget.spend(1 + len(rules)):
            limit = "total_work_limit"
            break
        settled.add(key)
        marking, states = key
        if marking == graph["final"]:
            outcomes = [_outcome(rule, state) for rule, state in zip(rules, states)]
            violated = [i for i, (_, bad) in enumerate(outcomes) if bad]
            activated = [i for i, (active, _) in enumerate(outcomes) if active]
            category = "violating" if violated else ("activated_satisfying" if activated else "vacuous")
            if category not in found:
                found[category] = {**witness(key), "violated_rule_indices": violated,
                                   "activated_rule_indices": activated}
        edges = graph["edges"][marking]
        for label, targets in sorted(edges.items(), key=lambda kv: (kv[0] is not None, kv[0] or "")):
            if not budget.spend(len(rules) + 1 + len(targets)):
                limit = "total_work_limit"
                break
            next_states = tuple(_step(rule, state, label) for rule, state in zip(rules, states))
            for target in sorted(targets):
                nxt = (target, next_states)
                cost = (visible + (label is not None), firings + 1)
                if nxt in best and best[nxt] <= cost:
                    continue
                if nxt not in best and len(best) >= MAX_PRODUCT_STATES:
                    limit = "product_state_limit"
                    break
                best[nxt] = cost
                parent[nxt] = (key, label)
                serial += 1
                heapq.heappush(queue, (*cost, serial, nxt))
            if limit:
                break
        if limit:
            break

    complete = graph["complete"] and limit is None
    if not graph["complete"] and limit is None:
        limit = "incomplete_marking_graph"
    violating = found.get("violating")
    activated = found.get("activated_satisfying")
    vacuous = found.get("vacuous")
    completed = bool(found)
    satisfies = activated is not None or vacuous is not None

    def possible(observed):
        return True if observed else (False if complete else None)

    # Avoid vacuous "verified" on a model with no completed execution.
    universal = False if violating else (True if complete and completed else None)
    if violating:
        status = "counterexample"
    elif complete and not completed:
        status = "no_completed_trace"
    elif complete and activated is None:
        status = "vacuous_only"
    elif complete:
        status = "verified"
    else:
        status = "inconclusive"
    return {"status": status, "complete": complete, "limit": limit,
            "product_states": len(best), "expanded_states": len(settled),
            "completed_trace_possible": possible(completed),
            "violating_trace_possible": possible(violating is not None),
            "satisfying_trace_possible": possible(satisfies),
            "activated_satisfying_trace_possible": possible(activated is not None),
            "vacuous_trace_possible": possible(vacuous is not None),
            "all_completed_traces_satisfy": universal,
            "witnesses": {"violating": violating, "activated_satisfying": activated, "vacuous": vacuous},
            "witness_shortest_in_model": graph["complete"]}


def _review(data, rules):
    graph = language._graph(data)
    budget = _Budget()
    # Fair share is not assumed; report any later check starved by the common
    # limit as explicitly inconclusive. Joint feasibility is checked first.
    joint = _search(graph, rules, budget)
    results = []
    for i, rule in enumerate(rules):
        check = joint if len(rules) == 1 else _search(graph, [rule], budget)
        results.append({"rule_index": i, "rule": rule, **check})
    return {"model": {k: v for k, v in graph.items() if k not in {"edges", "final"}},
            "joint": joint, "rules": results, "total_work": budget.work}


def _change(before, after):
    if before is None or after is None:
        return "inconclusive"
    if before == after:
        return "unchanged_possible" if before else "unchanged_impossible"
    return "introduced" if after else "removed"


def review(baseline: bytes, spec, candidate: bytes | None = None):
    rules = parse_rules(spec)
    canonical = json.dumps({"rules": rules}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    before = _review(baseline, rules)
    after = _review(candidate, rules) if candidate is not None else None
    changes = None
    if after is not None:
        def compare(a, b):
            return {"violating_trace_possibility": _change(a["violating_trace_possible"], b["violating_trace_possible"]),
                    "satisfying_trace_possibility": _change(a["satisfying_trace_possible"], b["satisfying_trace_possible"]),
                    "activated_satisfying_trace_possibility": _change(a["activated_satisfying_trace_possible"], b["activated_satisfying_trace_possible"]),
                    "completed_trace_possibility": _change(a["completed_trace_possible"], b["completed_trace_possible"])}
        changes = {"joint": compare(before["joint"], after["joint"]),
                   "rules": [{"rule_index": i, **compare(a, b)}
                             for i, (a, b) in enumerate(zip(before["rules"], after["rules"]))]}
    return {"format": FORMAT, "rules_sha256": sha256(canonical).hexdigest(),
            "baseline": before, "candidate": after, "changes": changes,
            "limits": {"product_states_per_check": MAX_PRODUCT_STATES,
                       "total_work_per_model": MAX_TOTAL_WORK,
                       "witness_visible_activities": MAX_WITNESS,
                       "marking_states_per_model": language.soundness.MAX_STATES},
            "caveat": "Only completed visible activity traces and declared untimed rules are checked. "
                      "Vacuity, no completed trace and resource/time/data constraints are separate; "
                      "a model proof is not evidence that real work or an AI agent obeys policy."}
