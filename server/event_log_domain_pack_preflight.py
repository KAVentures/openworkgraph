"""Read-only domain-pack trial with an optimistic preflight installation token."""
from __future__ import annotations

from . import event_log_domain_packs as packs
from . import event_log_workspace as workspace
from . import event_log_object_relationships as relationships

FORMAT = "openworkgraph.domain-pack-preflight.v1"
MAX_TRIAL_EVENTS = 250_000
MAX_TRIAL_OBJECTS = 250_000


def basis(conn, log_id, pack_id, parsed, params, definitions, previous):
    """Shared with install inside its transaction; never trust client summaries."""
    saved = conn.execute("""SELECT metric_id, definition FROM metric_definitions
                            WHERE log_id=? ORDER BY metric_id""", [log_id]).fetchall()
    token = packs._digest({"log_id":log_id,"pack_id":pack_id,"version":parsed.version,
        "revision":relationships._revision(conn,log_id),"mapping":parsed.mapping.model_dump(),
        "parameters":params,"definitions_sha256":packs._digest(definitions),
        "installation_token":packs._installation_token(previous) if previous else None,
        "metric_library":saved})
    return token,saved


def _diff(old,new):
    """Exact definition-level multiset differences, without claiming semantic equivalence."""
    from collections import Counter
    sections={}
    for key in ("metrics","compliance","object_rules","not_applicable"):
        a=Counter(packs._digest(row) for row in old.get(key,[]))
        b=Counter(packs._digest(row) for row in new[key])
        sections[key]={"unchanged":sum((a&b).values()),"removed":sum((a-b).values()),
                       "added":sum((b-a).values())}
    sections["reference_model"]={"changed":old.get("reference_model")!=new["reference_model"]}
    return sections


def preview(log_id,pack_id,value):
    pack,parsed,params=packs._parse_install(pack_id,value)
    mapping=parsed.mapping.model_dump()
    definitions=packs.resolve(pack,mapping,params)
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            info=workspace._check_log(conn,log_id)
            packs._check_names(conn,log_id,parsed.mapping)
            previous=packs._record(conn,log_id,pack_id)
            if "expected_installation" in parsed.model_fields_set:
                current=packs._installation_token(previous) if previous else None
                if parsed.expected_installation!=current:
                    raise packs.DomainPackConflict("domain pack installation changed; reload setup")
            token,saved=basis(conn,log_id,pack_id,parsed,params,definitions,previous)
            import json
            owned=set(previous["metric_ids"]) if previous else set()
            saved_names=[(mid,json.loads(raw)["metric"]["name"]) for mid,raw in saved]
            names={d["metric"]["name"] for d in definitions["metrics"]}
            clashes=sorted(name for mid,name in saved_names if mid not in owned and name in names)
            existing_ids={mid for mid,_ in saved_names}
            projected=len(saved_names)-len(owned&existing_ids)+len(definitions["metrics"])
            blockers=[]
            if previous and packs._version_key(parsed.version)<packs._version_key(previous["version"]):
                blockers.append("downgrade_refused")
            if clashes:
                blockers.append("metric_name_collision")
            if projected>packs.query.MAX_SAVED:
                blockers.append("metric_library_capacity")
            events,objects=conn.execute("""SELECT
                (SELECT count(*) FROM events WHERE log_id=?),
                (SELECT count(*) FROM objects WHERE log_id=?)""",[log_id,log_id]).fetchone()
            revision=relationships._revision(conn,log_id)
            role_counts={"object_types":[],"activities":[]}
            for kind,table,column in (("object_types","objects","object_type"),
                                      ("activities","events","activity")):
                for role,name in sorted(mapping[kind].items()):
                    count=conn.execute(f"SELECT count(*) FROM {table} WHERE log_id=? AND {column}=?",
                                       [log_id,name]).fetchone()[0]
                    role_counts[kind].append({"role":role,"name":name,"recorded_count":count})
        finally:
            conn.close()
        previous_defs=None
        if previous:
            try:
                previous_defs=packs.resolve(packs._pack(pack_id,previous["version"]),
                                            previous["mapping"],previous["parameters"])
                if packs._digest(previous_defs)!=previous["definitions_sha256"]:
                    previous_defs=None
            except workspace.EventLogWorkspaceError:
                previous_defs=None
        # Never install definitions in order to test them. Reuse the exact
        # existing report engines on the resolved, unsaved configuration.
        trial=None
        trial_reason=None
        if events>MAX_TRIAL_EVENTS or objects>MAX_TRIAL_OBJECTS:
            trial_reason="trial_population_bound"
        else:
            groups={}
            for item in definitions["compliance"]:
                groups.setdefault(item["case_type"],[]).append(item["rule"])
            trial={"metrics":[packs._metric_summary(log_id,item) for item in definitions["metrics"]],
                   "compliance":[{"case_type":t,**{k:v for k,v in packs.compliance.check(
                       log_id,{"rules":rules,"case_type":t}).items()
                       if k in ("results","rules_with_violations")}} for t,rules in groups.items()],
                   "object_rules":({k:v for k,v in packs.object_constraints.check(
                       log_id,{"rules":definitions["object_rules"]}).items()
                       if k in ("results","rules_with_violations","rules_unknown")}
                       if definitions["object_rules"] else None),
                   "object_model":({k:v for k,v in packs.object_model.check(
                       log_id,{"model":definitions["reference_model"]}).items()
                       if k in ("lifecycles","arcs","summary")}
                       if definitions["reference_model"]["types"] else None),
                   "not_applicable":definitions["not_applicable"]}
        # All calls above share the workspace lock and cannot append between
        # the definition validation and trial. No persistent records changed.
        return {"format":FORMAT,"log_id":log_id,"log_revision":revision,
                "pack":{"pack_id":pack_id,"version":parsed.version,"title":pack["title"],
                        "definitions_sha256":packs._digest(definitions)},
                "mapping":mapping,"parameters":params,"role_counts":role_counts,
                "retained_events":events,"retained_objects":objects,
                "current_installation_token":packs._installation_token(previous) if previous else None,
                "previous_version":previous["version"] if previous else None,
                "definition_changes":_diff(previous_defs or {},definitions) if not previous or previous_defs is not None else None,
                "resolved_definitions":definitions,"assumptions":pack["assumptions"],
                "metric_name_collisions":clashes,"projected_metric_library_size":projected,
                "metric_library_limit":packs.query.MAX_SAVED,"installation_blockers":blockers,
                "installable":not blockers,"preview_token":token,
                "trial":trial,"trial_unavailable_reason":trial_reason,
                "trial_completed":trial is not None,"installation_performed":False,
                "source_system_validated":False,"policy_compliance_verified":False,
                "business_outcomes_verified":False,"execution_authorized":False,
                "interpretation":"Trial results are descriptive measurements on the retained log under the proposed mapping. They are not historical outcomes or proof of business compliance. A preview token pins setup and retained append revision, not independent approval."}
