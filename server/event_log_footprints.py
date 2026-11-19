"""Footprints: the ordering relations between activities, from the log and from the discovered model.

A footprint matrix (van der Aalst, "Process Mining", ch. 3 and 8; PM4Py's footprints discovery and
footprints conformance; ProM's footprint comparison) classifies every ordered pair of activities:

* ``→`` (causal): ``a`` is directly followed by ``b`` somewhere, never the other way round;
* ``←`` (reverse causal): the mirror of ``→``;
* ``||`` (parallel): both orders occur;
* ``#`` (unrelated): neither occurs.

This module builds the matrix twice, once from the recorded cases and once from the process tree
the model discovery drafts (the same draft that is exported as BPMN and PNML), over the activities
the model shows. Where the two disagree, the log has an ordering the model does not allow, or the
model allows an ordering never seen. The cells that agree, as a share of all cells, are the
footprint conformance. Each log-only ordering carries how many cases took it.

Footprints compare order only: they ignore frequency, timing and how many times a loop repeats,
so they show *where* model and log differ rather than how much each difference matters.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_footprints.v1"
MAX_ACTIVITIES = 60
TOP_DIFFERENCES = 40

CAUSAL, REVERSE, PARALLEL, NONE = "→", "←", "||", "#"


class _Fp:
    __slots__ = ("acts", "start", "end", "skip", "seq", "par")

    def __init__(self, acts=frozenset(), start=frozenset(), end=frozenset(), skip=False, seq=frozenset(), par=frozenset()):
        self.acts, self.start, self.end, self.skip, self.seq, self.par = acts, start, end, skip, seq, par


def _tree_fp(node: dict[str, Any]) -> _Fp:
    """Bottom-up footprint of a process tree: which activities can directly follow which."""
    op = node.get("op")
    if op == "activity":
        a = frozenset([str(node["activity"])])
        return _Fp(a, a, a, False)
    if op == "tau":
        return _Fp(skip=True)
    kids = [_tree_fp(child) for child in node.get("children", [])]
    acts = frozenset().union(*(k.acts for k in kids)) if kids else frozenset()
    seq = set().union(*(k.seq for k in kids)) if kids else set()
    par = set().union(*(k.par for k in kids)) if kids else set()
    if op == "xor":
        return _Fp(acts, frozenset().union(*(k.start for k in kids)), frozenset().union(*(k.end for k in kids)),
                   any(k.skip for k in kids), frozenset(seq), frozenset(par))
    if op == "and":
        for i, ki in enumerate(kids):
            for kj in kids[i + 1:]:
                for a in ki.acts:
                    for b in kj.acts:
                        par.update({(a, b), (b, a)})
        return _Fp(acts, frozenset().union(*(k.start for k in kids)), frozenset().union(*(k.end for k in kids)),
                   all(k.skip for k in kids), frozenset(seq), frozenset(par))
    if op == "seq":
        for i, ki in enumerate(kids):
            for kj in kids[i + 1:]:
                seq.update((a, b) for a in ki.end for b in kj.start)
                if not kj.skip:
                    break
        start, end = set(), set()
        for k in kids:
            start |= k.start
            if not k.skip:
                break
        for k in reversed(kids):
            end |= k.end
            if not k.skip:
                break
        return _Fp(acts, frozenset(start), frozenset(end), all(k.skip for k in kids), frozenset(seq), frozenset(par))
    if op == "loop":
        body, redo = kids[0], kids[1]
        # After the body: the redo part, or the body again when the redo part can be skipped.
        after_body = set(redo.start) | (set(body.start) if redo.skip else set())
        seq.update((a, b) for a in body.end for b in after_body)
        after_redo = set(body.start) | (set(redo.start) if body.skip else set())
        seq.update((a, b) for a in redo.end for b in after_redo)
        if body.skip:
            seq.update((a, b) for a in redo.end for b in redo.start)
        start = set(body.start) | (set(redo.start) if body.skip else set())
        end = set(body.end) | (set(redo.end) if body.skip else set())
        return _Fp(acts, frozenset(start), frozenset(end), body.skip, frozenset(seq), frozenset(par))
    raise EventLogWorkspaceError(f"unknown process tree operator: {op!r}")


def _relation(a: str, b: str, follows: set[tuple[str, str]], par: set[tuple[str, str]] = frozenset()) -> str:
    ab, ba = (a, b) in follows, (b, a) in follows
    if (a, b) in par or (ab and ba):
        return PARALLEL
    if ab:
        return CAUSAL
    if ba:
        return REVERSE
    return NONE


def footprints(log_id: str, case_type: str | None = None, *, noise: float = 0.2, activities: float = 1.0,
               cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    from . import event_log_model_discovery as discovery
    model = discovery.discover(log_id, case_type, noise=noise, activities=activities, cohort=cohort)
    fp = _tree_fp(model["tree"])
    alphabet = sorted(fp.acts)
    if len(alphabet) > MAX_ACTIVITIES:
        raise EventLogWorkspaceError(f"the model shows {len(alphabet)} activities; footprints compare at most "
                                     f"{MAX_ACTIVITIES}. Lower the activity share.")
    keep = set(alphabet)

    def run(conn):
        return {"paths": conn.execute("SELECT list(activity ORDER BY ts, event_id) FROM ce GROUP BY case_id").fetchall()}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    pair_cases: Counter = Counter()
    starts: Counter = Counter()
    ends: Counter = Counter()
    cases = 0
    for (path,) in data.pop("paths"):
        trace = [a for a in path if a in keep]
        if not trace:
            continue
        cases += 1
        pair_cases.update(set(zip(trace, trace[1:])))
        starts[trace[0]] += 1
        ends[trace[-1]] += 1
    log_follows = set(pair_cases)
    model_follows = set(fp.seq)
    model_par = set(fp.par)

    matrix, differences = [], []
    agree = 0
    for a in alphabet:
        row = []
        for b in alphabet:
            in_log = _relation(a, b, log_follows)
            in_model = _relation(a, b, model_follows, model_par)
            row.append({"log": in_log, "model": in_model})
            if in_log == in_model:
                agree += 1
        matrix.append({"activity": a, "cells": row})
    # Directly-follows pairs one side has and the other does not, with how many cases took each.
    model_allows = model_follows | model_par
    for a, b in sorted(log_follows - model_allows):
        differences.append({"from": a, "to": b, "kind": "in_log_not_in_model", "cases": pair_cases[(a, b)],
                            "log": _relation(a, b, log_follows), "model": _relation(a, b, model_follows, model_par)})
    for a, b in sorted(model_allows - log_follows):
        if a == b and (a, b) in model_par:
            continue
        differences.append({"from": a, "to": b, "kind": "in_model_not_in_log", "cases": 0,
                            "log": _relation(a, b, log_follows), "model": _relation(a, b, model_follows, model_par)})
    differences.sort(key=lambda d: (d["kind"] != "in_log_not_in_model", -d["cases"], d["from"], d["to"]))
    cells = len(alphabet) ** 2
    log_only_cases = sum(d["cases"] for d in differences if d["kind"] == "in_log_not_in_model")
    start_diff = sorted(set(starts) - set(fp.start))
    end_diff = sorted(set(ends) - set(fp.end))
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": model.get("case_notion"),
        "cases": cases, "activities": alphabet,
        "settings": model["settings"],
        "process_model_sha256": model.get("process_model_sha256"),
        "conformance": {
            "cells": cells, "agreeing_cells": agree,
            "footprint_fitness": round(agree / cells, 4) if cells else None,
            "log_pairs": len(log_follows), "model_pairs": len(model_allows),
            "log_pairs_not_in_model": sum(1 for d in differences if d["kind"] == "in_log_not_in_model"),
            "model_pairs_not_in_log": sum(1 for d in differences if d["kind"] == "in_model_not_in_log"),
            "case_pair_occurrences_not_in_model": log_only_cases,
        },
        "start_activities": {"log": sorted(starts), "model": sorted(fp.start), "log_only": start_diff},
        "end_activities": {"log": sorted(ends), "model": sorted(fp.end), "log_only": end_diff},
        "differences": differences[:TOP_DIFFERENCES], "differences_total": len(differences),
        "matrix": matrix,
        "legend": {CAUSAL: "row is directly followed by column, never the reverse",
                   REVERSE: "column is directly followed by row, never the reverse",
                   PARALLEL: "both orders occur (or the model runs them in parallel)",
                   NONE: "neither directly follows the other"},
        "method": ("footprint matrices from directly-follows pairs in the recorded cases and from the discovered "
                   "process tree, compared cell by cell over the activities the model shows"),
        "caveat": ("Footprints compare ordering only, not frequency, timing or loop counts. A disagreement shows "
                   "where the draft model and the recorded cases differ, not which one is right."),
    }


__all__ = ["footprints", "FORMAT"]
