"""Read-only, user-weighted case triage for installed domain packs.

Scores are declared review priorities, not risk probabilities, business losses,
staff ratings or permissions. Unknown rule outcomes contribute only to the
upper bound. Every case is evaluated within one exact object type.
"""
from __future__ import annotations

from typing import Any

from . import event_log_domain_pack_benchmark as benchmark
from . import event_log_domain_packs as packs
from . import event_log_workspace as workspace
from . import event_log_compliance as compliance
from . import event_log_activity_mappings as mappings
from . import event_log_hidden_activities as hidden
from . import process_discovery_provenance as provenance

FORMAT = "openworkgraph.domain-pack-priority.v1"
MAX_RULES = 12
MAX_SOURCE_ROWS = 500_000
MAX_OUTCOMES = 2_000_000
MAX_EXPORT_CASES = 5_000
_GUARD = benchmark._GUARD


def _fail(message):
    raise workspace.EventLogWorkspaceError(message)


def _parse(spec: Any):
    required = {"case_type", "weights", "minimum_confirmed", "include_uncertain", "limit"}
    if not isinstance(spec, dict) or set(spec) not in (required, required | {"expected_basis_sha256", "after"}):
        _fail("Invalid priority worklist request")
    kind = spec["case_type"]
    if not isinstance(kind, str) or not 1 <= len(kind) <= 200:
        _fail("Invalid case type")
    weights = spec["weights"]
    if not isinstance(weights, list) or not 1 <= len(weights) <= MAX_RULES:
        _fail("Choose one to twelve weighted rules")
    seen = set()
    for entry in weights:
        if not isinstance(entry, dict) or set(entry) != {"rule_index", "weight"}:
            _fail("Invalid rule weight entry")
        i, w = entry["rule_index"], entry["weight"]
        if type(i) is not int or i < 0 or i in seen or type(w) is not int or not 1 <= w <= 100:
            _fail("Rule indices must be distinct and weights integers from 1 to 100")
        seen.add(i)
    total = sum(e["weight"] for e in weights)
    minimum, limit = spec["minimum_confirmed"], spec["limit"]
    if type(minimum) is not int or not 1 <= minimum <= total or type(spec["include_uncertain"]) is not bool:
        _fail("Invalid score threshold or uncertainty selection")
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("Invalid worklist page size")
    after = spec.get("after")
    if "after" in spec:
        pin = spec["expected_basis_sha256"]
        if not isinstance(pin, str) or len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
            _fail("Invalid reviewed worklist digest")
        if not isinstance(after, dict) or set(after) != {"lower", "upper", "case_id"}:
            _fail("Invalid worklist cursor")
        lo, hi, key = after["lower"], after["upper"], after["case_id"]
        if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= total or not isinstance(key, str) or not 1 <= len(key) <= 200:
            _fail("Invalid worklist cursor")
    return kind, sorted(weights, key=lambda x:x["rule_index"]), after


def _configuration(conn, log_id, pack_id):
    workspace._check_log(conn, log_id)
    record = packs._record(conn, log_id, pack_id)
    if record is None:
        _fail("Install a domain pack before building a priority worklist")
    definition = packs._pack(pack_id, record["version"])
    resolved = packs.resolve(definition, record["mapping"], record["parameters"])
    if packs._digest(resolved) != record["definitions_sha256"]:
        _fail("Installed definitions changed; reinstall before review")
    omitted = {x["index"] for x in resolved["not_applicable"] if x["section"] == "compliance"}
    slots = [i for i in range(len(definition["compliance"])) if i not in omitted]
    if len(slots) != len(resolved["compliance"]):
        _fail("Resolved rule indices cannot be aligned")
    groups = {}
    for i, item in zip(slots, resolved["compliance"]):
        groups.setdefault(item["case_type"], []).append({"rule_index":i, "rule":item["rule"]})
    return record, groups


def available(log_id, pack_id):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            record, groups = _configuration(conn, log_id, pack_id)
            return {"pack_id":pack_id, "version":record["version"],
                    "groups":[{"case_type":k,"rules":v} for k,v in sorted(groups.items())],
                    "max_rules":MAX_RULES}
        finally:
            conn.close()


def _basis(log_id, pack_id, kind, weights, spec):
    conn = workspace._connect()
    try:
        record, groups = _configuration(conn, log_id, pack_id)
        if mappings.applied_mapping(conn, log_id) is not None or hidden.hidden_names(conn, log_id):
            _fail("Clear active label mapping and hiding before priority review")
        packs._check_names(conn, log_id, packs.Mapping.model_validate(record["mapping"]))
        rules = {x["rule_index"]:x["rule"] for x in groups.get(kind, [])}
        if any(x["rule_index"] not in rules for x in weights):
            _fail("Choose mapped rules from one exact case type")
        rows = provenance.source_rows(conn, log_id)
        if rows > MAX_SOURCE_ROWS:
            _fail("Retained source exceeds the complete priority source-row budget")
        return {"format":FORMAT, "log_id":log_id, "pack_id":pack_id, "version":record["version"],
                "installation_token":packs._installation_token(record),
                "definitions_sha256":record["definitions_sha256"], "case_type":kind,
                "rules":[{**x,"rule":rules[x["rule_index"]]} for x in weights],
                "minimum_confirmed":spec["minimum_confirmed"],"include_uncertain":spec["include_uncertain"],
                "source_rows":rows,"source_sha256":provenance.source_digest(conn, log_id)}
    finally:
        conn.close()


def _materialize(conn, basis):
    compliance.positions(conn)
    n = conn.execute("SELECT count(DISTINCT case_id) FROM cp").fetchone()[0]
    if n*len(basis["rules"]) > MAX_OUTCOMES:
        _fail("Case-rule population exceeds the complete priority outcome budget")
    conn.execute("""CREATE OR REPLACE TEMP TABLE priority_cases AS
        SELECT DISTINCT case_id, 0::BIGINT AS lo, 0::BIGINT AS hi,
          0::BIGINT AS violations, 0::BIGINT AS unknowns FROM cp""")
    for pos, entry in enumerate(basis["rules"]):
        rule = compliance.parse({"case_type":basis["case_type"],"rules":[entry["rule"]]}).rules[0]
        compliance._materialize_outcomes(conn, rule)
        count, distinct = conn.execute("SELECT count(*), count(DISTINCT case_id) FROM co").fetchone()
        matched = conn.execute("SELECT count(*) FROM co JOIN priority_cases USING(case_id)").fetchone()[0]
        inconsistent = conn.execute("""SELECT count(*) FROM co WHERE
            (co.violated AND co.unknown) OR ((co.violated OR co.unknown) AND NOT co.activated)""").fetchone()[0]
        if count != n or distinct != n or matched != n or inconsistent:
            _fail("Compliance outcome population is inconsistent")
        w, bit = entry["weight"], 1 << pos
        conn.execute("""UPDATE priority_cases SET
            lo=priority_cases.lo+CASE WHEN co.violated THEN ? ELSE 0 END,
            hi=priority_cases.hi+CASE WHEN co.violated OR co.unknown THEN ? ELSE 0 END,
            violations=priority_cases.violations | CASE WHEN co.violated THEN ? ELSE 0 END,
            unknowns=priority_cases.unknowns | CASE WHEN co.unknown THEN ? ELSE 0 END
            FROM co WHERE priority_cases.case_id=co.case_id""", [w,w,bit,bit])
    return n


def _page(conn, basis, after, limit):
    minimum = basis["minimum_confirmed"]
    total, definite, uncertain, below = conn.execute("""SELECT count(*),
        count(*) FILTER(WHERE lo>=?), count(*) FILTER(WHERE lo<? AND hi>=?),
        count(*) FILTER(WHERE hi<?) FROM priority_cases""",[minimum,minimum,minimum,minimum]).fetchone()
    predicate = "hi>=?" if basis["include_uncertain"] else "lo>=?"
    params = [minimum]
    if after is not None:
        # Descending lower score, descending upper score, ascending case ID.
        predicate += " AND (lo<? OR (lo=? AND hi<?) OR (lo=? AND hi=? AND case_id>?))"
        params.extend([after["lower"],after["lower"],after["upper"],after["lower"],after["upper"],after["case_id"]])
    rows = conn.execute("SELECT case_id,lo,hi,violations,unknowns FROM priority_cases WHERE "+predicate+
                        " ORDER BY lo DESC, hi DESC, case_id ASC LIMIT ?",[*params,limit+1]).fetchall()
    ids = [r["rule_index"] for r in basis["rules"]]
    page = rows[:limit]
    return {"cases_evaluated":total,"confirmed_threshold_cases":definite,
            "uncertain_threshold_cases":uncertain,"below_threshold_cases":below,
            "matching_cases":definite+uncertain if basis["include_uncertain"] else definite,
            "cases":[{"case_id":key,"lower":lo,"upper":hi,
                      "threshold_status":"confirmed" if lo>=minimum else "uncertain",
                      "violated_rule_indices":[i for pos,i in enumerate(ids) if v & (1<<pos)],
                      "unknown_rule_indices":[i for pos,i in enumerate(ids) if u & (1<<pos)]}
                     for key,lo,hi,v,u in page],
            "next_after":{"case_id":page[-1][0],"lower":page[-1][1],"upper":page[-1][2]} if len(rows)>limit else None}


def inspect_case(log_id, pack_id, spec):
    """Explain one matching case under the exact reviewed worklist basis."""
    expected = {"case_type", "weights", "minimum_confirmed", "include_uncertain",
                "expected_basis_sha256", "case_id"}
    if not isinstance(spec, dict) or set(spec) != expected:
        _fail("Case inspection requires the reviewed selection, basis digest and case ID")
    key, expected_pin = spec["case_id"], spec["expected_basis_sha256"]
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        _fail("Invalid inspection case identifier")
    if not isinstance(expected_pin, str) or len(expected_pin) != 64 or any(c not in "0123456789abcdef" for c in expected_pin):
        _fail("Invalid reviewed inspection digest")
    request = {**{k:v for k,v in spec.items() if k not in ("case_id", "expected_basis_sha256")}, "limit":1}
    kind, weights, _ = _parse(request)
    if not _GUARD.acquire(blocking=False):
        _fail("Another domain pack analysis is running")
    try:
        with workspace._LOCK:
            basis = _basis(log_id, pack_id, kind, weights, request)
            pin = provenance.digest(basis)
            if pin != expected_pin:
                _fail("Reviewed source or installation changed; rerun priority review")

            def run(conn):
                # Rule outcomes are independent per case. Restrict only after
                # selecting the exact same object-type projection as the review.
                conn.execute("DELETE FROM ce WHERE case_id<>?", [key])
                event_count = conn.execute("SELECT count(*) FROM ce").fetchone()[0]
                if not event_count:
                    _fail("Case is not present in the reviewed projection")
                compliance.positions(conn)
                outcomes = []
                lo = hi = 0
                for item in basis["rules"]:
                    rule = compliance.parse({"case_type":kind,"rules":[item["rule"]]}).rules[0]
                    compliance._materialize_outcomes(conn,rule)
                    rows = conn.execute("SELECT case_id,activated,violated,unknown,detail FROM co").fetchall()
                    if len(rows) != 1 or rows[0][0] != key:
                        _fail("Case rule outcome does not match the reviewed projection")
                    _, activated, violated, unknown, detail = rows[0]
                    if (violated and unknown) or ((violated or unknown) and not activated):
                        _fail("Case rule outcome is inconsistent")
                    weight = item["weight"]
                    if violated:
                        status, reason = "violated", detail
                        lo += weight
                        hi += weight
                    elif unknown:
                        status, reason = "unknown", "Insufficient recorded evidence to decide this rule"
                        hi += weight
                    elif activated:
                        status, reason = "satisfied", None
                    else:
                        status, reason = "nonactivated", None
                    outcomes.append({"rule_index":item["rule_index"],"rule":item["rule"],
                                     "weight":weight,"status":status,"detail":reason,
                                     "confirmed_points":weight if violated else 0,
                                     "possible_points":weight if violated or unknown else 0})
                minimum = basis["minimum_confirmed"]
                if (hi if basis["include_uncertain"] else lo) < minimum:
                    _fail("Case is outside the reviewed worklist threshold")
                return {"case_id":key,"projected_events":event_count,"lower":lo,"upper":hi,
                        "threshold_status":"confirmed" if lo>=minimum else "uncertain",
                        "outcomes":outcomes}

            output = workspace._query(log_id,kind,run)
            fields = ("case_id","projected_events","lower","upper","threshold_status","outcomes")
            return {"format":"openworkgraph.domain-pack-priority-case.v1",
                    "basis_sha256":pin,**{k:output[k] for k in fields},
                    "resource_names_returned":False,"configuration_modified":False,
                    "execution_authorized":False,
                    "interpretation":"Recorded rule evidence only. A missing event may reflect unfinished work or incomplete data; a violation is not verified policy breach."}
    finally:
        _GUARD.release()


def _review(log_id, pack_id, spec, *, complete_export=False):
    export_pin = None
    if complete_export:
        if not isinstance(spec, dict) or set(spec) != {"case_type", "weights", "minimum_confirmed", "include_uncertain", "expected_basis_sha256"}:
            _fail("Complete export requires the exact reviewed selection and basis digest")
        export_pin = spec["expected_basis_sha256"]
        if not isinstance(export_pin, str) or len(export_pin) != 64 or any(c not in "0123456789abcdef" for c in export_pin):
            _fail("Invalid reviewed export digest")
        spec = {**{k:v for k,v in spec.items() if k != "expected_basis_sha256"}, "limit": 1}
    kind, weights, after = _parse(spec)
    if not _GUARD.acquire(blocking=False):
        _fail("Another domain pack analysis is running")
    try:
        with workspace._LOCK:
            basis = _basis(log_id, pack_id, kind, weights, spec)
            pin = provenance.digest(basis)
            if (after is not None and spec["expected_basis_sha256"] != pin) or (complete_export and export_pin != pin):
                _fail("Reviewed source or installation changed; rerun priority review")
            def run(conn):
                _materialize(conn,basis)
                if complete_export:
                    # Refuse before materializing identifiers; never label a partial page complete.
                    column = "hi" if basis["include_uncertain"] else "lo"
                    count = conn.execute("SELECT count(*) FROM priority_cases WHERE " + column + ">=?",
                                         [basis["minimum_confirmed"]]).fetchone()[0]
                    if count > MAX_EXPORT_CASES:
                        _fail(f"Complete export exceeds {MAX_EXPORT_CASES:,} matching cases; use exact pages or narrow the threshold")
                    result = _page(conn,basis,None,max(1,count))
                    if result["next_after"] is not None or len(result["cases"]) != count:
                        _fail("Complete export population does not match the reviewed cases")
                    return {"result":result}
                return {"result":_page(conn,basis,after,spec["limit"])}
            output = workspace._query(log_id,kind,run)["result"]
            return {"format":FORMAT,"basis":basis,"basis_sha256":pin,**output,
                    "maximum_score":sum(x["weight"] for x in weights),
                    "configuration_modified":False,"execution_authorized":False,
                    "complete_export":complete_export,"export_case_limit":MAX_EXPORT_CASES,
                    "interpretation":"User-declared review points, not probability, financial loss or verified policy breach. Unknown outcomes contribute only to the upper bound. Ordering is confirmed points descending, possible points descending, then case ID. No business actions are performed."}
    finally:
        _GUARD.release()


def review(log_id, pack_id, spec):
    return _review(log_id, pack_id, spec)


def export(log_id, pack_id, spec):
    """Recompute a bounded complete worklist against its reviewed basis."""
    return _review(log_id, pack_id, spec, complete_export=True)
