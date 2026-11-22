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
from .event_log_variant_coverage import VARIANT_KEY_SQL
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_alignments.v1"
MAX_VARIANTS = 500
MAX_TRACE_LENGTH = 200
MAX_STATES_PER_VARIANT = 20_000
MAX_STATES_TOTAL = 1_000_000
TOP_VARIANTS = 20
TOP_DEVIATIONS = 30
MAX_MONTHS = 120
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
               cohort: dict[str, Any] | None = None, _all_non_fitting: bool = False) -> dict[str, Any]:
    net = parse_pnml(pnml)
    labels = {label for label in net["transitions"].values() if label is not None}

    def run(conn):
        variant_key = VARIANT_KEY_SQL.format(path="path")
        rows = conn.execute(f"""SELECT path, count(*) AS cases, {variant_key} FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id HAVING count(*) <= ?)
            GROUP BY path ORDER BY cases DESC, path LIMIT ?""", [MAX_TRACE_LENGTH, MAX_VARIANTS]).fetchall()
        total = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        months = conn.execute("""WITH c AS (
              SELECT list(activity ORDER BY ts, event_id) AS path, min(ts) AS started FROM ce GROUP BY case_id
              HAVING count(*) <= ?),
            v AS (SELECT path, count(*) AS cases FROM c GROUP BY path ORDER BY cases DESC, path LIMIT ?)
            SELECT c.path, strftime(date_trunc('month', c.started), '%Y-%m') AS month, count(*)
            FROM c JOIN v ON c.path = v.path GROUP BY 1, 2""", [MAX_TRACE_LENGTH, MAX_VARIANTS]).fetchall()
        return {"rows": rows, "cases_total": total, "months": months}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows = data.pop("rows")
    by_path_month = data.pop("months")
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
    aligned_cost: dict[tuple, tuple[int, int]] = {}
    for path, n, variant_id in rows:
        trace = tuple(path)
        result, used = (None, 0) if remaining <= 0 else _align(net, trace, labels, min(MAX_STATES_PER_VARIANT, remaining))
        remaining -= used
        if result is None or result["cost"] is None:
            unaligned.append({"path": list(trace), "variant_id": variant_id, "cases": n})
            continue
        cost = result["cost"]
        worst = len(trace) + shortest_run
        aligned_cost[trace] = (cost, worst)
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
        variants.append({"path": list(trace), "variant_id": variant_id, "cases": n, "cost": cost, "fitness": round(fitness, 4),
                         "alignment": [{"log": s[0], "model": s[1], "move": s[2]} for s in steps if s[2] != SILENT]})
    variants.sort(key=lambda v: (-v["cases"], v["fitness"]))
    month_totals: dict[str, list[int]] = {}
    for path, month, n in by_path_month:
        if tuple(path) not in aligned_cost:
            continue
        cost, worst = aligned_cost[tuple(path)]
        totals = month_totals.setdefault(month, [0, 0, 0, 0])
        totals[0] += n
        totals[1] += n if cost == 0 else 0
        totals[2] += cost * n
        totals[3] += worst * n
    by_month = [{"month": m, "cases": t[0], "fitting_case_share": round(t[1] / t[0], 4),
                 "fitness": round(1 - t[2] / t[3], 4) if t[3] else None}
                for m, t in sorted(month_totals.items())][-MAX_MONTHS:]
    unaligned_cases = sum(u["cases"] for u in unaligned)
    observed = {a for path, _, _ in rows for a in path}

    def deviations(times: Counter, in_cases: Counter) -> list[dict[str, Any]]:
        order = sorted(times, key=lambda a: (-in_cases[a], -times[a], a))
        return [{"activity": a, "cases": in_cases[a], "times": times[a]} for a in order[:TOP_DEVIATIONS]]

    extra = {"_non_fitting_paths": [v["path"] for v in variants]} if _all_non_fitting else {}
    return {
        **extra,
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
        "by_month": by_month,
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


def deviation_causes(log_id: str, pnml: bytes, case_type: str | None = None, *,
                     cohort: dict[str, Any] | None = None, limit: int = 30) -> dict[str, Any]:
    """Which case features go with cases that deviate from the net: root-cause associations with the
    outcome "the case followed a path the alignment found non-fitting"."""
    from . import event_log_analytics as analytics
    aligned = alignments(log_id, pnml, case_type, cohort=cohort, _all_non_fitting=True)
    paths = aligned.pop("_non_fitting_paths")
    summary = {"format": FORMAT + "+causes", "log_id": log_id, "cases": aligned["cases"],
               "fitting_cases": aligned["fitness"]["fitting_cases"], "non_fitting_paths": len(paths),
               "unaligned": aligned["unaligned"]}
    if not paths:
        return {**summary, "associations": [], "note": "every aligned case fits the net, so nothing deviates"}
    causes = analytics.root_cause(log_id, "variant_in", case_type, variants=paths, cohort=cohort, limit=limit)
    return {**summary, **{k: v for k, v in causes.items() if k not in ("log",)},
            "outcome_meaning": "the case followed a path that does not fit the net",
            "caveat": ("Associations with deviating cases, not causes. Activities are listed too: a case with an "
                       "extra or rare step often deviates because of that step.")}


def case_alignment(log_id: str, case_id: str, case_type: str | None = None, *, noise: float = 0.2,
                   pnml: bytes | None = None) -> dict[str, Any]:
    """How one case departs from a net: the discovered net at ``noise`` unless a PNML net is given."""
    from . import event_log_pnml as pnml_export
    if not isinstance(case_id, str) or not case_id or len(case_id) > 500:
        raise EventLogWorkspaceError("give a case ID")
    meta: dict[str, Any] = {}
    if pnml is None:
        if isinstance(noise, bool) or not isinstance(noise, (int, float)) or not 0 <= noise < 1:
            raise EventLogWorkspaceError("noise must be a share from 0 up to 1")
        xml, meta = pnml_export.discovered_pnml(log_id, case_type, noise=float(noise))
        pnml = xml.encode()
    net = parse_pnml(pnml)
    labels = {label for label in net["transitions"].values() if label is not None}

    def run(conn):
        return {"path": conn.execute("SELECT list(activity ORDER BY ts, event_id) FROM ce WHERE case_id = ?",
                                     [case_id]).fetchone()[0]}

    path = workspace._query(log_id, case_type, run)["path"]
    if not path:
        raise EventLogWorkspaceError("no case with that ID in this log")
    if len(path) > MAX_TRACE_LENGTH:
        raise EventLogWorkspaceError(f"the case has more than {MAX_TRACE_LENGTH} events")
    empty, _ = _align(net, (), labels, MAX_STATES_PER_VARIANT)
    if empty is None or empty["cost"] is None:
        raise EventLogWorkspaceError("the net cannot reach its final marking from its initial marking")
    result, _ = _align(net, tuple(path), labels, MAX_STATES_PER_VARIANT * 5)
    base = {"format": FORMAT + "+case", "log_id": log_id, "case_id": case_id, "path": list(path),
            "model": {"noise": noise} if meta else {"uploaded": True},
            "process_model_sha256": meta.get("process_model_sha256")}
    if result is None or result["cost"] is None:
        return {**base, "aligned": False, "note": "the search limit was reached before an alignment was found"}
    steps = [{"log": s[0], "model": s[1], "move": s[2]} for s in result["steps"] if s[2] != SILENT]
    worst = len(path) + empty["cost"]
    return {**base, "aligned": True, "cost": result["cost"], "fits": result["cost"] == 0,
            "fitness": round(1 - result["cost"] / worst, 4) if worst else 1.0,
            "extra_steps": [s["log"] for s in steps if s["move"] == LOG],
            "skipped_steps": [s["model"] for s in steps if s["move"] == MODEL],
            "alignment": steps}


def reference_net(pnml: bytes) -> dict[str, Any]:
    """Parse a net to keep as a reference and check its final marking can be reached."""
    net = parse_pnml(pnml)
    labels = {label for label in net["transitions"].values() if label is not None}
    empty, _ = _align(net, (), labels, MAX_STATES_PER_VARIANT)
    if empty is None or empty["cost"] is None:
        raise EventLogWorkspaceError("the net cannot reach its final marking from its initial marking")
    return net


def conformance_of(pnml: bytes, paths: list[tuple[list[str], int]]) -> dict[str, Any]:
    """Share of cases not fitting a net and their alignment fitness, for (path, cases) pairs."""
    net = reference_net(pnml)
    labels = {label for label in net["transitions"].values() if label is not None}
    shortest = _align(net, (), labels, MAX_STATES_PER_VARIANT)[0]["cost"]
    remaining = MAX_STATES_TOTAL
    cases = non_fitting = unaligned = 0
    cost_sum = worst_sum = 0
    for path, n in sorted(paths, key=lambda p: -p[1])[:MAX_VARIANTS]:
        trace = tuple(path)
        if len(trace) > MAX_TRACE_LENGTH or remaining <= 0:
            unaligned += n
            continue
        result, used = _align(net, trace, labels, min(MAX_STATES_PER_VARIANT, remaining))
        remaining -= used
        if result is None or result["cost"] is None:
            unaligned += n
            continue
        cases += n
        non_fitting += n if result["cost"] else 0
        cost_sum += result["cost"] * n
        worst_sum += (len(trace) + shortest) * n
    unaligned += sum(n for _, n in sorted(paths, key=lambda p: -p[1])[MAX_VARIANTS:])
    return {"cases": cases, "non_fitting_cases": non_fitting, "unaligned_cases": unaligned,
            "non_fitting_share": round(non_fitting / cases, 4) if cases else None,
            "alignment_fitness": round(1 - cost_sum / worst_sum, 4) if worst_sum else None}


MAX_EXPORT_CASES = 200_000


def _cell(value: Any) -> str:
    """CSV cell safe to open in a spreadsheet: a leading formula character is neutralized."""
    from shared.spreadsheet_review import review_cell

    return review_cell("" if value is None else str(value))


def non_fitting_cases_csv(log_id: str, pnml: bytes, case_type: str | None = None, *,
                          cohort: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Every case whose path does not fit the net, one row each, for follow-up outside OWG: case ID, its
    activities, alignment fitness, and the steps it added or skipped. Distinct paths are aligned once."""
    import csv
    import io
    net = reference_net(pnml)
    labels = {label for label in net["transitions"].values() if label is not None}
    shortest = _align(net, (), labels, MAX_STATES_PER_VARIANT)[0]["cost"]

    def run(conn):
        rows = conn.execute("""SELECT case_id, list(activity ORDER BY ts, event_id) FROM ce GROUP BY case_id
            ORDER BY case_id""").fetchall()
        return {"rows": rows}

    rows = workspace._query_filtered(log_id, case_type, cohort, run)["rows"]
    by_path: dict[tuple, dict[str, Any] | None] = {}
    remaining = MAX_STATES_TOTAL
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["case_id", "activities", "alignment_fitness", "extra_steps", "skipped_steps"])
    written = unaligned = 0
    for case_id, path in rows:
        trace = tuple(path)
        if trace not in by_path:
            if len(trace) > MAX_TRACE_LENGTH or remaining <= 0:
                by_path[trace] = None
            else:
                result, used = _align(net, trace, labels, min(MAX_STATES_PER_VARIANT, remaining))
                remaining -= used
                by_path[trace] = None if result is None or result["cost"] is None else result
        result = by_path[trace]
        if result is None:
            unaligned += 1
            continue
        if result["cost"] == 0:
            continue
        if written >= MAX_EXPORT_CASES:
            break
        steps = result["steps"]
        worst = len(trace) + shortest
        writer.writerow([_cell(case_id), _cell(" > ".join(trace)),
                         round(1 - result["cost"] / worst, 4) if worst else 1.0,
                         _cell(" > ".join(st[0] for st in steps if st[2] == LOG)),
                         _cell(" > ".join(st[1] for st in steps if st[2] == MODEL))])
        written += 1
    return out.getvalue(), {"cases": len(rows), "non_fitting_cases": written, "unaligned_cases": unaligned,
                            "truncated": written >= MAX_EXPORT_CASES}


MAX_TAG_VARIANTS = 500


def tag_non_fitting_cases(log_id: str, pnml: bytes, tag: str, case_type: str | None = None, *,
                          note: str = "", cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tag every case whose path does not fit the net, so the tag can focus other analyses on them.

    Distinct paths are aligned once. The cases are chosen by their exact path (the cohort's ``variants``
    key) within the given case filter, then tagged with the case-tag store; the most common
    MAX_TAG_VARIANTS non-fitting paths are covered."""
    from . import event_log_case_tags as case_tags
    net = reference_net(pnml)
    labels = {label for label in net["transitions"].values() if label is not None}

    def run(conn):
        rows = conn.execute("""SELECT path, count(*) AS cases FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id)
            GROUP BY path ORDER BY cases DESC, path""").fetchall()
        return {"rows": rows}

    rows = workspace._query_filtered(log_id, case_type, cohort, run)["rows"]
    remaining = MAX_STATES_TOTAL
    misfit: list[tuple[list[str], int]] = []
    unaligned = 0
    for path, n in rows:
        trace = tuple(path)
        if len(trace) > MAX_TRACE_LENGTH or remaining <= 0:
            unaligned += n
            continue
        result, used = _align(net, trace, labels, min(MAX_STATES_PER_VARIANT, remaining))
        remaining -= used
        if result is None or result["cost"] is None:
            unaligned += n
        elif result["cost"] > 0:
            misfit.append((list(trace), n))
    if not misfit:
        raise EventLogWorkspaceError("every aligned case fits the net; there is nothing to tag")
    chosen = misfit[:MAX_TAG_VARIANTS]
    spec: dict[str, Any] = {"tag": tag, "note": note or "does not fit the reference Petri net",
                            "cohort": {**{k: v for k, v in (cohort or {}).items() if v not in (None, "", [], {})},
                                       "variants": [p for p, _ in chosen]}}
    if case_type:
        spec["case_type"] = case_type
    tagged = case_tags.add(log_id, spec)
    return {**tagged, "non_fitting_cases": sum(n for _, n in misfit),
            "non_fitting_paths": len(misfit), "paths_tagged": len(chosen),
            "cases_not_tagged": sum(n for _, n in misfit[MAX_TAG_VARIANTS:]), "unaligned_cases": unaligned}


__all__ = ["tag_non_fitting_cases", "alignments", "deviation_causes", "case_alignment", "reference_net", "conformance_of",
           "non_fitting_cases_csv", "FORMAT"]
