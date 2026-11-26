"""Exact bounded deadline sensitivity for installed domain packs.

Only declared response_within parameters are varied; all source records, role
mappings and other parameters are fixed. No pack configuration is modified.
"""
from __future__ import annotations

import math
from typing import Any

from . import event_log_domain_pack_benchmark as benchmark
from . import event_log_domain_packs as packs
from . import event_log_domain_pack_population as population
from . import event_log_workspace as workspace
from . import event_log_compliance as compliance
from . import event_log_activity_mappings as mappings
from . import event_log_hidden_activities as hidden
from . import process_discovery_provenance as provenance

FORMAT = "openworkgraph.domain-pack-deadline-sensitivity.v1"
MAX_SOURCE_ROWS = 500_000
MAX_RULES = 8
MAX_VALUES = 8
MAX_EVIDENCE = 100
FRONTIER_TARGETS = (50, 80, 90, 95, 99, 100)
_GUARD = benchmark._GUARD
_STATES = {"newly_satisfied", "newly_violating", "missing_response", "late_complete"}


def _fail(message):
    raise workspace.EventLogWorkspaceError(message)


def _number(value):
    if type(value) not in (int, float):
        _fail("Deadline values must be finite numbers")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        _fail("Deadline values must be finite numbers")
    if not math.isfinite(number):
        _fail("Deadline values must be finite numbers")
    return number


def _parse(spec: Any):
    if not isinstance(spec, dict) or set(spec) not in (
            {"parameter", "values"}, {"parameter", "values", "evidence", "expected_basis_sha256"}):
        _fail("Choose one deadline parameter and a list of alternative values")
    name = spec["parameter"]
    values = spec["values"]
    if not isinstance(name, str) or not 1 <= len(name) <= 40:
        _fail("Invalid deadline parameter")
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_VALUES:
        _fail("Choose one to eight alternative deadline values")
    values = [_number(v) for v in values]
    if len(set(values)) != len(values):
        _fail("Alternative deadline values must be distinct")
    evidence = spec.get("evidence")
    if "evidence" in spec:
        pin = spec["expected_basis_sha256"]
        if not isinstance(pin, str) or len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
            _fail("Invalid reviewed sensitivity basis")
        if not isinstance(evidence, dict) or set(evidence) != {"rule_index", "value", "state", "after", "limit"}:
            _fail("Invalid evidence page request")
        if type(evidence["rule_index"]) is not int or evidence["rule_index"] < 0:
            _fail("Invalid evidence rule index")
        if not isinstance(evidence["state"], str) or evidence["state"] not in _STATES:
            _fail("Invalid evidence classification")
        if _number(evidence["value"]) not in values:
            _fail("Evidence deadline must be one of the reviewed alternatives")
        after, limit = evidence["after"], evidence["limit"]
        if after is not None and (not isinstance(after, str) or not 1 <= len(after) <= 200):
            _fail("Invalid evidence cursor")
        if type(limit) is not int or not 1 <= limit <= MAX_EVIDENCE:
            _fail("Invalid evidence page size")
    return name, sorted(values), evidence


def _prepare(log_id, pack_id, name, values):
    conn = workspace._connect()
    try:
        workspace._check_log(conn, log_id)
        record = packs._record(conn, log_id, pack_id)
        if record is None:
            _fail("Install a domain pack before reviewing deadline sensitivity")
        pack = packs._pack(pack_id, record["version"])
        resolved = packs.resolve(pack, record["mapping"], record["parameters"])
        if packs._digest(resolved) != record["definitions_sha256"]:
            _fail("Installed definitions changed; reinstall before review")
        if mappings.applied_mapping(conn, log_id) is not None or hidden.hidden_names(conn, log_id):
            _fail("Clear applied activity mappings and hidden activities before deadline sensitivity review")
        packs._check_names(conn, log_id, packs.Mapping.model_validate(record["mapping"]))
        parameter = next((p for p in pack["parameters"] if p["name"] == name), None)
        if parameter is None:
            _fail("Unknown deadline parameter")
        if any(not parameter["min"] <= v <= parameter["max"] for v in values):
            _fail("Alternative values are outside the declared parameter range")
        omitted = {r["index"]: r["unmapped_role"] for r in resolved["not_applicable"]
                   if r["section"] == "compliance"}
        affected, omitted_rules = [], []
        slots = [i for i in range(len(pack["compliance"])) if i not in omitted]
        if len(slots) != len(resolved["compliance"]):
            _fail("Resolved rule indices cannot be aligned")
        by_index = dict(zip(slots, resolved["compliance"]))
        for index, item in enumerate(pack["compliance"]):
            rule = item["rule"]
            if rule.get("template") != "response_within" or rule.get("seconds") != "$" + name:
                continue
            if index in omitted:
                omitted_rules.append({"rule_index": index, "unmapped_role": omitted[index]})
            else:
                affected.append((index, by_index[index]))
        if not affected:
            _fail("No mapped response-within rules use this parameter")
        if len(affected) > MAX_RULES:
            _fail("Parameter exceeds the complete rule review budget")
        rows = provenance.source_rows(conn, log_id)
        if rows > MAX_SOURCE_ROWS:
            _fail("Retained log exceeds the complete deadline review source-row budget")
        baseline = float(record["parameters"][name])
        unit = parameter.get("unit", "days")
        factor = packs.UNIT_SECONDS[unit]
        baseline_seconds = int(round(baseline * factor))
        alternatives = [{"value": v, "seconds": int(round(v * factor))} for v in values]
        if len({r["seconds"] for r in alternatives}) != len(alternatives):
            _fail("Alternative deadlines must be distinct after conversion to whole seconds")
        for _, item in affected:
            if item["rule"]["seconds"] != baseline_seconds:
                _fail("Installed deadline does not match the declared parameter")
            for row in alternatives:
                compliance.parse({"case_type": item["case_type"],
                                  "rules": [{**item["rule"], "seconds": row["seconds"]}]})
        basis = {"format": FORMAT, "log_id": log_id, "pack_id": pack_id,
                 "version": record["version"], "installation_token": packs._installation_token(record),
                 "definitions_sha256": record["definitions_sha256"], "parameter": name,
                 "parameter_unit": unit, "baseline_value": baseline, "baseline_seconds": baseline_seconds,
                 "alternatives": alternatives, "source_rows": rows,
                 "parameter_min_seconds": max(1, int(round(parameter["min"] * factor))),
                 "parameter_max_seconds": min(10**9, int(round(parameter["max"] * factor))),
                 "frontier_target_percentages": list(FRONTIER_TARGETS),
                 "source_sha256": provenance.source_digest(conn, log_id),
                 "source_fingerprint_basis": "stored_events_objects_and_event_object_links",
                 "omitted_rules": omitted_rules,
                 "case_populations": {kind: population.counts(conn, log_id, kind)
                                      for kind in sorted({item["case_type"] for _, item in affected})}}
        return basis, affected
    finally:
        conn.close()


def _profile(conn, a, b):
    # Equivalent to response_within's earliest later B for every A.
    # A forward window avoids materializing a cross-product of occurrences.
    # Positions use the compliance engine's timestamp/event-ID order.
    compliance.positions(conn)
    conn.execute("""CREATE OR REPLACE TEMP TABLE deadline_profile AS
        WITH next_step AS (
          SELECT case_id, activity, ts,
            min(ts) FILTER (WHERE activity = ?) OVER (
              PARTITION BY case_id ORDER BY pos
              ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) AS next_b
          FROM cp
        )
        SELECT case_id, count(*) FILTER (WHERE activity = ?) AS triggers,
          count(*) FILTER (WHERE activity = ? AND next_b IS NULL) AS missing,
          max(epoch(next_b-ts)) FILTER (WHERE activity = ?) AS worst_seconds
        FROM next_step GROUP BY case_id""", [b, a, a, a])


def _counts(conn, baseline, seconds):
    cases, activated, missing, late, base_late, improved, worsened = conn.execute("""
        SELECT count(*), count(*) FILTER (WHERE triggers>0),
          count(*) FILTER (WHERE missing>0),
          count(*) FILTER (WHERE triggers>0 AND missing=0 AND worst_seconds>?),
          count(*) FILTER (WHERE triggers>0 AND missing=0 AND worst_seconds>?),
          count(*) FILTER (WHERE triggers>0 AND missing=0 AND worst_seconds>? AND worst_seconds<=?),
          count(*) FILTER (WHERE triggers>0 AND missing=0 AND worst_seconds<=? AND worst_seconds>?)
        FROM deadline_profile""", [seconds, baseline, baseline, seconds, baseline, seconds]).fetchone()
    violated = missing + late
    return {"cases": cases, "activated_cases": activated, "nonactivated_cases": cases-activated,
            "missing_response_cases": missing, "late_complete_response_cases": late,
            "satisfied_cases": activated-violated, "violating_cases": violated,
            "violation_rate": violated/activated if activated else None,
            "baseline_violating_cases": missing+base_late,
            "newly_satisfied_cases": improved, "newly_violating_cases": worsened,
            "violation_count_change": worsened-improved}


def _frontier(conn, lower, upper):
    """Minimum whole-second thresholds per fixed observed target, not a policy proposal."""
    activated, completed, achievable = conn.execute("""
        SELECT count(*) FILTER (WHERE triggers>0),
          count(*) FILTER (WHERE triggers>0 AND missing=0),
          count(*) FILTER (WHERE triggers>0 AND missing=0 AND worst_seconds<=?)
        FROM deadline_profile""", [upper]).fetchone()
    targets = [(percent, (activated*percent+99)//100) for percent in FRONTIER_TARGETS]
    # Group ties before accumulating: reaching a threshold admits every case
    # at that exact integer-second boundary. Never count partially answered cases.
    needed = [n for _, n in targets]
    expressions = ", ".join("min(seconds) FILTER (WHERE cumulative>=?)" for _ in needed)
    first = conn.execute("""
        WITH bins AS (
          SELECT CAST(ceil(worst_seconds) AS BIGINT) AS seconds, count(*) AS cases
          FROM deadline_profile WHERE triggers>0 AND missing=0 GROUP BY 1
        ), ranked AS (
          SELECT seconds, sum(cases) OVER (ORDER BY seconds ROWS UNBOUNDED PRECEDING) AS cumulative
          FROM bins
        )
        SELECT """+expressions+" FROM ranked", needed).fetchone()
    results = []
    for (percent, required), seconds in zip(targets, first):
        if not activated:
            status = "no_activated_cases"
        elif required > completed:
            status = "unattainable_missing_responses"
        elif required > achievable:
            status = "outside_declared_range"
        elif seconds is None:
            _fail("Deadline frontier does not match the observed case population")
        else:
            status = "attainable"
        threshold = max(lower, seconds) if status == "attainable" else None
        if threshold is not None and threshold > upper:
            _fail("Deadline frontier exceeds the declared range")
        results.append({"target_percent":percent,"required_satisfied_cases":required,
                        "status":status,"minimum_seconds":threshold})
    return {"activated_cases":activated,"fully_responded_cases":completed,
            "missing_response_cases":activated-completed,
            "maximum_satisfied_cases_in_range":achievable,
            "minimum_allowed_seconds":lower,"maximum_allowed_seconds":upper,
            "targets":results,"policy_recommendation":False}


def _page(conn, baseline, seconds, spec):
    predicate, params = {
        "newly_satisfied": ("triggers>0 AND missing=0 AND worst_seconds>? AND worst_seconds<=?", [baseline, seconds]),
        "newly_violating": ("triggers>0 AND missing=0 AND worst_seconds<=? AND worst_seconds>?", [baseline, seconds]),
        "missing_response": ("missing>0", []),
        "late_complete": ("triggers>0 AND missing=0 AND worst_seconds>?", [seconds]),
    }[spec["state"]]
    total = conn.execute("SELECT count(*) FROM deadline_profile WHERE " + predicate, params).fetchone()[0]
    after = spec["after"]
    suffix = " AND case_id>?" if after is not None else ""
    rows = conn.execute("SELECT case_id,triggers,missing,worst_seconds FROM deadline_profile WHERE "
                        + predicate + suffix + " ORDER BY case_id LIMIT ?",
                        [*params, *([after] if after is not None else []), spec["limit"]+1]).fetchall()
    page = rows[:spec["limit"]]
    return {"classification": spec["state"], "total": total,
            "cases": [{"case_id": key, "trigger_occurrences": triggers,
                       "unanswered_occurrences": missing,
                       "worst_observed_response_seconds": worst} for key,triggers,missing,worst in page],
            "next_after": page[-1][0] if len(rows)>len(page) else None}


def review(log_id, pack_id, spec):
    name, values, evidence = _parse(spec)
    if not _GUARD.acquire(blocking=False):
        _fail("Another domain pack deadline review is running")
    try:
        with workspace._LOCK:
            basis, rules = _prepare(log_id, pack_id, name, values)
            pin = provenance.digest(basis)
            baseline = basis["baseline_seconds"]
            if evidence is not None:
                if spec["expected_basis_sha256"] != pin:
                    _fail("The reviewed installation or retained source changed; rerun deadline sensitivity")
                selected = next(((i,item) for i,item in rules if i==evidence["rule_index"]), None)
                if selected is None:
                    _fail("Evidence rule is not part of this review")
                index, item = selected
                row = next(r for r in basis["alternatives"] if r["value"]==evidence["value"])
                rule = item["rule"]
                def run(conn):
                    _profile(conn, rule["a"], rule["b"])
                    population.require_count(basis["case_populations"][item["case_type"]],
                                             conn.execute("SELECT count(*) FROM deadline_profile").fetchone()[0])
                    return {"page": _page(conn, baseline, row["seconds"], evidence)}
                page = workspace._query(log_id, item["case_type"], run)["page"]
                return {"format": FORMAT, "basis_sha256": pin, "rule_index": index,
                        "value": row["value"], "seconds": row["seconds"], **page,
                        "population": basis["case_populations"][item["case_type"]],
                        "resource_names_returned": False, "execution_authorized": False}
            results = []
            for index, item in rules:
                rule = item["rule"]
                def run(conn):
                    _profile(conn, rule["a"], rule["b"])
                    population.require_count(basis["case_populations"][item["case_type"]],
                                             conn.execute("SELECT count(*) FROM deadline_profile").fetchone()[0])
                    return {"baseline": _counts(conn, baseline, baseline),
                            "frontier": _frontier(conn, basis["parameter_min_seconds"], basis["parameter_max_seconds"]),
                            "alternatives": [{**row, **_counts(conn, baseline, row["seconds"])}
                                             for row in basis["alternatives"]]}
                output = workspace._query(log_id, item["case_type"], run)
                results.append({"rule_index": index, "case_type": item["case_type"],
                                "rule": rule, "population": basis["case_populations"][item["case_type"]], "baseline": output["baseline"],
                                "alternatives": output["alternatives"], "frontier": output["frontier"]})
            return {"format": FORMAT, "basis": basis, "basis_sha256": pin, "rules": results,
                    "configuration_modified": False, "source_system_validated": False,
                    "policy_compliance_verified": False, "execution_authorized": False,
                    "interpretation": "Descriptive sensitivity on one fixed retained population. Missing later responses remain violations at every tested threshold; incomplete logs may explain them. No recommendation to weaken policy or claim business improvement."}
    finally:
        _GUARD.release()
