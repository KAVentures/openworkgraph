"""Finite synthetic decision-domain coverage for exact saved process designs.

This is exhaustive ONLY over an explicitly declared finite Cartesian product,
not arbitrary JSON values, policy authority or real-world input distributions.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from hashlib import sha256
from itertools import combinations, product
import json
import math
from typing import Any

from .process_decision_tables import evaluate_decisions, validate_date
from .process_model import ProcessModel
from .process_rule_conditions import validate_facts

MAX_DIMENSIONS = 8
MAX_CHOICES = 24
MAX_CASES = 2048
MAX_EXAMPLES = 6
MAX_NUMBER = 10**15


def _pointer(path):
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 240:
        raise ValueError("use bounded JSON pointers for dimensions")
    import re
    segments = path.split("/")[1:]
    if len(segments) > 8 or any(re.search(r"~(?![01])", x) for x in segments):
        raise ValueError("use bounded JSON pointers for dimensions")
    keys = tuple(x.replace("~1", "/").replace("~0", "~") for x in segments)
    if any(len(x) > 120 or any(ord(c) < 32 or 127 <= ord(c) <= 159 or
                                 0xD800 <= ord(c) <= 0xDFFF for c in x) for x in keys):
        raise ValueError("invalid dimension pointer segment")
    return keys


def _key(value):
    if type(value) in (int, float):
        return ("number", Decimal(str(value)))
    return (type(value).__name__, value)


def _choice_key(choice):
    return ("missing",) if choice == {"missing": True} else _key(choice["value"])


def _validate_choice(choice):
    if type(choice) is not dict:
        raise ValueError("each choice must be a value or a missing marker")
    if set(choice) == {"missing"} and choice["missing"] is True:
        return
    if set(choice) != {"value"} or type(choice["value"]) not in (str, int, float, bool, type(None)):
        raise ValueError("choices must be bounded JSON scalars or a missing marker")
    validate_facts({"value": choice["value"]})


def _parse(spec):
    if type(spec) is not dict or set(spec) != {"as_of_date", "dimensions"}:
        raise ValueError("send an explicit date and finite domain dimensions")
    date = validate_date(spec["as_of_date"])
    dimensions = spec["dimensions"]
    if type(dimensions) is not list or not 1 <= len(dimensions) <= MAX_DIMENSIONS:
        raise ValueError("choose 1 to 8 dimensions")
    paths, count = [], 1
    for dimension in dimensions:
        if type(dimension) is not dict or set(dimension) != {"path", "choices"}:
            raise ValueError("each dimension needs a path and choices")
        path = _pointer(dimension["path"])
        if any(path[:len(other)] == other or other[:len(path)] == path for other in paths):
            raise ValueError("dimension paths must be unique and non-nested")
        paths.append(path)
        choices = dimension["choices"]
        if type(choices) is not list or not 1 <= len(choices) <= MAX_CHOICES:
            raise ValueError("choose 1 to 24 values per dimension")
        for choice in choices:
            _validate_choice(choice)
        if len({_choice_key(choice) for choice in choices}) != len(choices):
            raise ValueError("duplicate equivalent choices in one dimension")
        count *= len(choices)
        if count > MAX_CASES:
            raise ValueError("finite domain exceeds 2048 combinations; narrow the input dimensions")
    return date, dimensions, paths, count


def _facts(dimensions, paths, indices):
    facts = {}
    for dimension, keys, index in zip(dimensions, paths, indices):
        choice = dimension["choices"][index]
        if "missing" in choice:
            continue
        node = facts
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = choice["value"]
    return facts


def _digest(spec):
    return sha256(json.dumps(spec, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def analyze(model: ProcessModel, spec: Any) -> dict[str, Any]:
    date, dimensions, paths, count = _parse(spec)
    if not model.decision_tables:
        raise ValueError("saved process has no decision tables")
    tables = {table.id: table for table in model.decision_tables}
    rules = {rule.id: rule for rule in model.business_rules}
    stats = {}
    for table in tables.values():
        stats[table.id] = {
            "statuses": Counter(), "examples": {},
            "rows": {row.id: {"matched": 0, "not_matched": 0, "unknown": 0,
                              "not_evaluated": 0, "selected": 0,
                              "shadowed_by_earlier_selection": 0, "examples": {}}
                     for row in table.rows},
            "matching_pairs": Counter(), "pair_examples": {},
        }

    def example(examples, key, indices):
        bucket = examples.setdefault(key, [])
        if len(bucket) < MAX_EXAMPLES:
            bucket.append(list(indices))

    for indices in product(*(range(len(d["choices"])) for d in dimensions)):
        facts = _facts(dimensions, paths, indices)
        checks = evaluate_decisions(model, facts, date)
        for check in checks:
            table = tables[check["table_id"]]
            acc = stats[table.id]
            status = check["status"]
            acc["statuses"][status] += 1
            example(acc["examples"], status, indices)
            row_results = {item["row_id"]: item["status"] for item in check["rows"]}
            selected = next((i for i, row in enumerate(table.rows)
                             if row.target_activity_id == check["selected_target_activity_id"]), None)
            matched_rows = []
            for i, row in enumerate(table.rows):
                row_stats = acc["rows"][row.id]
                row_status = row_results.get(row.id, "not_evaluated")
                row_stats[row_status] += 1
                if row_status == "matched":
                    matched_rows.append(row.id)
                    example(row_stats["examples"], "matched", indices)
                if selected == i:
                    row_stats["selected"] += 1
                    example(row_stats["examples"], "selected", indices)
                if (table.hit_policy == "FIRST" and selected is not None and
                        selected < i and row_status == "matched"):
                    row_stats["shadowed_by_earlier_selection"] += 1
                    example(row_stats["examples"], "shadowed", indices)
            for pair in combinations(matched_rows, 2):
                acc["matching_pairs"][pair] += 1
                # One concrete witness for each pair, never full case payloads.
                acc["pair_examples"].setdefault(pair, list(indices))

    results = []
    provided = set(paths)
    for table in tables.values():
        acc = stats[table.id]
        injected = {_pointer(d.fact_path) for d in table.required_decisions}
        declared = {condition.path for row in table.rows
                    for condition in rules[row.rule_id].predicate.conditions}
        rows = []
        for row in table.rows:
            values = acc["rows"][row.id]
            evaluated = count - values["not_evaluated"]
            rows.append({
                "row_id": row.id, "rule_id": row.rule_id, "target_activity_id": row.target_activity_id,
                **values, "evaluated": evaluated,
                # Not selected is a finite-domain result; unknown inputs and
                # unevaluated cases must not be called proven unreachable.
                "never_matched_in_evaluated_cases": evaluated > 0 and values["matched"] == 0,
                "never_selected_in_declared_domain": values["selected"] == 0,
                "known_never_matches_in_domain": evaluated == count and values["unknown"] == 0 and
                                                 values["matched"] == 0,
                "fully_shadowed_when_matched_in_domain": (table.hit_policy == "FIRST" and
                    values["matched"] > 0 and values["matched"] == values["shadowed_by_earlier_selection"] and
                    values["unknown"] == 0 and evaluated == count),
            })
        pairs = [{"row_ids": list(pair), "both_matched_cases": n,
                  "witness_choice_indices": acc["pair_examples"][pair]}
                 for pair, n in sorted(acc["matching_pairs"].items(), key=lambda item: (-item[1], item[0]))]
        results.append({
            "table_id": table.id, "hit_policy": table.hit_policy,
            "required_decision_table_ids": [d.table_id for d in table.required_decisions],
            "undeclared_predicate_paths": sorted(p for p in declared
                if _pointer(p) not in provided and _pointer(p) not in injected),
            "status_counts": dict(sorted(acc["statuses"].items())),
            "status_examples": acc["examples"], "rows": rows, "co_matching_pairs": pairs,
            "all_cases_selected": sum(acc["statuses"][s] for s in ("unique_match", "first_match")) == count,
        })
    return {
        "format": "openworkgraph.decision-domain-coverage.v1",
        "as_of_date": date, "domain_sha256": _digest(spec),
        "dimensions": [{"path": d["path"], "choice_count": len(d["choices"])} for d in dimensions],
        "combinations": count, "enumerated_combinations": count,
        "complete_declared_finite_domain": True,
        "arbitrary_input_domain_coverage_proven": False,
        "case_distribution_measured": False,
        "witness_index_basis": "zero_based_choice_indices_in_original_dimension_order",
        "tables": results, "input_values_retained": False,
        "facts_authoritativeness_verified": False,
        "policy_compliance_verified": False, "execution_authorized": False,
    }


def analyze_saved(packet, spec):
    model = ProcessModel.model_validate(packet["process_model"])
    return {"reference": {k: packet[k] for k in ("workflow_id", "revision", "content_sha256")},
            **analyze(model, spec)}


def suggested_domain(model: ProcessModel) -> dict[str, Any]:
    """Create editable boundary examples from existing declared constants only.

    Never call these an exhaustive partition; floats, omitted dimensions,
    dependencies and nested objects may introduce additional behaviors.
    """
    rules = {r.id: r for r in model.business_rules}
    injected = {_pointer(d.fact_path) for t in model.decision_tables for d in t.required_decisions}
    values = {}
    for table in model.decision_tables:
        for row in table.rows:
            for condition in rules[row.rule_id].predicate.conditions:
                choices = values.setdefault(condition.path, [])
                if condition.operator in ("exists", "not_exists"):
                    continue
                constants = condition.expected if isinstance(condition.expected, list) else [condition.expected]
                for value in constants:
                    choices.append(value)
                    if type(value) in (int, float):
                        choices.extend([candidate for candidate in
                                        (math.nextafter(float(value), -math.inf),
                                         math.nextafter(float(value), math.inf))
                                        if abs(candidate) <= MAX_NUMBER])
    dimensions, omitted, clipped = [], [], []
    keys = []
    for path, constants in sorted(values.items()):
        key = _pointer(path)
        if any(key[:len(p)] == p or p[:len(key)] == key for p in injected | set(keys)):
            omitted.append({"path": path, "reason": "overlapping_or_injected_path"})
            continue
        if len(dimensions) >= MAX_DIMENSIONS:
            omitted.append({"path": path, "reason": "dimension_limit"})
            continue
        distinct = {}
        for value in [None, False, True, *constants, 0, "OWG synthetic other"]:
            distinct.setdefault(_key(value), value)
        candidates = list(distinct.values())
        if len(candidates) >= MAX_CHOICES:
            clipped.append(path)
        choices = [{"missing": True}] + [{"value": v} for v in candidates[:MAX_CHOICES-1]]
        dimensions.append({"path": path, "choices": choices})
        keys.append(key)
    size = math.prod(len(d["choices"]) for d in dimensions)
    return {"format": "openworkgraph.decision-domain-suggestions.v1",
            "dimensions": dimensions, "omitted_paths": omitted, "clipped_paths": clipped,
            "suggested_combinations": size if dimensions else 0, "max_combinations": MAX_CASES,
            "review_and_explicit_date_required": True,
            "arbitrary_input_domain_coverage_proven": False, "execution_authorized": False}
