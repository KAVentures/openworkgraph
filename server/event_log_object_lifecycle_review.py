"""Source-bound, paginated evidence for the existing object-lifecycle replay.

Uses exactly the workspace's mapped/hidden activity projection and the original
variant selection and replay budgets. A variant omitted by a budget remains
unknown; an individually replayed object is never silently added to the reviewed
population. The fingerprint covers the projected event/object memberships, not
every attribute of the source or external business relationships.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import Field, StrictInt, ValidationError

from shared.process_token_replay import replay_path, replay_paths
from . import event_log_object_process_model as ocpm
from . import event_log_workspace as workspace
from . import event_log_quality as quality
from . import process_discovery_provenance as provenance

FORMAT = "openworkgraph.object-lifecycle-review.v1"
MAX_PROJECTED_EVENTS = 1_000_000
MAX_REGISTERED_OBJECTS = 500_000
PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


class Request(ocpm._Strict):
    model: ocpm.Model
    object_type: ocpm.Label
    max_variants: StrictInt = Field(default=200, ge=1, le=ocpm.MAX_VARIANTS)
    view: Literal["summary", "variants", "objects", "object"] = "summary"
    status: Literal["all", "fitting", "deviating", "unknown"] = "all"
    expected_review_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    variant_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    object_id: str | None = Field(default=None, min_length=1, max_length=200)
    offset: StrictInt = Field(default=0, ge=0, le=MAX_REGISTERED_OBJECTS)
    limit: StrictInt = Field(default=PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


def _parse(spec: Any) -> Request:
    try:
        request = Request.model_validate(spec)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("Invalid lifecycle evidence request") from None
    if request.view != "summary" and request.expected_review_sha256 is None:
        raise workspace.EventLogWorkspaceError("Run a lifecycle review before opening its evidence")
    if request.view == "object" and not request.object_id:
        raise workspace.EventLogWorkspaceError("Select an exact reviewed object")
    if request.view != "object" and request.object_id is not None:
        raise workspace.EventLogWorkspaceError("An object identifier is only supported for individual object review")
    if request.variant_sha256 is not None and request.view not in ("objects", "object"):
        raise workspace.EventLogWorkspaceError("A variant may only constrain object evidence")
    if request.view == "summary" and (request.offset or request.status != "all"):
        raise workspace.EventLogWorkspaceError("The summary always covers the entire selected object type")
    if request.view == "variants" and request.status == "unknown":
        raise workspace.EventLogWorkspaceError("Unknown objects have no reviewed replay variant; browse objects instead")
    return request


def _digest_path(path: list[str]) -> str:
    return provenance.digest({"ordered_activities": path})


def _coverage(conn, log_id: str, request: Request, type_model: ocpm.TypeModel) -> tuple[int, str]:
    registered = conn.execute("SELECT count(*) FROM objects WHERE log_id=? AND object_type=?",
                              [log_id, request.object_type]).fetchone()[0]
    projected = conn.execute("SELECT count(*) FROM ce").fetchone()[0]
    if projected > MAX_PROJECTED_EVENTS or registered > MAX_REGISTERED_OBJECTS:
        raise workspace.EventLogWorkspaceError("Lifecycle evidence exceeds the complete review budget; no partial fingerprint returned")
    # Include objects with no visible events, including isolated objects and
    # those removed by the active hidden-activity configuration.
    membership = quality._set_digest(conn, """
        SELECT sha256(to_json(struct_pack(id:=object_id))) AS h FROM objects
        WHERE log_id=? AND object_type=?
    """, [log_id, request.object_type])
    events = quality._set_digest(conn, """
        SELECT sha256(to_json(struct_pack(object_id:=case_id, event_id:=event_id,
            activity:=activity, event_time:=ts))) AS h FROM ce
    """, [])
    review = provenance.digest({"format": FORMAT, "log_id": log_id, "type_model": type_model.model_dump(mode="json"),
                                "max_variants": request.max_variants, "registered_membership": membership,
                                "projected_events": events, "ordering": "timestamp_then_event_id"})
    return registered, review


def _population(conn, log_id: str, request: Request, model, label_of: dict[str, str]):
    # Count before aggregating; never build a path for a trace exceeding the
    # original conformance limit. One row per registered object, not per event.
    conn.execute("""CREATE TEMP TABLE lifecycle_counts AS
        SELECT o.object_id, count(c.event_id) AS events, min(c.ts) AS first_at, max(c.ts) AS last_at
        FROM objects o LEFT JOIN ce c ON o.object_id=c.case_id
        WHERE o.log_id=? AND o.object_type=? GROUP BY o.object_id""", [log_id, request.object_type])
    conn.execute("""CREATE TEMP TABLE lifecycle_paths AS
        SELECT c.case_id AS object_id, list(c.activity ORDER BY c.ts,c.event_id) AS path
        FROM ce c JOIN lifecycle_counts n ON c.case_id=n.object_id
        WHERE n.events BETWEEN 1 AND ? GROUP BY c.case_id""", [ocpm.MAX_TRACE_LENGTH])
    rows = conn.execute("""SELECT path,count(*) AS n FROM lifecycle_paths
                            GROUP BY path ORDER BY n DESC,path LIMIT ?""", [request.max_variants]).fetchall()
    weighted = [(tuple(label_of.get(a, ocpm._UNMODELLED+a) for a in path), count) for path,count in rows]
    replay = replay_paths(weighted, model, max_events=ocpm.MAX_REPLAY_EVENTS)
    # Replay paths are a prefix of the exact weighted ordering; preserve raw
    # names, including names that normalize to the same display characters.
    variants = []
    conn.execute("""CREATE TEMP TABLE lifecycle_replayed (
        path VARCHAR[], variant_sha256 VARCHAR, status VARCHAR, fitness DOUBLE, missing BIGINT, remaining BIGINT)""")
    data = []
    for (path,count), result in zip(rows, replay["variants"]):
        identity = _digest_path(path)
        status = "fitting" if result["fits"] else "deviating"
        variants.append({"variant_sha256": identity, "path": path, "objects": count, "status": status,
                         "fitness": result["fitness"], "missing": result["missing"], "remaining": result["remaining"],
                         "produced": result["produced"], "consumed": result["consumed"]})
        data.append((path,identity,status,result["fitness"],result["missing"],result["remaining"]))
    if data:
        conn.executemany("INSERT INTO lifecycle_replayed VALUES (?,?,?,?,?,?)", data)
    conn.execute("CREATE TEMP TABLE lifecycle_selected(path VARCHAR[])")
    if rows:
        conn.executemany("INSERT INTO lifecycle_selected VALUES (?)", [(path,) for path,_ in rows])
    conn.execute("""CREATE TEMP TABLE lifecycle_population AS
        SELECT n.*,p.path,r.variant_sha256,r.fitness,r.missing,r.remaining,
            coalesce(r.status,'unknown') AS status,
            CASE WHEN r.status IS NOT NULL THEN NULL
                 WHEN n.events=0 THEN 'no_visible_events'
                 WHEN n.events>? THEN 'trace_length_limit'
                 WHEN s.path IS NULL THEN 'variant_limit'
                 ELSE 'replay_budget' END AS unknown_reason
        FROM lifecycle_counts n LEFT JOIN lifecycle_paths p USING(object_id)
        LEFT JOIN lifecycle_replayed r ON p.path=r.path
        LEFT JOIN lifecycle_selected s ON p.path=s.path""", [ocpm.MAX_TRACE_LENGTH])
    return variants, replay


def _stamp(value):
    return value.isoformat() + "Z" if value is not None else None


def _objects(conn, request: Request, variants: list[dict]):
    if request.variant_sha256 is not None and not any(v["variant_sha256"] == request.variant_sha256 for v in variants):
        raise workspace.EventLogWorkspaceError("Variant is not in the reviewed replay population")
    where = "WHERE (?='all' OR status=?) AND (? IS NULL OR variant_sha256=?)"
    args = [request.status,request.status,request.variant_sha256,request.variant_sha256]
    count = conn.execute("SELECT count(*) FROM lifecycle_population "+where,args).fetchone()[0]
    rows = conn.execute("""SELECT object_id,events,first_at,last_at,variant_sha256,status,unknown_reason,fitness,missing,remaining
        FROM lifecycle_population """+where+" ORDER BY object_id LIMIT ? OFFSET ?",[*args,request.limit,request.offset]).fetchall()
    return {"total":count,"objects":[{"object_id":key,"events":events,"first_at":_stamp(first),"last_at":_stamp(last),
            "variant_sha256":digest,"status":status,"unknown_reason":reason,"fitness":fitness,"missing":missing,"remaining":remaining}
            for key,events,first,last,digest,status,reason,fitness,missing,remaining in rows],
            "next_offset":request.offset+len(rows) if request.offset+len(rows)<count else None}


def _object(conn, request: Request, model, label_of: dict[str,str]):
    row = conn.execute("""SELECT status,unknown_reason,events,path,variant_sha256
        FROM lifecycle_population WHERE object_id=?""",[request.object_id]).fetchone()
    if row is None or (request.variant_sha256 is not None and request.variant_sha256 != row[4]) or (
            request.status != "all" and request.status != row[0]):
        raise workspace.EventLogWorkspaceError("Object is absent from the selected reviewed population")
    status, reason, count, path, variant = row
    events = conn.execute("""SELECT event_id,activity,ts,count(*) OVER(PARTITION BY ts) AS tied
        FROM ce WHERE case_id=? ORDER BY ts,event_id LIMIT ? OFFSET ?""",
        [request.object_id,request.limit,request.offset]).fetchall()
    diagnostics = None
    if status != "unknown":
        replay = replay_path(tuple(label_of.get(a,ocpm._UNMODELLED+a) for a in path),model)
        reverse = {label:activity for activity,label in label_of.items()}
        def display(values):
            result = Counter()
            for key,value in values.items():
                result[key[1:] if key.startswith(ocpm._UNMODELLED) else reverse.get(key,key)] += value
            return dict(result)
        diagnostics = {**replay,**{k:display(replay[k]) for k in
                       ("missing_by_activity","remaining_by_activity","unmodelled_activities")}}
    return {"object_id":request.object_id,"status":status,"unknown_reason":reason,
            "variant_sha256":variant,"total_events":count,"token_replay":diagnostics,
            "events":[{"event_id":key,"activity":activity,"timestamp":_stamp(ts),"timestamp_tied":tied>1}
                      for key,activity,ts,tied in events],
            "next_offset":request.offset+len(events) if request.offset+len(events)<count else None,
            "token_diagnostics_are_per_activity_not_event_attribution":True}


def review(log_id: str, spec: Any) -> dict:
    request = _parse(spec)
    type_model = next((t for t in request.model.types if t.object_type==request.object_type),None)
    if type_model is None:
        raise workspace.EventLogWorkspaceError("Select an object type in the reviewed model")
    model,label_of = ocpm._validated(type_model)
    def run(conn):
        registered,digest = _coverage(conn,log_id,request,type_model)
        if request.expected_review_sha256 is not None and request.expected_review_sha256 != digest:
            raise workspace.EventLogWorkspaceError("Lifecycle source or model changed; run the review again")
        variants,replay = _population(conn,log_id,request,model,label_of)
        counts = dict(conn.execute("SELECT status,count(*) FROM lifecycle_population GROUP BY status").fetchall())
        reasons = dict(conn.execute("""SELECT unknown_reason,count(*) FROM lifecycle_population
            WHERE status='unknown' GROUP BY unknown_reason""").fetchall())
        fitting,deviating,unknown = (counts.get(k,0) for k in ("fitting","deviating","unknown"))
        if fitting+deviating+unknown != registered or fitting+deviating != replay["replayed_cases"]:
            raise workspace.EventLogWorkspaceError("Lifecycle review accounting is inconsistent")
        base = {"format":FORMAT,"object_type":request.object_type,"review_sha256":digest,
                "registered_objects":registered,"fitting_objects":fitting,"deviating_objects":deviating,
                "unknown_objects":unknown,"unknown_reasons":reasons,
                "fitting_share_bounds":{"lower":fitting/registered if registered else None,
                    "upper":(fitting+unknown)/registered if registered else None},
                "replayed_variants":len(variants),"complete_registered_population_replayed":unknown==0,
                "ordering_basis":"timestamp_then_event_id","fingerprint_basis":"projected_event_memberships_and_selected_type_model",
                "binding_based_replay":False,"policy_compliance_verified":False,"execution_authorized":False}
        if request.view=="summary":
            return {**base,"token_fitness_replayed_objects":replay["token_fitness"]}
        if request.view=="variants":
            picked=[v for v in variants if request.status=="all" or v["status"]==request.status]
            page=picked[request.offset:request.offset+request.limit]
            return {**base,"total":len(picked),"variants":page,
                    "next_offset":request.offset+len(page) if request.offset+len(page)<len(picked) else None}
        if request.view=="objects":
            return {**base,**_objects(conn,request,variants)}
        return {**base,**_object(conn,request,model,label_of)}
    return workspace._query(log_id,request.object_type,run)
