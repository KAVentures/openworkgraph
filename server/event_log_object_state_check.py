"""Descriptive event-time checks against retained OCEL object attribute history."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from shared.event_log_formats import _exact_key, EventLogFormatError
from . import event_log_workspace as workspace
from . import event_log_object_relationships as relationships

MAX_EVENTS = 200_000
MAX_LINKS = 1_000_000
MAX_HISTORY_ROWS = 2_000_000
STATUSES = ("matched", "not_matched", "unknown", "no_linked_object")
OBJECT_STATUSES = ("matched", "not_matched", "missing_history", "before_first_value", "ambiguous_value")
FORMAT = "openworkgraph.ocel-event-time-state-check.v1"


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    activity: str = Field(min_length=1, max_length=200)
    object_type: str = Field(min_length=1, max_length=200)
    attribute: str = Field(min_length=1, max_length=200)
    expected_value: str = Field(max_length=4096)
    operator: Literal["eq", "ne"] = "eq"
    quantifier: Literal["all", "any"] = "all"
    event_qualifier: str | None = Field(default=None, max_length=80)
    status: Literal["matched", "not_matched", "unknown", "no_linked_object"] | None = None
    offset: int = Field(default=0, ge=0, le=MAX_EVENTS)
    limit: int = Field(default=30, ge=1, le=100)
    expected_revision: int | None = Field(default=None, ge=0)

    @field_validator("activity", "object_type", "attribute", "event_qualifier")
    @classmethod
    def literal(cls, value, info):
        if value is None:
            return None
        try:
            _exact_key(value, 80 if info.field_name == "event_qualifier" else 200)
        except EventLogFormatError:
            raise ValueError("Use exact imported labels") from None
        if info.field_name != "event_qualifier" and not value.strip():
            raise ValueError("Labels cannot be blank")
        return value


class WitnessSpec(Spec):
    expected_revision: int = Field(ge=0)
    event_id: str = Field(min_length=1, max_length=200)
    after: str | None = Field(default=None, min_length=1, max_length=200)
    witness_limit: int = Field(default=50, ge=1, le=100)

    @field_validator('event_id', 'after')
    @classmethod
    def literal_key(cls, value):
        if value is not None:
            try:
                _exact_key(value)
            except EventLogFormatError:
                raise ValueError('Use exact imported identifiers') from None
        return value


def _parse(value):
    try:
        result = Spec.model_validate(value)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("invalid bounded event-time state check") from None
    if result.offset and result.expected_revision is None:
        raise workspace.EventLogWorkspaceError("restart evidence from page one")
    return result


def _stamp(value):
    return value.isoformat()+"Z" if value is not None else None


def analyze(log_id, value):
    return _analyze(log_id, _parse(value))


def witnesses(log_id, value):
    try:
        spec = WitnessSpec.model_validate(value)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('invalid event-time state witness request') from None
    return _analyze(log_id, spec, witness=True)


def _analyze(log_id, spec, witness=False):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute('BEGIN TRANSACTION')
            info = workspace._check_log(conn, log_id)
            if info["source_format"] not in ("ocel", "ocelxml"):
                raise workspace.EventLogWorkspaceError("event-time object states need a retained OCEL log")
            revision = relationships._revision(conn, log_id)
            if spec.expected_revision is not None and revision != spec.expected_revision:
                raise workspace.EventLogWorkspaceError("log changed; restart the event-time state check")
            events = conn.execute("SELECT count(*) FROM events WHERE log_id=? AND activity=?",
                                  [log_id,spec.activity]).fetchone()[0]
            if events > MAX_EVENTS:
                raise workspace.EventLogWorkspaceError("selected activity exceeds event-time state check event bound")
            conn.execute("""CREATE TEMP TABLE st_events AS SELECT event_id,ts FROM events
                            WHERE log_id=? AND activity=?""",[log_id,spec.activity])
            # A repeated event-object relationship under several qualifiers must
            # not duplicate an object decision or change the quantifier result.
            predicate = "eo.log_id=? AND o.object_type=?"
            args = [log_id,spec.object_type]
            if spec.event_qualifier is not None:
                predicate += " AND eo.qualifier=?"
                args.append(spec.event_qualifier)
            conn.execute("""CREATE TEMP TABLE st_links AS SELECT DISTINCT e.event_id,e.ts,eo.object_id
                FROM st_events e JOIN event_objects eo ON e.event_id=eo.event_id
                JOIN objects o ON o.log_id=eo.log_id AND o.object_id=eo.object_id
                WHERE """+predicate,args)
            links = conn.execute("SELECT count(*) FROM st_links").fetchone()[0]
            if links > MAX_LINKS:
                raise workspace.EventLogWorkspaceError("selected event-object links exceed state check bound")
            history_scope = """FROM object_attribute_values v WHERE v.log_id=? AND v.name=?
                AND EXISTS(SELECT 1 FROM st_links l WHERE l.object_id=v.object_id)"""
            args=[log_id,spec.attribute]
            history_rows = conn.execute("SELECT count(*) "+history_scope,args).fetchone()[0]
            if history_rows > MAX_HISTORY_ROWS:
                raise workspace.EventLogWorkspaceError("selected object history exceeds state check bound")
            # The value at an exact timestamp applies immediately. A conflicting
            # value remains ambiguous until a later unambiguous declaration.
            conn.execute("""CREATE TEMP TABLE st_history AS SELECT v.object_id,v.valid_from,
                    CASE WHEN count(DISTINCT v.value)=1 THEN any_value(v.value) END AS value,
                    count(DISTINCT v.value)<>1 OR count(v.value)<>count(*) AS ambiguous
                """+history_scope+" GROUP BY 1,2",args)
            conn.execute("""CREATE TEMP TABLE st_known AS SELECT DISTINCT object_id FROM st_history""")
            op = "=" if spec.operator=="eq" else "<>"
            conn.execute("""CREATE TEMP TABLE st_object AS SELECT l.event_id,l.object_id,
                    h.valid_from,h.value AS value_then,
                    CASE WHEN k.object_id IS NULL THEN 'missing_history'
                         WHEN h.valid_from IS NULL THEN 'before_first_value'
                         WHEN h.ambiguous OR h.value IS NULL THEN 'ambiguous_value'
                         WHEN h.value """+op+""" ? THEN 'matched' ELSE 'not_matched' END AS status
                FROM st_links l ASOF LEFT JOIN st_history h
                ON h.object_id=l.object_id AND l.ts>=h.valid_from
                LEFT JOIN st_known k ON k.object_id=l.object_id""",[spec.expected_value])
            # Three-valued quantification. No matching object is not vacuous
            # success: report no_linked_object separately for both modes.
            first = "WHEN n_false>0 THEN 'not_matched'" if spec.quantifier=="all" else "WHEN n_true>0 THEN 'matched'"
            last = "'matched'" if spec.quantifier=="all" else "'not_matched'"
            conn.execute("""CREATE TEMP TABLE st_result AS WITH counts AS (
                SELECT event_id,count(*) AS n_objects,
                  count(*) FILTER (WHERE status='matched') AS n_true,
                  count(*) FILTER (WHERE status='not_matched') AS n_false,
                  count(*) FILTER (WHERE status NOT IN ('matched','not_matched')) AS n_unknown
                FROM st_object GROUP BY event_id)
                SELECT e.event_id,e.ts,coalesce(c.n_objects,0) AS n_objects,
                  coalesce(c.n_true,0) AS n_true,coalesce(c.n_false,0) AS n_false,
                  coalesce(c.n_unknown,0) AS n_unknown,
                  CASE WHEN c.event_id IS NULL THEN 'no_linked_object'
                       """+first+"""
                       WHEN n_unknown>0 THEN 'unknown' ELSE """+last+""" END AS status
                FROM st_events e LEFT JOIN counts c ON c.event_id=e.event_id""")
            event_counts=dict.fromkeys(STATUSES,0)
            event_counts.update(dict(conn.execute("SELECT status,count(*) FROM st_result GROUP BY status").fetchall()))
            object_counts=dict.fromkeys(OBJECT_STATUSES,0)
            object_counts.update(dict(conn.execute("SELECT status,count(*) FROM st_object GROUP BY status").fetchall()))
            where="WHERE status=?" if spec.status else ""
            args=[spec.status] if spec.status else []
            total=conn.execute("SELECT count(*) FROM st_result "+where,args).fetchone()[0]
            if witness:
                selected = conn.execute('SELECT n_objects, status FROM st_result WHERE event_id=?' +
                                        (' AND status=?' if spec.status else ''),
                                        [spec.event_id, *([spec.status] if spec.status else [])]).fetchone()
                if not selected:
                    raise workspace.EventLogWorkspaceError('event is not in the reviewed state-check result')
                predicate = 'event_id=?' + (' AND object_id>?' if spec.after is not None else '')
                found = conn.execute('SELECT object_id,valid_from,value_then,status FROM st_object WHERE ' + predicate +
                                     ' ORDER BY object_id LIMIT ?',
                                     [spec.event_id, *([spec.after] if spec.after is not None else []), spec.witness_limit + 1]).fetchall()
                more = len(found) > spec.witness_limit
                found = found[:spec.witness_limit]
                return {'format':'openworkgraph.ocel-state-check-witnesses.v1', 'log_id':log_id, 'revision':revision,
                        'event_id':spec.event_id, 'event_status':selected[1], 'total_objects':selected[0],
                        'witnesses':[{'object_id':oid, 'valid_from':_stamp(at), 'recorded_value':v, 'status':status}
                                     for oid,at,v,status in found],
                        'next_after':found[-1][0] if more else None,
                        'historical_source_completeness_verified':False, 'policy_compliance_verified':False}
            page=conn.execute("""SELECT event_id,ts,n_objects,n_true,n_false,n_unknown,status FROM st_result """
                +where+" ORDER BY ts,event_id LIMIT ? OFFSET ?",[*args,spec.limit,spec.offset]).fetchall()
            rows=[]
            # At most 20 exact object witnesses per event. Counts remain full.
            for eid,ts,n,yes,no,unknown,status in page:
                examples=conn.execute("""SELECT object_id,valid_from,value_then,status FROM st_object
                    WHERE event_id=? ORDER BY object_id LIMIT 20""",[eid]).fetchall()
                rows.append({"event_id":eid,"at":_stamp(ts),"status":status,"linked_objects":n,
                    "matched_objects":yes,"not_matched_objects":no,"unknown_objects":unknown,
                    "object_witnesses":[{"object_id":oid,"valid_from":_stamp(at),
                        "recorded_value":v,"status":state} for oid,at,v,state in examples],
                    "object_witnesses_truncated":n>len(examples)})
            return {"format":FORMAT,"log_id":log_id,"revision":revision,
                    "settings":spec.model_dump(exclude={"offset","limit","expected_revision"}),
                    "selected_events":events,"selected_distinct_event_object_links":links,
                    "selected_history_rows":history_rows,"event_counts":event_counts,
                    "object_link_counts":object_counts,"matching_events":total,"offset":spec.offset,
                    "next_offset":spec.offset+len(rows) if spec.offset+len(rows)<total else None,
                    "rows":rows,"complete_for_retained_selection":True,
                    "initial_value_convention":"1970-01-01T00:00:00Z",
                    "stored_value_representation":"exact retained strings; no type coercion",
                    "historical_source_completeness_verified":False,
                    "policy_compliance_verified":False,"business_outcomes_verified":False,
                    "execution_authorized":False}
        finally:
            conn.close()
