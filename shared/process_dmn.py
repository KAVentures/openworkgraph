"""DMN 1.4 decision-table interchange for OWG decision tables (#370 R21).

OWG decision tables route an exclusive decision by structured predicates over case
facts. This module writes them as DMN decision tables and reads DMN decision tables
back, limited to the subset that maps exactly in both directions:

- inputs are simple FEEL paths (``amount``, ``customer.tier``), which become OWG
  JSON pointers (``/amount``, ``/customer/tier``);
- input entries are FEEL simple unary tests that match OWG operators: ``-`` (any),
  a literal (``5``, ``"gold"``, ``true``), ``> 5``, ``>= 5``, ``< 5``, ``<= 5``,
  ``not(5)``, a list ``"a","b"``, ``not("a","b")``, ``null`` and ``not(null)``;
- there is exactly one output column, whose entry is the label of the target step
  as a string literal;
- the hit policy is UNIQUE or FIRST.

Anything else (full FEEL, ranges, functions, several outputs, other hit policies)
is refused with a fixed message, never approximated. DMN is read as untrusted data:
no DOCTYPE or entities, bounded size, and imported rules and tables are always
unconfirmed. Nothing here evaluates or executes a decision.
"""
from __future__ import annotations

import json
import re
from typing import Any
from xml.etree import ElementTree as ET

from .process_model import ProcessModel
from .process_rule_conditions import RuleCondition, RulePredicate

DMN = "https://www.omg.org/spec/DMN/20191111/MODEL/"
DMN_NAMESPACES = {DMN, "http://www.omg.org/spec/DMN/20180521/MODEL/", "https://www.omg.org/spec/DMN/20230324/MODEL/"}
MAX_BYTES = 256 * 1024
_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,6})?$")
_STRING = re.compile(r'^"((?:[^"\\]|\\["\\])*)"$')


class DMNError(ValueError):
    """Fixed safe error; never echoes DMN content."""


# ---------------------------------------------------------------- literals

def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    return json.dumps(value, ensure_ascii=False)


def _parse_literal(text: str) -> Any:
    text = text.strip()
    if text in ("true", "false"):
        return text == "true"
    if text == "null":
        return None
    if _NUMBER.fullmatch(text):
        return float(text) if "." in text else int(text)
    match = _STRING.fullmatch(text)
    if match:
        return match.group(1).replace('\\"', '"').replace("\\\\", "\\")
    raise DMNError("Use only FEEL literals: numbers, quoted strings, true, false or null")


def _split_list(text: str) -> list[str]:
    parts, current, quoted, escaped = [], "", False, False
    for char in text:
        if escaped:
            current += char
            escaped = False
        elif char == "\\" and quoted:
            current += char
            escaped = True
        elif char == '"':
            current += char
            quoted = not quoted
        elif char == "," and not quoted:
            parts.append(current)
            current = ""
        else:
            current += char
    if quoted:
        raise DMNError("Unterminated string in a decision-table entry")
    parts.append(current)
    return [p.strip() for p in parts]


# ---------------------------------------------------------------- conditions

def _pointer_to_path(pointer: str) -> str:
    segments = pointer.split("/")[1:]
    if not segments or any(not _SEGMENT.fullmatch(s) for s in segments):
        raise DMNError("Only simple fact paths (letters, digits, underscores) can be written as DMN inputs")
    return ".".join(segments)


def _path_to_pointer(path: str) -> str:
    segments = path.strip().split(".")
    if not 1 <= len(segments) <= 8 or any(not _SEGMENT.fullmatch(s) for s in segments):
        raise DMNError("DMN input expressions must be simple paths such as amount or customer.tier")
    return "/" + "/".join(segments)


def condition_to_test(condition: RuleCondition) -> str:
    op, value = condition.operator, condition.expected
    if op == "eq":
        return _literal(value)
    if op == "ne":
        return f"not({_literal(value)})"
    if op in {"gt", "gte", "lt", "lte"}:
        return {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op] + " " + _literal(value)
    if op == "in":
        return ",".join(_literal(v) for v in value)
    if op == "not_in":
        return "not(" + ",".join(_literal(v) for v in value) + ")"
    if op == "exists":
        return "not(null)"
    return "null"  # not_exists


def unary_test_to_condition(path: str, text: str) -> RuleCondition | None:
    """One FEEL simple unary test as an OWG condition; ``-`` means any value."""
    text = " ".join(text.split())
    if text in ("", "-"):
        return None
    if len(text) > 2000:
        raise DMNError("A decision-table entry is too long")
    pointer = _path_to_pointer(path)
    if text == "null":
        return RuleCondition(path=pointer, operator="not_exists")
    if text == "not(null)":
        return RuleCondition(path=pointer, operator="exists")
    comparison = re.fullmatch(r"(<=|>=|<|>)\s*(.+)", text)
    if comparison:
        value = _parse_literal(comparison.group(2))
        if type(value) not in (int, float):
            raise DMNError("Comparisons in decision-table entries need a number")
        op = {"<": "lt", "<=": "lte", ">": "gt", ">=": "gte"}[comparison.group(1)]
        return RuleCondition(path=pointer, operator=op, expected=value)
    negated = re.fullmatch(r"not\((.+)\)", text)
    body = negated.group(1) if negated else text
    values = [_parse_literal(part) for part in _split_list(body)]
    if len(values) == 1:
        return RuleCondition(path=pointer, operator="ne" if negated else "eq", expected=values[0])
    if len(values) > 20:
        raise DMNError("Decision-table lists may hold at most 20 values")
    return RuleCondition(path=pointer, operator="not_in" if negated else "in", expected=values)


# ---------------------------------------------------------------- export

def _sub(parent, tag, **attrs):
    return ET.SubElement(parent, "{" + DMN + "}" + tag, {k: str(v) for k, v in attrs.items()})


def export_dmn(packet: dict[str, Any]) -> str:
    """All decision tables of one saved revision as one DMN document."""
    model = ProcessModel.model_validate(packet["process_model"])
    if not model.decision_tables:
        raise DMNError("This revision has no decision tables to export")
    if any(t.required_decisions for t in model.decision_tables):
        # OWG passes the selected step ID; DMN outputs here are step labels.
        raise DMNError("Decision dependencies cannot be written as DMN required decisions exactly; export without them")
    rules = {r.id: r for r in model.business_rules}
    labels = {a.id: a.label for a in model.activities}
    ET.register_namespace("", DMN)
    root = ET.Element("{" + DMN + "}definitions", {
        "id": "Definitions_OWG", "name": "OpenWorkGraph decisions",
        "namespace": "https://openworkgraph.local/process/" + str(packet["workflow_id"])})
    _sub(root, "documentation").text = (
        f"Exported from reviewed design {packet['workflow_id']} revision {packet['revision']} "
        f"sha256 {packet['content_sha256']}. Declarations only; not policy certification or execution authority.")
    for table in model.decision_tables:
        decision = _sub(root, "decision", id="Decision_" + table.id, name=labels[table.decision_activity_id])
        basis = table.source_basis
        _sub(decision, "description").text = (
            f"Effective from {table.effective_from}" + (f" until {table.effective_until} (exclusive)" if table.effective_until else "")
            + f". Source: {basis.label} (sha256 {basis.document_sha256}). Confirmed in OWG: {str(table.user_confirmed).lower()}.")
        dmn_table = _sub(decision, "decisionTable", id="Table_" + table.id, hitPolicy=table.hit_policy)
        paths: list[str] = []
        per_row: list[dict[str, str]] = []
        for row in table.rows:
            predicate = rules[row.rule_id].predicate
            if predicate is None:
                raise DMNError("Every exported row needs a structured rule")
            if predicate.match == "any" and len(predicate.conditions) > 1:
                raise DMNError("Rows that match ANY of several conditions cannot be written as one DMN rule")
            entries: dict[str, str] = {}
            for condition in predicate.conditions:
                path = _pointer_to_path(condition.path)
                if path in entries:
                    raise DMNError("A row with two conditions on the same fact cannot be written as one DMN entry")
                entries[path] = condition_to_test(condition)
                if path not in paths:
                    paths.append(path)
            per_row.append(entries)
        for index, path in enumerate(paths, 1):
            column = _sub(dmn_table, "input", id=f"Input_{table.id}_{index}", label=path)
            expression = _sub(column, "inputExpression", id=f"InputExpression_{table.id}_{index}", typeRef="Any")
            _sub(expression, "text").text = path
        _sub(dmn_table, "output", id=f"Output_{table.id}", name="next_step", label="Next step", typeRef="string")
        for row, entries in zip(table.rows, per_row):
            rule = _sub(dmn_table, "rule", id="Rule_" + row.id)
            _sub(rule, "description").text = rules[row.rule_id].text[:500]
            for index, path in enumerate(paths, 1):
                entry = _sub(rule, "inputEntry", id=f"InputEntry_{row.id}_{index}")
                _sub(entry, "text").text = entries.get(path, "-")
            output = _sub(rule, "outputEntry", id=f"OutputEntry_{row.id}")
            _sub(output, "text").text = _literal(labels[row.target_activity_id])
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------- import

def _local(element) -> tuple[str, str]:
    namespace, _, name = element.tag[1:].partition("}") if element.tag.startswith("{") else ("", "", element.tag)
    return namespace, name


def _child(element, name):
    return [c for c in element if _local(c)[1] == name]


def _text_of(element, name="text") -> str:
    nodes = _child(element, name)
    return (nodes[0].text or "") if nodes else ""


def import_dmn(raw: bytes, model_value: dict[str, Any], decision_activity_id: str, *,
               effective_from: str, effective_until: str | None = None,
               decision_name: str | None = None) -> dict[str, Any]:
    """Propose unconfirmed rules and a table for one OWG decision from one DMN decision table."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise DMNError("DMN must be a nonempty UTF-8 file of at most 256 KiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise DMNError("DMN must use UTF-8 encoding") from None
    if "\x00" in text or re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.I):
        raise DMNError("DMN declarations, entities and control bytes are not supported")
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        raise DMNError("DMN XML could not be parsed") from None
    if sum(1 for _ in root.iter()) > 5000:
        raise DMNError("DMN exceeds the 5,000 XML-element limit")
    namespace, name = _local(root)
    if name != "definitions" or namespace not in DMN_NAMESPACES:
        raise DMNError("A DMN 1.2–1.5 definitions document is required")
    model = ProcessModel.model_validate(model_value)
    activities = {a.id: a for a in model.activities}
    decision_node = activities.get(decision_activity_id)
    if decision_node is None or decision_node.kind != "decision":
        raise DMNError("Choose a decision step of the saved design")
    targets = {}
    for edge in model.transitions:
        if edge.source == decision_activity_id:
            targets.setdefault(activities[edge.target].label, edge.target)
    decisions = [d for d in _child(root, "decision") if _child(d, "decisionTable")]
    if decision_name is not None:
        decisions = [d for d in decisions if d.get("name") == decision_name]
    if len(decisions) != 1:
        raise DMNError("Select exactly one DMN decision with a decision table")
    table = _child(decisions[0], "decisionTable")[0]
    hit = table.get("hitPolicy", "UNIQUE")
    if hit not in ("UNIQUE", "FIRST"):
        raise DMNError("Only UNIQUE and FIRST hit policies map onto OWG decision tables")
    if table.get("aggregation") or table.get("preferredOrientation") not in (None, "Rule-as-Row"):
        raise DMNError("Aggregations and non-row table orientations are unsupported")
    inputs = _child(table, "input")
    outputs = _child(table, "output")
    if len(outputs) != 1:
        raise DMNError("A decision table needs exactly one output naming the next step")
    paths = []
    for column in inputs:
        expressions = _child(column, "inputExpression")
        if len(expressions) != 1:
            raise DMNError("Every input needs one input expression")
        paths.append(_text_of(expressions[0]).strip())
    rows, rules, seen_targets = [], [], set()
    for index, rule in enumerate(_child(table, "rule"), 1):
        entries = _child(rule, "inputEntry")
        out = _child(rule, "outputEntry")
        if len(entries) != len(paths) or len(out) != 1:
            raise DMNError("Every rule needs one entry per input and one output entry")
        target_label = _parse_literal(_text_of(out[0]))
        if not isinstance(target_label, str) or target_label not in targets:
            raise DMNError("Every output entry must name one of the decision's next steps by its label")
        target = targets[target_label]
        if target in seen_targets:
            raise DMNError("Each next step may appear in only one rule, as in OWG decision tables")
        seen_targets.add(target)
        conditions = [c for c in (unary_test_to_condition(p, _text_of(e)) for p, e in zip(paths, entries)) if c]
        if not conditions:
            raise DMNError("A rule that matches every case cannot be a structured OWG rule")
        if len(conditions) > 8:
            raise DMNError("OWG rules hold at most eight conditions")
        description = " ".join(_text_of(rule, "description").split())[:500]
        rule_id = f"dmn_{re.sub(r'[^A-Za-z0-9_-]', '_', decision_activity_id)[:40]}_r{index}"
        if any(r.id == rule_id for r in model.business_rules):
            rule_id += "_imported"
        predicate = RulePredicate(match="all", conditions=conditions)
        rules.append({"id": rule_id, "text": description or f"DMN rule {index} for {target_label}",
                      "user_confirmed": False, "predicate": predicate.model_dump()})
        rows.append({"id": f"dmn_row_{index}", "rule_id": rule_id, "target_activity_id": target})
    if set(targets.values()) != seen_targets:
        raise DMNError("The DMN rules must cover every next step of the decision exactly once")
    import hashlib
    from .process_decision_tables import validate_date
    try:
        validate_date(effective_from)
        if effective_until is not None:
            validate_date(effective_until)
    except ValueError:
        raise DMNError("Enter effective dates as YYYY-MM-DD") from None
    proposed = model.model_dump()
    by_target = {row["target_activity_id"]: row["rule_id"] for row in rows}
    replaced = {e["rule_id"] for e in proposed["transitions"] if e["source"] == decision_activity_id and e.get("rule_id")}
    for edge in proposed["transitions"]:
        if edge["source"] == decision_activity_id:
            edge["rule_id"] = by_target[edge["target"]]
    still_used = {e.get("rule_id") for e in proposed["transitions"]}
    proposed["business_rules"] = [r for r in proposed["business_rules"]
                                  if r["id"] not in replaced or r["id"] in still_used] + rules
    label = ("DMN " + (decisions[0].get("name") or "decision"))[:120]
    proposed["decision_tables"] = [t for t in proposed.get("decision_tables", [])
                                   if t["decision_activity_id"] != decision_activity_id] + [{
        "id": "dmn_table_" + re.sub(r"[^A-Za-z0-9_-]", "_", decision_activity_id)[:50],
        "decision_activity_id": decision_activity_id, "hit_policy": hit,
        "effective_from": effective_from, "effective_until": effective_until,
        "source_basis": {"label": label, "document_sha256": hashlib.sha256(raw).hexdigest(),
                         "basis": "human_declared_document_digest", "independently_verified": False},
        "rows": rows, "user_confirmed": False, "execution_authorized": False}]
    try:
        proposed = ProcessModel.model_validate(proposed).model_dump()
    except ValueError:
        raise DMNError("The imported table does not fit this decision; check its inputs and next steps") from None
    return {
        "format": "openworkgraph.dmn-import-preview.v1",
        "proposed_process_model": proposed,
        "decision_activity_id": decision_activity_id,
        "hit_policy": hit,
        "proposed_rules": rules,
        "proposed_table_rows": rows,
        "warnings": ["dmn_is_untrusted_data", "imported_rules_and_table_are_unconfirmed",
                     "add_effective_dates_and_source_basis_before_saving"],
        "process_model_saved": False, "execution_authorized": False,
    }


__all__ = ["DMNError", "condition_to_test", "export_dmn", "import_dmn", "unary_test_to_condition"]
