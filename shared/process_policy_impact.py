"""Exact saved-design policy change and potential downstream impact review.

IDs are scoped to one workflow. Reachability is graph-structural, not proof
of a changed route, business policy semantics, or actual execution impact.
"""
from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal
import json

from .process_model import ProcessModel

FORMAT = "openworkgraph.policy-revision-impact.v1"


def _scalar(value):
    if type(value) in (int, float):
        return ("number", Decimal(str(value)))
    return (type(value).__name__, value)


def _condition(condition):
    expected = condition.expected
    # The declared evaluator treats numeric representations equivalently,
    # membership order/duplicates as irrelevant, and bool separately.
    key = (tuple(sorted(set(_scalar(v) for v in expected), key=repr))
           if isinstance(expected, list) else _scalar(expected))
    return (condition.path, condition.operator, key)


def _predicate(rule):
    if rule.predicate is None:
        return None
    return (rule.predicate.match,
            frozenset(_condition(c) for c in rule.predicate.conditions))


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _changed(left, right, fields):
    return [field for field in fields if _json(left.get(field)) != _json(right.get(field))]


def _reach(starts, adjacency):
    """Shortest witness path from any starting ID, deterministic ties."""
    paths = {node:[node] for node in sorted(set(starts))}
    queue = deque(paths)
    while queue:
        node = queue.popleft()
        for nxt in sorted(adjacency.get(node, ())):
            if nxt not in paths:
                paths[nxt] = [*paths[node], nxt]
                queue.append(nxt)
    return paths


def _index(model):
    rules = {r.id:r for r in model.business_rules}
    tables = {t.id:t for t in model.decision_tables}
    edges = {(e.source,e.target):e.rule_id for e in model.transitions}
    following, required_by = defaultdict(set), defaultdict(set)
    rule_edges, rule_tables = defaultdict(set), defaultdict(set)
    for edge in model.transitions:
        following[edge.source].add(edge.target)
        if edge.rule_id:
            rule_edges[edge.rule_id].add((edge.source,edge.target))
    for table in model.decision_tables:
        for row in table.rows:
            rule_tables[row.rule_id].add(table.id)
        for required in table.required_decisions:
            required_by[required.table_id].add(table.id)
    return rules,tables,edges,following,required_by,rule_edges,rule_tables


def _rule_changes(a,b):
    changes = []
    for ident in sorted(a.keys() | b.keys()):
        old,new = a.get(ident),b.get(ident)
        if old is None or new is None:
            fields = ["rule_added" if old is None else "rule_removed"]
        else:
            fields = []
            if old.text != new.text:
                fields.append("text_changed")
            if old.user_confirmed != new.user_confirmed:
                fields.append("confirmation_changed")
            if _predicate(old) != _predicate(new):
                fields.append("structured_predicate_changed")
            elif _json(old.model_dump().get("predicate")) != _json(new.model_dump().get("predicate")):
                fields.append("predicate_representation_changed")
            if old.library_ref != new.library_ref:
                fields.append("library_reference_changed")
        if fields:
            changes.append({"rule_id":ident,"changes":fields,
                "old_fact_paths":sorted({c.path for c in old.predicate.conditions}) if old and old.predicate else [],
                "new_fact_paths":sorted({c.path for c in new.predicate.conditions}) if new and new.predicate else [],
                "old_confirmed":old.user_confirmed if old else None,
                "new_confirmed":new.user_confirmed if new else None})
    return changes


def _table_changes(a,b):
    changes = []
    for ident in sorted(a.keys() | b.keys()):
        old,new = a.get(ident),b.get(ident)
        if old is None or new is None:
            fields = ["table_added" if old is None else "table_removed"]
        else:
            fields = _changed(old.model_dump(),new.model_dump(),
                ("decision_activity_id","hit_policy","effective_from","effective_until",
                 "source_basis","user_confirmed"))
            old_rows = {r.id:r.model_dump() for r in old.rows}
            new_rows = {r.id:r.model_dump() for r in new.rows}
            if old_rows != new_rows:
                fields.append("row_membership_or_binding_changed")
            if [r.id for r in old.rows] != [r.id for r in new.rows]:
                fields.append("first_row_order_changed" if "FIRST" in (old.hit_policy,new.hit_policy)
                              else "unique_row_order_changed")
            if {r.table_id:r.fact_path for r in old.required_decisions} != {
                    r.table_id:r.fact_path for r in new.required_decisions}:
                fields.append("required_decision_binding_changed")
            elif [r.table_id for r in old.required_decisions] != [r.table_id for r in new.required_decisions]:
                fields.append("dependency_order_changed")
        if fields:
            changes.append({"table_id":ident,"changes":fields,
                "old_activity_id":old.decision_activity_id if old else None,
                "new_activity_id":new.decision_activity_id if new else None})
    return changes


def _policy_entries(model):
    manual = {(g.activity_id,g.kind,g.condition_label.casefold()):g.model_dump()
              for g in model.manual_review_conditions}
    outcomes = {(e.terminal_activity_id,e.operation):e.model_dump()
                for e in model.outcome_expectations}
    return manual,outcomes


def _entry_changes(a,b):
    return [{"identity":list(key),"change":"added" if key not in a else "removed" if key not in b else "modified"}
            for key in sorted(a.keys() | b.keys()) if a.get(key)!=b.get(key)]


def _impact(model,index,rule_ids,table_ids,edge_changes,manual,outcomes):
    rules,tables,edges,following,required_by,rule_edges,rule_tables = index
    direct_tables = set(table_ids) & tables.keys()
    guarded_edges = set()
    starts = set()
    for rid in rule_ids:
        direct_tables.update(rule_tables.get(rid,()))
        guarded_edges.update(rule_edges.get(rid,()))
    for row in edge_changes:
        pair = tuple(row["edge"])
        if pair in edges:
            guarded_edges.add(pair)
            # If a decision table exists at this activity it is potentially affected.
            direct_tables.update(t.id for t in tables.values() if t.decision_activity_id==pair[0])
    table_paths = _reach(direct_tables,required_by)
    for ident in table_paths:
        table = tables[ident]
        starts.add(table.decision_activity_id)
        starts.update(r.target_activity_id for r in table.rows)
    starts.update(node for edge in guarded_edges for node in edge)
    known = {a.id for a in model.activities}
    starts.update(row["identity"][0] for row in [*manual,*outcomes] if row["identity"][0] in known)
    activity_paths = _reach(starts,following)
    return {"direct_guarded_edges":[list(pair) for pair in sorted(guarded_edges)],
            "direct_decision_table_ids":sorted(direct_tables),
            "decision_dependency_witnesses":[{"table_id":k,"path":v} for k,v in sorted(table_paths.items())],
            "downstream_activity_witnesses":[{"activity_id":k,"path":v} for k,v in sorted(activity_paths.items())],
            "potentially_affected_terminal_ids":sorted(set(model.terminal_activity_ids) & activity_paths.keys()),
            "unreferenced_changed_rule_ids":sorted(r for r in rule_ids if r in rules and
                                                  not rule_edges.get(r) and not rule_tables.get(r))}


def compare(before,after):
    if before["workflow_id"] != after["workflow_id"] or before["revision"] == after["revision"]:
        raise ValueError("choose two different revisions of the same workflow")
    old,new = (ProcessModel.model_validate(p["process_model"]) for p in (before,after))
    a,b = _index(old),_index(new)
    rules = _rule_changes(a[0],b[0])
    tables = _table_changes(a[1],b[1])
    edges = []
    for pair in sorted(a[2].keys() | b[2].keys()):
        if a[2].get(pair)!=b[2].get(pair) or (pair in a[2])!=(pair in b[2]):
            # Unconditional edge changes are also structural changes, not rule edits.
            edges.append({"edge":list(pair),"old_exists":pair in a[2],"new_exists":pair in b[2],
                          "old_rule_id":a[2].get(pair),"new_rule_id":b[2].get(pair)})
    old_manual,old_outcomes = _policy_entries(old)
    new_manual,new_outcomes = _policy_entries(new)
    manual = _entry_changes(old_manual,new_manual)
    outcomes = _entry_changes(old_outcomes,new_outcomes)
    rule_ids = {r["rule_id"] for r in rules}
    table_ids = {t["table_id"] for t in tables}
    references = lambda p:{k:p[k] for k in ("workflow_id","revision","content_sha256")}
    return {"format":FORMAT,"before":references(before),"after":references(after),
            "rule_changes":rules,"decision_table_changes":tables,"transition_changes":edges,
            "manual_review_changes":manual,"outcome_expectation_changes":outcomes,
            "before_impact":_impact(old,a,rule_ids,table_ids,edges,manual,outcomes),
            "after_impact":_impact(new,b,rule_ids,table_ids,edges,manual,outcomes),
            "analysis_complete_for_declared_graph":True,
            "comparison_basis":"stable_ids_within_one_saved_workflow",
            "reachability_basis":"potential_graph_reachability_not_executable_paths",
            "predicate_comparison_basis":"normalized_declared_conditions_not_general_logical_equivalence",
            "text_change_meaning_verified":False,"policy_equivalence_proven":False,
            "source_state_verified":False,"execution_authorized":False,
            "business_outcomes_verified":False,"review_approval_transferred":False}
