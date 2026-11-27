"""Generate bounded acceptance scenarios from a reviewed reference PNML.

This is characterization testing, not independent validation of the reference.
A complete marking graph is required to derive negative expectations. Prefix
subset exploration can be incomplete without making individual tests inexact.
"""
from __future__ import annotations

from collections import deque

from . import event_log_petri_acceptance as acceptance
from . import event_log_petri_language as language
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.petri_test_generation.v1"
MAX_PREFIX_STATES = 2000
MAX_WORK = 600_000
MAX_SELECTION_WORK = 2_000_000
MAX_DEPTH = acceptance.MAX_TRACE - 1


class _Budget(acceptance._Budget):
    def spend(self, n=1):
        if self.work + n > MAX_WORK:
            raise acceptance._Limit()
        self.work += n


def _settings(spec):
    if not isinstance(spec, dict) or set(spec) - {"max_cases", "max_depth"}:
        raise EventLogWorkspaceError("unknown test generation settings")
    cases = spec.get("max_cases", 20)
    depth = spec.get("max_depth", 20)
    if type(cases) is not int or not 1 <= cases <= acceptance.MAX_CASES:
        raise EventLogWorkspaceError("max_cases must be 1 to 40")
    if type(depth) is not int or not 1 <= depth <= MAX_DEPTH:
        raise EventLogWorkspaceError("max_depth must be 1 to 99")
    return cases, depth


def _features(states, dist, completed, next_labels, negative=False):
    viable = states & dist.keys()
    tags = {"negative_prefix"} if negative else {
        "completed_trace" if completed else "noncompleted_prefix",
        "finishable_prefix" if viable else "nonfinishable_prefix",
    }
    if not negative:
        if viable and states - viable:
            tags.add("ambiguous_completion")
        if len(next_labels) > 1:
            tags.add("branching_next")
        if completed and next_labels:
            tags.add("optional_continuation")
    return tags


def generate(data: bytes, spec=None):
    max_cases, max_depth = _settings({} if spec is None else spec)
    graph = language._graph(data)
    coverage = {k: v for k, v in graph.items() if k not in {"edges", "final"}}
    result = {"format": FORMAT, "model": coverage,
              "settings": {"max_cases": max_cases, "max_depth": max_depth},
              "limits": {"prefix_states": MAX_PREFIX_STATES, "work": MAX_WORK, "selection_work": MAX_SELECTION_WORK},
              "suite": None, "suite_sha256": None, "selected": [],
              "caveat": "Generated expectations characterize this reference model, not independent business policy or real execution. Review them before treating them as acceptance criteria."}
    if not graph["complete"]:
        return {**result, "status": "inconclusive", "limit": "incomplete_marking_graph",
                "prefix_exploration_complete": False, "prefix_states": 0, "work": 0}
    budget = _Budget()
    candidates = []
    queue = deque()
    seen = set()
    limit = None
    alphabet = sorted({label for edges in graph["edges"] for label in edges if label is not None})
    try:
        # Validate labels using the actual suite contract. The graph supports
        # other visible labels which must not be silently rewritten.
        acceptance._labels(alphabet, language.soundness.MAX_TRANSITIONS, unique=True)
        dist, _ = acceptance._reverse_completion(graph, budget)
        first = frozenset(acceptance._closure(graph, {0}, budget))
        queue.append((first, ()))
        seen.add(first)
        while queue:
            states, trace = queue.popleft()
            moves = {}
            for state in sorted(states):
                budget.spend()
                for label, targets in graph["edges"][state].items():
                    budget.spend(1 + len(targets))
                    if label is not None:
                        moves.setdefault(label, set()).update(targets)
            viable = states & dist.keys()
            next_labels = sorted(label for label, targets in moves.items() if targets & dist.keys())
            completed = graph["final"] is not None and graph["final"] in states
            expect = {"completed": completed, "fireable": True, "finishable": bool(viable),
                      "all_prefix_states_finishable": states <= viable}
            if len(next_labels) <= acceptance.MAX_LABELS:
                expect["exact_next"] = next_labels
            tags = _features(states, dist, completed, next_labels)
            # A characterization case covers this exact determinized prefix
            # state, visible next options and semantic category. Not transition
            # execution coverage: a visible trace can have several realizations.
            features = {("category", tag) for tag in tags}
            features.update(("marking", m) for m in states)
            features.update(("next", label) for label in next_labels)
            features.update(("activity", label) for label in trace)
            candidates.append({"trace": trace, "expect": expect, "tags": tags, "features": features})
            if len(trace) < acceptance.MAX_TRACE:
                # One impossible next activity per representative prefix, chosen
                # from the model alphabet, avoids inventing an unknown label.
                missing = next((label for label in alphabet if label not in moves), None)
                if missing is not None:
                    neg_tags = {"negative_prefix"}
                    candidates.append({"trace": trace + (missing,), "expect": {
                        "completed": False, "fireable": False, "finishable": False,
                        "all_prefix_states_finishable": False, "exact_next": []},
                        "tags": neg_tags, "features": {("category", "negative_prefix"),
                            ("negative_next", missing)}})
            if len(trace) >= max_depth:
                # Only mark depth incomplete if an as-yet-unseen subset lies
                # beyond the configured depth. BFS discovers shortest prefixes.
                for label in sorted(moves):
                    target = frozenset(acceptance._closure(graph, moves[label], budget))
                    if target not in seen:
                        limit = limit or "depth_limit"
                continue
            for label in sorted(moves):
                target = frozenset(acceptance._closure(graph, moves[label], budget))
                if target in seen:
                    continue
                if len(seen) >= MAX_PREFIX_STATES:
                    limit = "prefix_state_limit"
                    break
                seen.add(target)
                queue.append((target, trace + (label,)))
            if limit == "prefix_state_limit":
                break
    except acceptance._Limit:
        limit = "work_limit"
    # Greedy deterministic diversity: prioritize semantic categories, then
    # distinct marking and activity/next-option coverage; ties prefer shorter
    # traces. Explicitly account for any budget limiting suite selection.
    covered = set()
    selected = []
    remaining = list(candidates)
    selection_work = 0
    selection_limited = False
    while remaining and len(selected) < max_cases:
        round_work = sum(len(row["features"]) + 1 for row in remaining)
        if selection_work + round_work > MAX_SELECTION_WORK:
            selection_limited = True
            break
        selection_work += round_work
        def score(row):
            novel = row["features"] - covered
            return (-sum(k == "category" for k, _ in novel), -len(novel),
                    len(row["trace"]), row["trace"])
        row = min(remaining, key=score)
        remaining.remove(row)
        if row["features"] <= covered and selected:
            break
        selected.append(row)
        covered.update(row["features"])
    suite_limited = False
    suite = {"format": acceptance.SUITE_FORMAT, "cases": []}
    notes = []
    for row in selected:
        ident = f"generated-{len(suite['cases'])+1:02d}"
        case = {"id": ident, "trace": list(row["trace"]), "expect": row["expect"]}
        try:
            acceptance.parse_suite({**suite, "cases": suite["cases"] + [case]})
        except EventLogWorkspaceError:
            # The only expected failure here is the canonical byte limit.
            suite_limited = True
            break
        suite["cases"].append(case)
        notes.append({"id": ident, "categories": sorted(row["tags"])})
    canonical, digest = acceptance.parse_suite(suite) if suite["cases"] else (None, None)
    # A depth cutoff encountered before later same-depth nodes were processed
    # remains conservative; never inflate completion from a partial search.
    all_categories = {tag for row in candidates for tag in row["tags"]}
    chosen_categories = {tag for row in notes for tag in row["categories"]}
    return {**result, "status": "generated" if canonical else "inconclusive",
            "suite": canonical, "suite_sha256": digest, "selected": notes,
            "prefix_exploration_complete": limit is None,
            "limit": limit, "prefix_states": len(seen), "work": budget.work,
            "candidate_scenarios": len(candidates),
            "selection_work": selection_work, "selection_limited": selection_limited,
            "suite_byte_limited": suite_limited,
            "categories_found": sorted(all_categories),
            "categories_selected": sorted(chosen_categories),
            "categories_omitted": sorted(all_categories - chosen_categories),
            "visible_alphabet": alphabet,
            "coverage_caveat": "Prefix-state exploration and selected semantic categories are not exhaustive trace or transition coverage."}
