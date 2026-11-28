"""Local model mutation testing: synthetic faults, never edits to saved models.

All mutants are derived independently from the original strict P/T net. A
surviving mutant with an exact language counterexample supplies a candidate
regression test. Completed-language equivalence is not prefix equivalence.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from . import event_log_petri_acceptance as acceptance
from . import event_log_petri_language as language
from . import event_log_petri_soundness as soundness
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.petri_mutation_review.v1"
MAX_MUTANTS = 8
OPERATORS = ("hide_activity", "rename_activity", "remove_transition", "increase_input_weight")


def _settings(spec):
    if not isinstance(spec, dict) or set(spec) != {"suite", "settings"}:
        raise EventLogWorkspaceError("send suite and mutation settings")
    suite, digest = acceptance.parse_suite(spec["suite"])
    settings = spec["settings"]
    if not isinstance(settings, dict) or set(settings) - {"max_mutants", "operators"}:
        raise EventLogWorkspaceError("unknown mutation settings")
    count = settings.get("max_mutants", MAX_MUTANTS)
    ops = settings.get("operators", list(OPERATORS))
    if type(count) is not int or not 1 <= count <= MAX_MUTANTS:
        raise EventLogWorkspaceError("max_mutants must be an integer from 1 to 8")
    if (not isinstance(ops, list) or not ops or any(not isinstance(x, str) or x not in OPERATORS for x in ops)
            or len(set(ops)) != len(ops)):
        raise EventLogWorkspaceError("choose distinct supported mutation operators")
    return suite, digest, {"max_mutants": count, "operators": [op for op in OPERATORS if op in ops]}


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _semantic(root, name):
    # The strict validator rejects semantic objects hidden inside annotations.
    return [e for e in root.iter() if _local(e.tag) == name]


def _new_label(labels):
    label = "OWG synthetic unexpected activity"
    i = 1
    while label in labels:
        i += 1
        label = f"OWG synthetic unexpected activity {i}"
    return label


def _recipes(data, net, settings):
    root = ET.fromstring(data)
    labels = {x for x in net["transitions"].values() if x is not None}
    visible = sorted(t for t, x in net["transitions"].items() if x is not None)
    groups = {
        "hide_activity": [{"operator": "hide_activity", "transition_id": t,
                           "old_label": net["transitions"][t]} for t in visible],
        "rename_activity": [{"operator": "rename_activity", "transition_id": t,
                             "old_label": net["transitions"][t], "new_label": _new_label(labels)}
                            for t in visible],
        "remove_transition": [{"operator": "remove_transition", "transition_id": t,
                               "old_label": net["transitions"][t]}
                              for t in sorted(net["transitions"]) if len(net["transitions"]) > 1],
        "increase_input_weight": [],
    }
    for arc in sorted(_semantic(root, "arc"), key=lambda a: a.get("id")):
        if arc.get("source") not in net["places"] or arc.get("target") not in net["transitions"]:
            continue
        inscription = soundness.replay._child(arc, "inscription")
        old = max(1, soundness.replay._count(soundness.replay._text(inscription), "arc weight", 1))
        if old < soundness.replay.MAX_WEIGHT:
            groups["increase_input_weight"].append({"operator": "increase_input_weight",
                "arc_id": arc.get("id"), "transition_id": arc.get("target"),
                "old_weight": old, "new_weight": old + 1})
    ops = settings["operators"]
    available = {op: len(groups[op]) for op in ops}
    chosen = []
    # Round robin across enabled fault categories, deterministic by identifier.
    for i in range(settings["max_mutants"]):
        for op in ops:
            if i < len(groups[op]):
                chosen.append(groups[op][i])
                if len(chosen) == settings["max_mutants"]:
                    return chosen, available
    return chosen, available


def _mutate(data, recipe):
    root = ET.fromstring(data)
    parents = {c: p for p in root.iter() for c in p}
    op = recipe["operator"]
    target = next(t for t in _semantic(root, "transition") if t.get("id") == recipe["transition_id"])

    def tag(element, local):
        return element.tag.rsplit("}", 1)[0] + "}" + local if "}" in element.tag else local

    if op == "hide_activity":
        for child in list(target):
            if _local(child.tag) == "name":
                target.remove(child)
    elif op == "rename_activity":
        name = soundness.replay._child(target, "name")
        if name is None:
            name = ET.SubElement(target, tag(target, "name"))
        # Replace the label annotation; graphics do not define firing semantics.
        for child in list(name):
            name.remove(child)
        name.text = None
        ET.SubElement(name, tag(name, "text")).text = recipe["new_label"]
    elif op == "remove_transition":
        for arc in _semantic(root, "arc"):
            if recipe["transition_id"] in (arc.get("source"), arc.get("target")):
                parents[arc].remove(arc)
        parents[target].remove(target)
    elif op == "increase_input_weight":
        arc = next(a for a in _semantic(root, "arc") if a.get("id") == recipe["arc_id"])
        inscription = soundness.replay._child(arc, "inscription")
        if inscription is None:
            inscription = ET.SubElement(arc, tag(arc, "inscription"))
        for child in list(inscription):
            inscription.remove(child)
        inscription.text = None
        ET.SubElement(inscription, tag(inscription, "text")).text = str(recipe["new_weight"])
    return ET.tostring(root, encoding="utf-8")


def review(data: bytes, spec):
    suite, digest, settings = _settings(spec)
    net = soundness._strict_net(data)
    baseline_graph = language._graph(data)
    baseline = acceptance._review_graph(baseline_graph, suite)
    result = {"format": FORMAT, "suite": suite, "suite_sha256": digest, "settings": settings,
              "baseline": baseline, "mutants": [], "available_by_operator": {},
              "counts": None, "detected_fraction_bounds": None,
              "caveat": "Synthetic, independently applied model faults. Detection rate is not real-world defect probability or exhaustive fault coverage. Completed-language equivalence does not imply equivalent prefixes or other model properties."}
    if baseline["suite_passed"] is not True:
        return {**result, "status": "baseline_not_passed"}
    recipes, available = _recipes(data, net, settings)
    rows = []
    for i, recipe in enumerate(recipes):
        row = {"id": f"mutation-{i+1:02d}", "recipe": recipe}
        try:
            mutated = _mutate(data, recipe)
            graph = language._graph(mutated)
            report = acceptance._review_graph(graph, suite)
        except EventLogWorkspaceError as exc:
            rows.append({**row, "status": "inconclusive", "reason": str(exc),
                         "pnml_sha256": None, "failed_case_ids": [], "suggested_cases": []})
            continue
        failed = [c["id"] for c in report["cases"] if c["status"] == "failed"]
        unknown = [c["id"] for c in report["cases"] if c["status"] == "inconclusive"]
        status = "detected" if failed else ("inconclusive" if unknown else "survived")
        row.update(status=status, pnml_sha256=graph["pnml_sha256"],
                   failed_case_ids=failed, inconclusive_case_ids=unknown,
                   model=report["model"], work_limit_reached=report["work_limit_reached"],
                   language_comparison=None, suggested_cases=[])
        if status == "survived":
            comparison = language._compare_graphs(baseline_graph, graph)
            row["language_comparison"] = {k: comparison[k] for k in (
                "equivalent", "complete", "limit", "removed_trace", "added_trace")}
            for key, expected in (("removed_trace", True), ("added_trace", False)):
                witness = comparison[key]
                if witness is None or witness["truncated"] or len(witness["activities"]) > acceptance.MAX_TRACE:
                    continue
                try:
                    trace = acceptance._labels(witness["activities"], acceptance.MAX_TRACE)
                except EventLogWorkspaceError:
                    continue
                row["suggested_cases"].append({"id": f"{row['id']}-{key}",
                                               "trace": trace, "expect": {"completed": expected}})
        rows.append(row)
    counts = {k: sum(row["status"] == k for row in rows) for k in ("detected", "survived", "inconclusive")}
    bounds = None if not rows else {"lower": counts["detected"] / len(rows),
                                   "upper": (counts["detected"] + counts["inconclusive"]) / len(rows)}
    return {**result, "status": "reviewed" if rows else "no_applicable_mutations",
            "mutants": rows, "available_by_operator": available, "counts": counts,
            "selected_mutants": len(rows), "available_mutants": sum(available.values()),
            "selection_truncated": len(rows) < sum(available.values()),
            "detected_fraction_bounds": bounds,
            "survivors_with_proven_language_difference": sum(
                row.get("language_comparison", {}).get("equivalent") is False
                for row in rows if row.get("language_comparison") is not None)}
