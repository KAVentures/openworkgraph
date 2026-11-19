"""Alignments: the cheapest way to explain each recorded case on a Petri net, step by step.

An alignment (Adriansyah 2014; van der Aalst, "Process Mining", ch. 8; PM4Py's and ProM's
alignment-based conformance) pairs the steps of a case with a run of the net from its initial to its
final marking. Each pair is one of:

* a **synchronous move**: the case did the step and the net allowed it there;
* a **log move**: the case did a step the net cannot explain at that point;
* a **model move**: the net needed a step the case skipped;
* a **silent move**: the net fired an invisible transition (free, not a deviation).

Log and model moves cost 1 each; the alignment returned is one of least cost, found by a shortest
path search over the product of the case and the net. Unlike token replay, the result names exactly
which steps were extra and which were skipped, and it is not confused by duplicate labels or silent
paths. Trace fitness is ``1 - cost / (case length + cost of the shortest run of the net)``, as in
PM4Py; log fitness divides the summed costs by the summed worst cases.

Identical cases (one variant) are aligned once. The search has a per-variant and a total budget;
variants it cannot finish within them are reported, never guessed.
"""
from __future__ import annotations

import heapq
from collections import Counter
from itertools import count
from typing import Any

from . import event_log_workspace as workspace
from .event_log_petri_replay import parse_pnml
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_alignments.v1"
MAX_VARIANTS = 500
MAX_TRACE_LENGTH = 200
MAX_STATES_PER_VARIANT = 20_000
MAX_STATES_TOTAL = 1_000_000
TOP_VARIANTS = 20
TOP_DEVIATIONS = 30
SYNC, LOG, MODEL, SILENT = "sync", "log", "model", "silent"


def _key(marking: Counter) -> tuple:
    return tuple(sorted((p, n) for p, n in marking.items() if n))


def _fire(marking: tuple, pre: Counter, post: Counter) -> tuple | None:
    m = dict(marking)
    for p, n in pre.items():
        if m.get(p, 0) < n:
            return None
        m[p] -= n
    for p, n in post.items():
        m[p] = m.get(p, 0) + n
    return tuple(sorted((p, n) for p, n in m.items() if n))


def _align(net: dict[str, Any], trace: tuple[str, ...], labels: set[str], budget: int) -> tuple[dict | None, int]:
    """Least-cost alignment by Dijkstra/A* over (marking, position). Returns (result or None, states used)."""
    pre, post, names = net["pre"], net["post"], net["transitions"]
    start, goal = _key(net["initial"]), _key(net["final"])
    n = len(trace)
    # Admissible heuristic: steps whose label no transition has can only be log moves.
    unmatched_from = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        unmatched_from[i] = unmatched_from[i + 1] + (trace[i] not in labels)
    tie = count()
    # Priority: estimated cost, then fewer silent moves, then further along the case.
    frontier = [(unmatched_from[0], 0, 0, 0, next(tie), (start, 0))]
    best: dict[tuple, tuple[int, int]] = {(start, 0): (0, 0)}
    parent: dict[tuple, tuple[tuple, tuple[str | None, str | None, str]] | None] = {(start, 0): None}
    done: set[tuple] = set()
    transitions = sorted(names.items())
    expanded = 0
    while frontier:
        _, cost, silent, _, _, state = heapq.heappop(frontier)
        if state in done:
            continue
        done.add(state)
        marking, i = state
        if i == n and marking == goal:
            steps = []
            node = state
            while parent[node] is not None:
                node, move = parent[node]
                steps.append(move)
            steps.reverse()
            return {"cost": cost, "steps": steps}, expanded
        expanded += 1
        if expanded > budget:
            return None, expanded
        moves: list[tuple[tuple, int, int, tuple]] = []
        if i < n:
            moves.append(((marking, i + 1), 1, 0, (trace[i], None, LOG)))
        for t, label in transitions:
            fired = _fire(marking, pre[t], post[t])
            if fired is None:
                continue
            if label is None:
                moves.append(((fired, i), 0, 1, (None, t, SILENT)))
                continue
            moves.append(((fired, i), 1, 0, (None, label, MODEL)))
            if i < n and trace[i] == label:
                moves.append(((fired, i + 1), 0, 0, (label, label, SYNC)))
        for nxt, step_cost, step_silent, move in moves:
            if nxt in done:
                continue
            g = (cost + step_cost, silent + step_silent)
            if nxt in best and best[nxt] <= g:
                continue
            best[nxt] = g
            parent[nxt] = (state, move)
            heapq.heappush(frontier, (g[0] + unmatched_from[nxt[1]], g[0], g[1], -nxt[1], next(tie), nxt))
    return {"cost": None, "steps": []}, expanded   # final marking unreachable


def alignments(log_id: str, pnml: bytes, case_type: str | None = None, *,
               cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    net = parse_pnml(pnml)
    labels = {label for label in net["transitions"].values() if label is not None}

    def run(conn):
        rows = conn.execute("""SELECT path, count(*) AS cases FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id HAVING count(*) <= ?)
            GROUP BY path ORDER BY cases DESC, path LIMIT ?""", [MAX_TRACE_LENGTH, MAX_VARIANTS]).fetchall()
        total = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        return {"rows": rows, "cases_total": total}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows = data.pop("rows")
    if not rows:
        raise EventLogWorkspaceError("No cases are available to align in this selection")
    empty, used = _align(net, (), labels, MAX_STATES_PER_VARIANT)
    if empty is None or empty["cost"] is None:
        raise EventLogWorkspaceError("the net cannot reach its final marking from its initial marking"
                                     + (" within the search limit" if empty is None else ""))
    shortest_run = empty["cost"]
    remaining = MAX_STATES_TOTAL - used
    cases = fitting_cases = 0
    cost_sum = worst_sum = 0
    fitness_sum = 0.0
    log_moves: Counter = Counter()
    log_move_cases: Counter = Counter()
    model_moves: Counter = Counter()
    model_move_cases: Counter = Counter()
    variants, unaligned = [], []
    for path, n in rows:
        trace = tuple(path)
        result, used = (None, 0) if remaining <= 0 else _align(net, trace, labels, min(MAX_STATES_PER_VARIANT, remaining))
        remaining -= used
        if result is None or result["cost"] is None:
            unaligned.append({"path": list(trace), "cases": n})
            continue
        cost = result["cost"]
        worst = len(trace) + shortest_run
        fitness = 1 - cost / worst if worst else 1.0
        cases += n
        cost_sum += cost * n
        worst_sum += worst * n
        fitness_sum += fitness * n
        if cost == 0:
            fitting_cases += n
            continue
        steps = result["steps"]
        logs = Counter(step[0] for step in steps if step[2] == LOG)
        models = Counter(step[1] for step in steps if step[2] == MODEL)
        for a, k in logs.items():
            log_moves[a] += k * n
            log_move_cases[a] += n
        for a, k in models.items():
            model_moves[a] += k * n
            model_move_cases[a] += n
        variants.append({"path": list(trace), "cases": n, "cost": cost, "fitness": round(fitness, 4),
                         "alignment": [{"log": s[0], "model": s[1], "move": s[2]} for s in steps if s[2] != SILENT]})
    variants.sort(key=lambda v: (-v["cases"], v["fitness"]))
    unaligned_cases = sum(u["cases"] for u in unaligned)
    observed = {a for path, _ in rows for a in path}

    def deviations(times: Counter, in_cases: Counter) -> list[dict[str, Any]]:
        order = sorted(times, key=lambda a: (-in_cases[a], -times[a], a))
        return [{"activity": a, "cases": in_cases[a], "times": times[a]} for a in order[:TOP_DEVIATIONS]]

    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "net": {"places": len(net["places"]), "transitions": len(net["transitions"]),
                "silent_transitions": sum(1 for v in net["transitions"].values() if v is None),
                "labels": sorted(labels), "final_marking_declared": not net["final_guessed"],
                "shortest_run_visible_steps": shortest_run},
        "cases": cases, "cases_total": data.get("cases_total"),
        "cases_excluded": max(0, (data.get("cases_total") or 0) - cases - unaligned_cases),
        "variants": len(rows),
        "fitness": {"log": round(1 - cost_sum / worst_sum, 4) if worst_sum else None,
                    "average_case": round(fitness_sum / cases, 4) if cases else None,
                    "fitting_cases": fitting_cases,
                    "fitting_case_share": round(fitting_cases / cases, 4) if cases else None},
        "deviation_cost": {"total": cost_sum, "per_case": round(cost_sum / cases, 4) if cases else None},
        "log_moves": deviations(log_moves, log_move_cases),
        "model_moves": deviations(model_moves, model_move_cases),
        "activities_not_in_net": sorted(observed - labels),
        "non_fitting_variants": variants[:TOP_VARIANTS], "non_fitting_variants_total": len(variants),
        "unaligned_variants": unaligned[:TOP_VARIANTS],
        "unaligned": {"variants": len(unaligned), "cases": unaligned_cases},
        "method": ("optimal alignments (Adriansyah 2014): a least-cost search over the product of each variant and "
                   "the net, where steps only the case has and steps only the net has cost 1 and matching or "
                   "silent steps cost 0; trace fitness is 1 - cost / (case length + shortest run of the net)"),
        "caveat": ("When several alignments have the same least cost, one is shown; another explanation of the same "
                   "case may be equally cheap. Variants the search could not finish within its limits are listed "
                   "as unaligned and left out of the fitness."),
    }


__all__ = ["alignments", "FORMAT"]
