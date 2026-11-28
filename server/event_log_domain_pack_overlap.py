"""Exact overlap of installed case-rule violations within one object type.

Rules use the existing compliance engine. Case identifiers are never joined
across object types. Unknown outcomes are not passes; no causal claims.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

from . import event_log_domain_pack_benchmark as benchmark
from . import event_log_domain_packs as packs
from . import event_log_domain_pack_population as population
from . import event_log_compliance as compliance
from . import event_log_workspace as workspace
from . import event_log_activity_mappings as mappings
from . import event_log_hidden_activities as hidden
from . import process_discovery_provenance as provenance

FORMAT = "openworkgraph.domain-pack-overlap.v1"
MAX_SOURCE_ROWS = 500_000
MAX_OUTCOME_ROWS = 2_000_000
MAX_RULES = 12
_GUARD = benchmark._GUARD


def _fail(message):
    raise workspace.EventLogWorkspaceError(message)


def _parse(spec: Any):
    if not isinstance(spec, dict) or set(spec) not in (
            {"case_type", "rule_indices"}, {"case_type", "rule_indices", "expected_basis_sha256", "evidence"}):
        _fail("Choose an object type and two to twelve mapped rule indices")
    kind, indices = spec["case_type"], spec["rule_indices"]
    if not isinstance(kind, str) or not 1 <= len(kind) <= 200:
        _fail("Invalid object type")
    if not isinstance(indices, list) or not 2 <= len(indices) <= MAX_RULES or any(
            type(i) is not int or i < 0 for i in indices) or len(set(indices)) != len(indices):
        _fail("Choose two to twelve distinct mapped rule indices")
    evidence = spec.get("evidence")
    if "evidence" in spec:
        pin = spec["expected_basis_sha256"]
        if not isinstance(pin, str) or len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
            _fail("Invalid reviewed overlap digest")
        if not isinstance(evidence, dict) or set(evidence) != {"rule_indices", "mode", "after", "limit"}:
            _fail("Invalid overlap evidence selection")
        chosen = evidence["rule_indices"]
        if not isinstance(chosen, list) or not chosen or any(type(i) is not int or i not in indices for i in chosen) or len(set(chosen)) != len(chosen):
            _fail("Evidence rules must be distinct reviewed indices")
        if evidence["mode"] not in ("all_violated", "any_violated", "possible_all"):
            _fail("Invalid overlap evidence mode")
        after, limit = evidence["after"], evidence["limit"]
        if after is not None and (not isinstance(after, str) or not 1 <= len(after) <= 200):
            _fail("Invalid case cursor")
        if type(limit) is not int or not 1 <= limit <= 100:
            _fail("Invalid evidence page size")
    return kind, sorted(indices), evidence


def _basis(log_id, pack_id, kind, indices):
    conn = workspace._connect()
    try:
        workspace._check_log(conn, log_id)
        record = packs._record(conn, log_id, pack_id)
        if record is None:
            _fail("Install a domain pack before reviewing compliance overlap")
        pack = packs._pack(pack_id, record["version"])
        resolved = packs.resolve(pack, record["mapping"], record["parameters"])
        if packs._digest(resolved) != record["definitions_sha256"]:
            _fail("Installed definitions changed; reinstall before review")
        if mappings.applied_mapping(conn, log_id) is not None or hidden.hidden_names(conn, log_id):
            _fail("Clear applied activity mappings and hidden activities before overlap review")
        packs._check_names(conn, log_id, packs.Mapping.model_validate(record["mapping"]))
        omitted = {x["index"] for x in resolved["not_applicable"] if x["section"] == "compliance"}
        slots = [i for i in range(len(pack["compliance"])) if i not in omitted]
        if len(slots) != len(resolved["compliance"]):
            _fail("Resolved rule indices cannot be aligned")
        choices = {i: item for i, item in zip(slots, resolved["compliance"]) if item["case_type"] == kind}
        if any(i not in choices for i in indices):
            _fail("All selected rules must be mapped and belong to the same object type")
        rows = provenance.source_rows(conn, log_id)
        if rows > MAX_SOURCE_ROWS:
            _fail("Retained source exceeds the complete overlap source-row budget")
        basis = {"format": FORMAT, "log_id": log_id, "pack_id": pack_id,
                 "version": record["version"], "installation_token": packs._installation_token(record),
                 "definitions_sha256": record["definitions_sha256"], "case_type": kind,
                 "rules": [{"index": i, "rule": choices[i]["rule"]} for i in indices],
                 "population": population.counts(conn, log_id, kind),
                 "source_rows": rows, "source_sha256": provenance.source_digest(conn, log_id),
                 "source_fingerprint_basis": "stored_events_objects_and_event_object_links"}
        return basis
    finally:
        conn.close()


def available(log_id, pack_id):
    """List installed, mapped canonical rule indices by exact object type."""
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            record = packs._record(conn, log_id, pack_id)
            if record is None:
                _fail("Install a domain pack before reviewing compliance overlap")
            pack = packs._pack(pack_id, record["version"])
            resolved = packs.resolve(pack, record["mapping"], record["parameters"])
            if packs._digest(resolved) != record["definitions_sha256"]:
                _fail("Installed definitions changed; reinstall before review")
            omitted = {x["index"] for x in resolved["not_applicable"] if x["section"] == "compliance"}
            slots = [i for i in range(len(pack["compliance"])) if i not in omitted]
            if len(slots) != len(resolved["compliance"]):
                _fail("Resolved rule indices cannot be aligned")
            groups = {}
            for i, item in zip(slots, resolved["compliance"]):
                groups.setdefault(item["case_type"], []).append({"index": i, "rule": item["rule"]})
            return {"pack_id": pack_id, "version": record["version"],
                    "groups": [{"case_type": kind, "rules": rules} for kind, rules in sorted(groups.items())
                               if len(rules) >= 2], "max_rules": MAX_RULES}
        finally:
            conn.close()


def _materialize(conn, basis):
    compliance.positions(conn)
    n = conn.execute("SELECT count(DISTINCT case_id) FROM cp").fetchone()[0]
    population.require_count(basis["population"], n)
    if n * len(basis["rules"]) > MAX_OUTCOME_ROWS:
        _fail("Selected rule/case population exceeds the complete outcome-row budget")
    conn.execute("CREATE OR REPLACE TEMP TABLE ov AS SELECT DISTINCT case_id, 0::BIGINT AS violated, 0::BIGINT AS unknown, 0::BIGINT AS activated FROM cp")
    for pos, entry in enumerate(basis["rules"]):
        rule = compliance.parse({"case_type": basis["case_type"], "rules": [entry["rule"]]}).rules[0]
        compliance._materialize_outcomes(conn, rule)
        bit = 1 << pos
        # The existing engine returns exactly one outcome per projected case.
        # Fail closed if its contract changes, rather than silently lose rows.
        count, distinct = conn.execute("SELECT count(*), count(DISTINCT case_id) FROM co").fetchone()
        matched = conn.execute("SELECT count(*) FROM co JOIN ov USING(case_id)").fetchone()[0]
        if count != n or distinct != n or matched != n:
            _fail("Compliance outcome population does not match the reviewed cases")
        conn.execute("""UPDATE ov SET
            violated=ov.violated | CASE WHEN co.violated THEN ? ELSE 0 END,
            unknown=ov.unknown | CASE WHEN co.unknown THEN ? ELSE 0 END,
            activated=ov.activated | CASE WHEN co.activated THEN ? ELSE 0 END
            FROM co WHERE ov.case_id=co.case_id""", [bit, bit, bit])
    inconsistent = conn.execute("SELECT count(*) FROM ov WHERE (violated & unknown)<>0 OR ((violated | unknown) & activated)<>(violated | unknown)").fetchone()[0]
    if inconsistent:
        _fail("Compliance outcome states are inconsistent")
    return n


def _summarize(conn, basis, n):
    ids = [r["index"] for r in basis["rules"]]
    patterns = conn.execute("SELECT violated,unknown,activated,count(*) FROM ov GROUP BY 1,2,3 ORDER BY 1,2,3").fetchall()
    # Aggregate masks once. At most n patterns are present; do not export
    # every pattern or case. Histograms are complete, not top-N approximations.
    singles = [{"rule_index": i, "activated": 0, "violated": 0, "unknown": 0, "satisfied": 0, "nonactivated": 0} for i in ids]
    pairs = [{"left_rule_index": ids[a], "right_rule_index": ids[b], "both_violated": 0,
              "possible_both_violated": 0, "either_violated": 0, "possible_either_violated": 0}
             for a, b in combinations(range(len(ids)), 2)]
    pair_masks = [(1 << a) | (1 << b) for a,b in combinations(range(len(ids)), 2)]
    histogram = [{"violated_rule_count": k, "cases": 0} for k in range(len(ids)+1)]
    any_known = any_possible = 0
    for v,u,a,count in patterns:
        possible = v | u
        histogram[v.bit_count()]["cases"] += count
        any_known += count if v else 0
        any_possible += count if possible else 0
        for pos, row in enumerate(singles):
            bit = 1 << pos
            row["activated"] += count if a & bit else 0
            row["violated"] += count if v & bit else 0
            row["unknown"] += count if u & bit else 0
            row["satisfied"] += count if a & bit and not possible & bit else 0
            row["nonactivated"] += count if not a & bit else 0
        for mask, row in zip(pair_masks, pairs):
            row["both_violated"] += count if v & mask == mask else 0
            row["possible_both_violated"] += count if possible & mask == mask else 0
            row["either_violated"] += count if v & mask else 0
            row["possible_either_violated"] += count if possible & mask else 0
    for row in pairs:
        # Jaccard uses confirmed violations only; no overlap of an empty
        # union is interpreted as perfect agreement.
        row["confirmed_jaccard"] = row["both_violated"]/row["either_violated"] if row["either_violated"] else None
    return {"cases": n, "unique_cases_with_confirmed_violation": any_known,
            "unique_cases_with_confirmed_or_unknown_violation": any_possible,
            "confirmed_violation_count_histogram": histogram, "rules": singles, "pairs": pairs,
            "rule_violation_instances": sum(r["violated"] for r in singles)}


def _page(conn, basis, evidence):
    ids = [r["index"] for r in basis["rules"]]
    mask = sum(1 << ids.index(i) for i in evidence["rule_indices"])
    mode = evidence["mode"]
    expr, args = {"all_violated": ("(violated & ?) = ?", [mask,mask]),
                  "any_violated": ("(violated & ?) <> 0", [mask]),
                  "possible_all": ("((violated | unknown) & ?) = ?", [mask,mask])}[mode]
    total = conn.execute("SELECT count(*) FROM ov WHERE "+expr,args).fetchone()[0]
    after = evidence["after"]
    suffix = " AND case_id>?" if after is not None else ""
    rows = conn.execute("SELECT case_id,violated,unknown,activated FROM ov WHERE "+expr+suffix+
                        " ORDER BY case_id LIMIT ?", [*args,*([after] if after is not None else []),evidence["limit"]+1]).fetchall()
    page = rows[:evidence["limit"]]
    return {"total": total, "mode": mode, "selected_rule_indices": sorted(evidence["rule_indices"]),
            "cases": [{"case_id": key, "violated_rule_indices": [i for pos,i in enumerate(ids) if v & (1<<pos)],
                       "unknown_rule_indices": [i for pos,i in enumerate(ids) if u & (1<<pos)],
                       "activated_rule_indices": [i for pos,i in enumerate(ids) if a & (1<<pos)]}
                      for key,v,u,a in page], "next_after": page[-1][0] if len(rows)>len(page) else None}


def review(log_id, pack_id, spec):
    kind, indices, evidence = _parse(spec)
    if not _GUARD.acquire(blocking=False):
        _fail("Another domain pack analysis is running")
    try:
        with workspace._LOCK:
            basis = _basis(log_id, pack_id, kind, indices)
            pin = provenance.digest(basis)
            if evidence is not None and pin != spec["expected_basis_sha256"]:
                _fail("The reviewed installation or retained source changed; review overlap again")
            def run(conn):
                n = _materialize(conn, basis)
                return {"result": _page(conn,basis,evidence) if evidence is not None else _summarize(conn,basis,n)}
            result = workspace._query(log_id, kind, run)["result"]
            return {"format": FORMAT, "basis": basis, "basis_sha256": pin, **result,
                    "configuration_modified": False, "source_system_validated": False,
                    "policy_compliance_verified": False, "execution_authorized": False,
                    "interpretation": "Counts describe retained cases of one object type. Unknown outcomes are possible violations, not verified failures or passes. Overlap is descriptive, not causal. Do not combine counts across object types or infer business loss."}
    finally:
        _GUARD.release()
