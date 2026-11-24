"""Role-aligned comparison of explicitly selected installed domain packs.

The preflight pins both installations and retained event/object/link content.
No raw case or resource identifiers leave the existing report engine. This is
a local descriptive benchmark, not a causal estimate or a company ranking.
"""
from __future__ import annotations

from threading import BoundedSemaphore
from typing import Any

from . import event_log_domain_packs as packs
from . import event_log_workspace as workspace
from . import event_log_activity_mappings as mappings
from . import event_log_hidden_activities as hidden
from . import process_discovery_provenance as provenance

FORMAT = "openworkgraph.domain-pack-benchmark.v1"
MAX_SOURCE_ROWS = 500_000
MAX_DEFINITIONS = 100
_GUARD = BoundedSemaphore(1)


def _fail(message):
    raise workspace.EventLogWorkspaceError(message)


def _parse(spec: Any, compare=False):
    expected={"other_log_id"} | ({"expected_preflight_sha256"} if compare else set())
    if not isinstance(spec,dict) or set(spec)!=expected:
        _fail("Choose a second retained log and review both installations before comparing")
    other=spec["other_log_id"]
    if not isinstance(other,str) or not 1<=len(other)<=200:
        _fail("Invalid comparison log")
    if compare:
        digest=spec["expected_preflight_sha256"]
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):
            _fail("Invalid reviewed preflight digest")
    return other


def _basis(conn,log_id,pack_id):
    info=workspace._check_log(conn,log_id)
    record=packs._record(conn,log_id,pack_id)
    if record is None:
        _fail("Install the same domain pack on both retained logs before comparing")
    definition=packs._pack(pack_id,record["version"])
    resolved=packs.resolve(definition,record["mapping"],record["parameters"])
    if any("supplied" in str(rule.get("template","")) for rule in resolved["object_rules"]):
        _fail("Supplied relationship rules are outside this benchmark fingerprint scope")
    if packs._digest(resolved)!=record["definitions_sha256"]:
        _fail("Installed domain definitions changed; reinstall before comparing")
    if sum(len(definition[k]) for k in ("metrics","compliance","object_rules"))>MAX_DEFINITIONS:
        _fail("Pack exceeds the benchmark definition budget")
    if mappings.applied_mapping(conn,log_id) is not None or hidden.hidden_names(conn,log_id):
        _fail("Clear applied activity mappings and hidden activities on both logs before benchmarking")
    # A role that disappeared from a retained source is not equivalent to an
    # intentionally omitted optional role. Refuse rather than invent a zero.
    packs._check_names(conn,log_id,packs.Mapping.model_validate(record["mapping"]))
    first,last=conn.execute("SELECT min(ts),max(ts) FROM events WHERE log_id=?",[log_id]).fetchone()
    return {"log_id":log_id,"name":info["name"],"events":info["events"],"objects":info["objects"],
            "source_format":info["source_format"],"first_event_at":first.isoformat()+"Z" if first else None,
            "last_event_at":last.isoformat()+"Z" if last else None,
            "version":record["version"],"installation_token":packs._installation_token(record),
            "definitions_sha256":record["definitions_sha256"],"mapping":record["mapping"],
            "parameters":record["parameters"],"source_sha256":provenance.source_digest(conn,log_id),
            "source_fingerprint_basis":"stored_events_objects_and_event_object_links",
            "not_applicable":resolved["not_applicable"]},definition,resolved


def _prepare(log_id,other,pack_id):
    if log_id==other:
        _fail("Choose two different retained logs")
    conn=workspace._connect()
    try:
        workspace._check_log(conn,log_id);workspace._check_log(conn,other)
        if provenance.source_rows(conn,log_id)+provenance.source_rows(conn,other)>MAX_SOURCE_ROWS:
            _fail("Combined logs exceed the complete benchmark source-row budget")
        left,definition,a=_basis(conn,log_id,pack_id)
        right,_,b=_basis(conn,other,pack_id)
    finally:
        conn.close()
    same_version=left["version"]==right["version"]
    same_parameters=left["parameters"]==right["parameters"]
    blockers=[]
    if not same_version:
        blockers.append("different_pack_versions")
    if not same_parameters:
        blockers.append("different_parameter_values")
    roles=[]
    for kind in ("object_types","activities"):
        all_roles=sorted(set(left["mapping"][kind])|set(right["mapping"][kind]))
        for role in all_roles:
            l=left["mapping"][kind].get(role);r=right["mapping"][kind].get(role)
            roles.append({"kind":kind,"role":role,"baseline_label":l,"comparison_label":r,
                          "mapped_on_both":l is not None and r is not None})
    snapshot={"format":FORMAT,"pack_id":pack_id,"baseline":left,"comparison":right,
              "same_pack_version":same_version,"same_parameters":same_parameters,
              "role_alignment":roles,"blockers":blockers,"ready_to_compare":not blockers}
    snapshot["preflight_sha256"]=provenance.digest(snapshot)
    return snapshot,definition,a,b


def preflight(log_id,pack_id,spec):
    other=_parse(spec)
    if not _GUARD.acquire(blocking=False):
        _fail("Another domain benchmark is running")
    try:
        with workspace._LOCK:
            snapshot,_,_,_=_prepare(log_id,other,pack_id)
        return snapshot
    finally:
        _GUARD.release()


def _slots(definition,resolved,section):
    omitted={r["index"] for r in resolved["not_applicable"] if r["section"]==section}
    indices=[i for i in range(len(definition[section])) if i not in omitted]
    values=resolved[section]
    if len(indices)!=len(values):
        _fail("Resolved pack slots cannot be aligned exactly")
    return dict(zip(indices,values))


def _metric_slots(definition,resolved,report):
    by_name={m["name"]:m for m in report["metrics"]}
    result={}
    for index,item in _slots(definition,resolved,"metrics").items():
        name=item["metric"]["name"]
        if name not in by_name:
            _fail("Metric report does not match resolved definitions")
        row=by_name[name]
        result[index]={k:row[k] for k in ("unit","cases","median","p90","mean","coverage")}
    return result


def _rule_matches(actual,expected):
    return all(actual.get(key)==value for key,value in expected.items())


def _compliance_slots(definition,resolved,report):
    positions={}
    grouped={g["case_type"]:g["results"] for g in report["compliance"]}
    out={}
    for index,item in _slots(definition,resolved,"compliance").items():
        kind=item["case_type"];position=positions.get(kind,0)
        rows=grouped.get(kind,[])
        if position>=len(rows) or not _rule_matches(rows[position]["rule"],item["rule"]):
            _fail("Case rule report does not match resolved definitions")
        row=rows[position];positions[kind]=position+1
        fields=("cases","activated_cases","vacuously_satisfied_cases","violating_cases",
                "satisfied_cases","unknown_cases","violation_rate")
        value={k:row[k] for k in fields}
        n=value["activated_cases"];v=value["violating_cases"];u=value["unknown_cases"]
        value["decided_cases"]=n-u
        value["activated_violation_share_bounds"]={"lower":v/n if n else None,
                                                    "upper":(v+u)/n if n else None}
        out[index]=value
    if any(positions.get(k,0)!=len(v) for k,v in grouped.items()):
        _fail("Unexpected case rule results")
    return out


def _object_slots(definition,resolved,report):
    slots=_slots(definition,resolved,"object_rules")
    rows=(report["object_rules"] or {}).get("results",[])
    if len(slots)!=len(rows):
        _fail("Object rule report does not match resolved definitions")
    out={}
    for (index,rule),row in zip(slots.items(),rows):
        if not _rule_matches(row["rule"],rule):
            _fail("Object rule report does not match resolved definitions")
        out[index]={k:row.get(k) for k in ("complete","unit","units","activated","violating",
                    "satisfied","vacuously_satisfied","violation_rate","unknown_reason")}
    return out


def _delta(a,b):
    return b-a if a is not None and b is not None else None


def _compare_slots(definition,section,a,b):
    result=[]
    for index,declared in enumerate(definition[section]):
        left=a.get(index);right=b.get(index)
        reason=None
        if left is None or right is None:
            reason="unmapped_definition"
        elif section=="metrics" and left["unit"]!=right["unit"]:
            reason="different_units"
        elif section=="object_rules" and (not left["complete"] or not right["complete"]):
            reason="incomplete_object_rule"
        elif section=="object_rules" and left["unit"]!=right["unit"]:
            reason="different_units"
        changes=None
        if reason is None:
            if section=="metrics":
                changes={k:_delta(left[k],right[k]) for k in ("median","p90","mean")}
            else:
                changes={"violation_rate_change":_delta(left["violation_rate"],right["violation_rate"])}
                if section=="compliance":
                    l=left["activated_violation_share_bounds"];r=right["activated_violation_share_bounds"]
                    changes["activated_violation_share_change_bounds"]={
                        "lower":_delta(l["upper"],r["lower"]),"upper":_delta(l["lower"],r["upper"])}
        result.append({"definition_index":index,"canonical_definition":declared,"baseline":left,
                       "comparison":right,"comparable":reason is None,"not_comparable_reason":reason,
                       "changes":changes})
    return result


def _model_summary(report):
    # Object lifecycle model comparability needs the same mapped optional
    # activities and object types, not merely the same pack version.
    model=report["object_model"]
    if not model:
        return None
    inverse={v:k for k,v in report["mapping"]["object_types"].items()}
    return {"types":[{"role":inverse[t["object_type"]],
                      **{k:t[k] for k in ("objects","replayed_objects","fitting_objects",
                                           "unknown_objects","complete")}}
                     for t in model["lifecycles"]],
            "summary":model["summary"]}


def compare(log_id,pack_id,spec):
    other=_parse(spec,compare=True)
    if not _GUARD.acquire(blocking=False):
        _fail("Another domain benchmark is running")
    try:
        with workspace._LOCK:
            snapshot,definition,ar,br=_prepare(log_id,other,pack_id)
            if snapshot["preflight_sha256"]!=spec["expected_preflight_sha256"]:
                _fail("An installation or retained source changed; review the comparison again")
            if not snapshot["ready_to_compare"]:
                _fail("Pack versions and parameters must match before comparing")
            a=packs.report(log_id,pack_id);b=packs.report(other,pack_id)
            metrics=_compare_slots(definition,"metrics",_metric_slots(definition,ar,a),_metric_slots(definition,br,b))
            rules=_compare_slots(definition,"compliance",_compliance_slots(definition,ar,a),_compliance_slots(definition,br,b))
            objects=_compare_slots(definition,"object_rules",_object_slots(definition,ar,a),_object_slots(definition,br,b))
            same_roles=all(r["mapped_on_both"] for r in snapshot["role_alignment"])
            model={"baseline":_model_summary(a),"comparison":_model_summary(b),
                   "same_mapped_roles":same_roles,"pooled_fitting_share_reported":False}
            # Report per-side lifecycle counts, not a difference of differently
            # covered or differently scoped case populations.
            result={**snapshot,"metrics":metrics,"case_rules":rules,"object_rules":objects,"object_models":model,
                    "raw_case_identifiers_returned":False,"source_completeness_verified":False,
                    "causal_effect_estimated":False,"policy_compliance_verified":False,"execution_authorized":False,
                    "assumptions":[
                        "Canonical pack definition indices, not local activity names, align rows. Mapped role equivalence is asserted by the installer, not independently verified.",
                        "Both installations use the same pack version and parameters. Unmapped optional definitions and incomplete object-rule computations do not receive deltas.",
                        "Case-rule rates use activated decided cases; bounds include unknown activated cases. Unactivated cases are disclosed, not counted as verified passes.",
                        "Metric differences compare observed values and coverage, not paired cases. Logs may overlap and have different populations, observation periods or source completeness.",
                        "Object model counts are shown separately. No overall ranking, pooled savings, causal effect or verified business compliance is inferred.",
                        "The source fingerprint covers stored events, objects and event-object links. The benchmark refuses active hidden activities or relabeling. Temporal object history and supplied object relationship assertions are outside this fingerprint and benchmark scope."]}
            result["report_sha256"]=provenance.digest(result)
            return result
    finally:
        _GUARD.release()
