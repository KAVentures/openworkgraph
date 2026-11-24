"""Model quality: how well the discovered net fits, generalizes and stays simple, at each noise level.

Process mining judges a model on four dimensions (Buijs, van Dongen and van der Aalst, 2012; PM4Py's
``evaluation`` module; ProM's model quality plug-ins):

* **fitness**: can the net replay the recorded cases (token-based replay, as in the Petri net check);
* **precision**: does it allow little that no case did (ETConformance escaping edges);
* **generalization**: is each part of the net backed by many observations, so it would likely fit
  future cases too: ``1 - mean over transitions of 1/sqrt(times fired)``, with a transition never
  fired counting as 1, as in PM4Py;
* **simplicity**: how tangled the net is, from the mean number of arcs per node:
  ``1 / (1 + max(0, mean degree - 2))``, as in PM4Py's arc-degree simplicity.

The noise setting of model discovery trades these off: noise 0 keeps every observed path (high
fitness, often low precision and simplicity), higher noise drops rare paths. This module discovers
the net at several noise levels and scores each, so the slider can be chosen from evidence rather
than by eye. The F-score is the harmonic mean of fitness and precision. The scores describe the
model against this log; none of them says which model is right.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from typing import Any

from . import event_log_petri_replay as petri
from . import event_log_pnml as pnml
from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_model_quality.v1"
DEFAULT_LEVELS = (0.0, 0.1, 0.2, 0.3, 0.5)
MAX_LEVELS = 8


def _levels(raw: Any) -> list[float]:
    if raw is None:
        return list(DEFAULT_LEVELS)
    values = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    try:
        levels = sorted({round(float(v), 4) for v in values if str(v).strip() != ""})
    except (TypeError, ValueError):
        raise EventLogWorkspaceError("noise levels must be numbers from 0 up to 1") from None
    if not levels or len(levels) > MAX_LEVELS or any(not 0 <= v < 1 for v in levels):
        raise EventLogWorkspaceError(f"give 1 to {MAX_LEVELS} noise levels from 0 up to (not including) 1")
    return levels


def _generalization(net: dict[str, Any], fired: Counter) -> float:
    transitions = list(net["transitions"])
    if not transitions:
        return 0.0
    total = sum((1 / fired[t] ** 0.5) if fired[t] else 1.0 for t in transitions)
    return 1 - total / len(transitions)


def _simplicity(net: dict[str, Any]) -> tuple[float, float]:
    degree: Counter = Counter()
    for t in net["transitions"]:
        for p, _ in net["pre"][t].items():
            degree[p] += 1
            degree[t] += 1
        for p, _ in net["post"][t].items():
            degree[p] += 1
            degree[t] += 1
    nodes = len(net["places"]) + len(net["transitions"])
    mean = sum(degree.values()) / nodes if nodes else 0.0
    return 1 / (1 + max(0.0, mean - 2)), mean


def _score(xml: str, rows) -> dict[str, Any]:
    net = petri.parse_pnml(xml.encode())
    by_label: dict[str, list[str]] = {}
    for t, label in sorted(net["transitions"].items()):
        if label is not None:
            by_label.setdefault(label, []).append(t)
    totals: Counter = Counter()
    fired: Counter = Counter()
    fitting = cases = 0
    for path, n in rows:
        trace = tuple(path)
        result = petri._replay(net, trace, by_label)
        cases += n
        fitting += n if result["fits"] else 0
        for key in ("missing", "remaining", "consumed", "produced"):
            totals[key] += result[key] * n
        for t, k in result["fired"].items():
            fired[t] += k * n
    c, p = totals["consumed"], totals["produced"]
    fitness = 0.5 * (1 - totals["missing"] / c if c else 1) + 0.5 * (1 - totals["remaining"] / p if p else 1)
    precision = petri._precision(net, rows, by_label)["precision"]
    simplicity, mean_degree = _simplicity(net)
    f_score = (2 * fitness * precision / (fitness + precision)) if precision is not None and fitness + precision else None
    return {"places": len(net["places"]), "transitions": len(net["transitions"]),
            "silent_transitions": sum(1 for v in net["transitions"].values() if v is None),
            "activities": len(by_label),
            "fitness": round(fitness, 4), "fitting_case_share": round(fitting / cases, 4) if cases else None,
            "precision": precision, "f_score": round(f_score, 4) if f_score is not None else None,
            "generalization": round(_generalization(net, fired), 4),
            "simplicity": round(simplicity, 4), "mean_arc_degree": round(mean_degree, 3),
            "transitions_never_fired": sum(1 for t in net["transitions"] if not fired[t])}


def model_quality(log_id: str, case_type: str | None = None, *, levels: Any = None, activities: float = 1.0,
                  cohort: dict[str, Any] | None = None, include_nets: bool = False) -> dict[str, Any]:
    chosen = _levels(levels)
    if isinstance(activities, bool) or not isinstance(activities, (int, float)) or not 0 < activities <= 1:
        raise EventLogWorkspaceError("activities must be a share above 0 and at most 1")

    def run(conn):
        rows = conn.execute("""SELECT path, count(*) AS cases FROM (
              SELECT list(activity ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id HAVING count(*) <= ?)
            GROUP BY path ORDER BY cases DESC, path LIMIT ?""", [petri.MAX_TRACE_LENGTH, petri.MAX_VARIANTS]).fetchall()
        total = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        return {"rows": rows, "cases_total": total}

    # Discovery and trace selection read the same workspace state. Replay uses
    # only materialized inputs, so it can run after releasing the lock.
    with workspace._LOCK:
        data = workspace._query_filtered(log_id, case_type, cohort, run)
        rows = data.pop("rows")
        if not rows:
            raise EventLogWorkspaceError("No cases are available to score in this selection")
        drafts = [(noise, *pnml.discovered_pnml(log_id, data["case_notion"], noise=noise,
                                               activities=activities, cohort=cohort)) for noise in chosen]
    out = []
    for noise, xml, meta in drafts:
        out.append({"noise": noise, **_score(xml, rows), "process_model_sha256": meta.get("process_model_sha256"),
                    **({"pnml": xml, "pnml_sha256": sha256(xml.encode()).hexdigest()} if include_nets else {})})
    scored = [r for r in out if r["f_score"] is not None]
    # Highest F-score; on a tie the simpler net, then the lower noise.
    best = max(scored, key=lambda r: (r["f_score"], r["simplicity"], -r["noise"]), default=None)
    cases = sum(n for _, n in rows)
    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        **({"cohort": data.get("cohort", {})} if include_nets else {}),
        "cases": cases, "cases_total": data.get("cases_total"),
        "cases_excluded": max(0, (data.get("cases_total") or cases) - cases),
        "variants": len(rows), "activities_share": float(activities),
        "levels": out, "best_f_score_noise": best["noise"] if best else None,
        "definitions": {
            "fitness": "token-replay fitness of the recorded cases on the discovered net",
            "precision": "1 - share of escaping edges (steps the net allows next that no case took)",
            "f_score": "harmonic mean of fitness and precision",
            "generalization": "1 - mean over transitions of 1/sqrt(times fired); never fired counts as 1",
            "simplicity": "1 / (1 + max(0, mean arcs per node - 2))",
        },
        "method": ("the inductive-miner draft of model discovery is converted to a Petri net at each noise level and "
                   "scored on fitness, precision, generalization and simplicity (Buijs et al., 2012), as PM4Py's "
                   "evaluation does"),
        "caveat": ("Scores compare drafts against this log. A higher F-score is a better fit-and-precision balance, "
                   "not proof that the model is right; generalization and simplicity are rough guides."),
    }


__all__ = ["model_quality", "FORMAT", "DEFAULT_LEVELS"]
