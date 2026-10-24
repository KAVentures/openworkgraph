"""Classic token-based replay of observed paths on a reviewed process graph.

Rozinat & van der Aalst style: replay each event on the graph's edge-token net
(the same semantics as the structural alignment in ``process_parallel``),
counting produced (p), consumed (c), missing (m) and remaining (r) tokens:

    fitness = 1/2 (1 - m / c) + 1/2 (1 - r / p)

Missing tokens show where an activity happened without being enabled by the
graph; remaining tokens show which activities the graph expected but the case
never performed. Gateways, start/end and routing nodes are silent and fire only
when that enables the next observed activity or completion. Replay is greedy
(as in the classic method), so it can over-count deviations compared with an
optimal alignment; use the alignment for minimum edit counts.

Replay checks structure only: no business rule, policy or outcome is evaluated.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any

from .process_parallel import EdgeTokenModel

MAX_SILENT_STATES = 256
MAX_REPLAY_EVENTS = 5000
_INITIAL, _DONE = -1, -2


class _Net:
    def __init__(self, model) -> None:
        automaton = EdgeTokenModel(model)
        self.nodes = automaton.nodes
        self.incoming, self.outgoing = automaton.incoming, automaton.outgoing
        self.place_target: dict[int, str] = {}
        for identifier, places in self.incoming.items():
            for place in places:
                self.place_target[place] = identifier
        self.closures: dict[tuple, tuple[Counter, int, int] | None] = {}
        self.search_states = 0
        self.by_label: dict[str, list[str]] = defaultdict(list)
        for identifier, node in self.nodes.items():
            if node.kind == "activity":
                self.by_label[node.label].append(identifier)

    def consumptions(self, identifier: str, marking: Counter) -> list[list[int]]:
        """Ways the node can fire from this marking (empty when not enabled)."""
        places = self.incoming[identifier]
        if self.nodes[identifier].kind == "parallel_join":
            return [list(places)] if places and all(marking[p] > 0 for p in places) else []
        return [[place] for place in places if marking[place] > 0]

    def emissions(self, identifier: str) -> list[list[int]]:
        places = self.outgoing[identifier]
        if self.nodes[identifier].kind == "parallel_split":
            return [list(places)]
        return [[place] for place in places] or [[]]


def _key(marking: Counter) -> tuple:
    return tuple(sorted((place, count) for place, count in marking.items() if count))


def _apply(marking: Counter, consumed: list[int], emitted: list[int]) -> Counter:
    following = Counter(marking)
    following.subtract(consumed)
    following.update(emitted)
    return +following


def _silent_closure(net: _Net, marking: Counter, goal, goal_key) -> tuple[Counter, int, int] | None:
    """Shortest sequence of silent firings reaching a marking where ``goal`` holds.

    Returns (marking, tokens consumed, tokens produced) or None within budget.
    Results are memoized per (marking, goal) because variants share prefixes.
    """
    cache_key = (_key(marking), goal_key)
    if cache_key not in net.closures:
        found = _search(net, marking, goal)
        net.closures[cache_key] = None if found is None else (Counter(found[0]), found[1], found[2])
    cached = net.closures[cache_key]
    return None if cached is None else (Counter(cached[0]), cached[1], cached[2])


def _search(net: _Net, marking: Counter, goal) -> tuple[Counter, int, int] | None:
    if goal(marking):
        return marking, 0, 0
    queue = deque([(marking, 0, 0)])
    seen = {_key(marking)}
    while queue:
        current, used, made = queue.popleft()
        net.search_states += 1
        for identifier, node in net.nodes.items():
            if node.kind == "activity":
                continue
            for consumed in net.consumptions(identifier, current):
                for emitted in net.emissions(identifier):
                    following = _apply(current, consumed, emitted)
                    key = _key(following)
                    if key in seen:
                        continue
                    if len(seen) >= MAX_SILENT_STATES:
                        return None
                    seen.add(key)
                    state = (following, used + len(consumed), made + len(emitted))
                    if goal(following):
                        return state
                    queue.append(state)
    return None


def replay_path(path: tuple[str, ...] | list[str], model, *, _net: _Net | None = None) -> dict[str, Any]:
    """Replay one observed path; return token counts and per-activity diagnostics."""
    if len(path) > MAX_REPLAY_EVENTS:
        raise ValueError(f"Token replay path exceeds the {MAX_REPLAY_EVENTS:,} event limit")
    net = _net or _Net(model)
    marking: Counter = Counter({_INITIAL: 1})
    produced, consumed, missing = 1, 0, 0
    missing_at: Counter = Counter()
    unmodelled: Counter = Counter()

    def enabled_for(label: str | None):
        if label is None:
            return lambda m: m[_DONE] > 0
        identifiers = net.by_label.get(label, [])
        return lambda m: any(net.consumptions(identifier, m) for identifier in identifiers)

    for index, label in enumerate(path):
        identifiers = net.by_label.get(label, [])
        if not identifiers:
            # Not in the graph: it could never have been enabled.
            unmodelled[label] += 1
            missing += 1
            consumed += 1
            missing_at[label] += 1
            continue
        reached = _silent_closure(net, marking, enabled_for(label), label)
        if reached is not None:
            marking, used, made = reached
            consumed += used
            produced += made
            identifier = next(i for i in identifiers if net.consumptions(i, marking))
            taken = net.consumptions(identifier, marking)[0]
        else:
            identifier = identifiers[0]
            places = net.incoming[identifier]
            required = places if net.nodes[identifier].kind == "parallel_join" else places[:1]
            absent = [place for place in required if marking[place] == 0]
            missing += len(absent)
            missing_at[label] += len(absent)
            marking.update(absent)
            taken = list(required)
        consumed += len(taken)
        lookahead_key = path[index + 1] if index + 1 < len(path) else None
        lookahead = enabled_for(lookahead_key)
        options = net.emissions(identifier)
        chosen = options[0]
        for option in options:
            if _silent_closure(net, _apply(marking, taken, option), lookahead, lookahead_key) is not None:
                chosen = option
                break
        marking = _apply(marking, taken, chosen)
        produced += len(chosen)

    reached = _silent_closure(net, marking, enabled_for(None), None)
    if reached is not None:
        marking, used, made = reached
        consumed += used
        produced += made
        marking = _apply(marking, [_DONE], [])
    else:
        missing += 1
        missing_at["(process end)"] += 1
    consumed += 1
    remaining = sum(marking.values())
    remaining_at: Counter = Counter()
    for place, count in marking.items():
        target = net.place_target.get(place)
        label = net.nodes[target].label if target else ("(process end)" if place == _DONE else "(process start)")
        remaining_at[label] += count
    return {
        "produced": produced, "consumed": consumed, "missing": missing, "remaining": remaining,
        "fitness": token_fitness(produced, consumed, missing, remaining),
        "fits": missing == 0 and remaining == 0,
        "missing_by_activity": dict(missing_at),
        "remaining_by_activity": dict(remaining_at),
        "unmodelled_activities": dict(unmodelled),
    }


def token_fitness(produced: int, consumed: int, missing: int, remaining: int) -> float:
    if not produced or not consumed:
        return 0.0
    return round(0.5 * (1 - missing / consumed) + 0.5 * (1 - remaining / produced), 6)


def replay_paths(weighted_paths, model, *, max_events: int = 2_000_000,
                 max_search_states: int = 400_000) -> dict[str, Any]:
    """Aggregate replay over (path, case count) pairs, weighting by cases.

    Stops (and reports ``replay_truncated``) once the event or silent-search
    budget is spent; replayed cases are reported, the rest stay unreplayed.
    """
    totals = Counter()
    missing_at: Counter = Counter()
    remaining_at: Counter = Counter()
    unmodelled: Counter = Counter()
    fitting = cases = replayed_events = 0
    variants = []
    truncated = False
    net = _Net(model)
    for path, count in weighted_paths:
        if replayed_events + len(path) > max_events or net.search_states > max_search_states:
            truncated = True
            break
        result = replay_path(tuple(path), model, _net=net)
        replayed_events += len(path)
        cases += count
        fitting += count if result["fits"] else 0
        for name in ("produced", "consumed", "missing", "remaining"):
            totals[name] += result[name] * count
        for source, target in ((result["missing_by_activity"], missing_at),
                               (result["remaining_by_activity"], remaining_at),
                               (result["unmodelled_activities"], unmodelled)):
            for label, value in source.items():
                target[label] += value * count
        variants.append({"path": list(path), "cases": count, **{k: result[k] for k in (
            "produced", "consumed", "missing", "remaining", "fitness", "fits")}})
    return {
        "replayed_cases": cases,
        "fitting_cases": fitting,
        "fitting_case_share": fitting / cases if cases else None,
        "token_fitness": token_fitness(totals["produced"], totals["consumed"], totals["missing"],
                                       totals["remaining"]) if cases else None,
        "tokens": dict(totals),
        "missing_tokens_by_activity": dict(missing_at.most_common(20)),
        "remaining_tokens_by_activity": dict(remaining_at.most_common(20)),
        "unmodelled_activities": dict(unmodelled.most_common(20)),
        "variants": variants,
        "replay_truncated": truncated,
        "method": "greedy_token_replay_on_edge_token_net",
        "silent_nodes": "gateways, start, end and routing fire only to enable the next observed activity or completion",
        "may_overcount_deviations_versus_optimal_alignment": True,
        "business_rules_evaluated": False,
        "policy_compliance_verified": False,
        "business_outcome_verified": False,
    }


__all__ = ["replay_path", "replay_paths", "token_fitness"]
