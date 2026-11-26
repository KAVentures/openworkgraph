"""Descriptive event-time checks against retained OCEL object attribute history."""
from __future__ import annotations

import csv
from datetime import date
import re
from typing import IO, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from shared.event_log_formats import _exact_key, EventLogFormatError
from shared.spreadsheet_review import review_cell
from . import event_log_workspace as workspace
from . import event_log_object_relationships as relationships

MAX_EVENTS = 200_000
MAX_LINKS = 1_000_000
MAX_HISTORY_ROWS = 2_000_000
MAX_TIMELINE_BUCKETS = 1000
MAX_WORKLIST_BYTES = 512 * 1024 * 1024
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
    from_date: str | None = None
    until_date: str | None = None
    result_event_id: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["matched", "not_matched", "unknown", "no_linked_object"] | None = None
    offset: int = Field(default=0, ge=0, le=MAX_EVENTS)
    limit: int = Field(default=30, ge=1, le=100)
    expected_revision: int | None = Field(default=None, ge=0)

    @field_validator("from_date", "until_date")
    @classmethod
    def calendar_date(cls, value):
        if value is not None:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("Use a UTC calendar date")
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def date_window(self):
        if self.from_date and self.until_date and self.from_date >= self.until_date:
            raise ValueError("The exclusive end date must be after the start date")
        return self

    @field_validator("result_event_id")
    @classmethod
    def result_key(cls, value):
        if value is not None:
            try:
                if not _exact_key(value):
                    raise ValueError("Event ID cannot be blank")
            except EventLogFormatError:
                raise ValueError("Use an exact imported event identifier") from None
        return value

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


class TimelineSpec(Spec):
    expected_revision: int = Field(ge=0)
    bucket: Literal['day', 'week', 'month']


class ExportSpec(Spec):
    expected_revision: int = Field(ge=0)
    kind: Literal['events', 'objects']


class _LimitedWorklist:
    def __init__(self, out: IO[str]):
        self.out = out
        self.size = 0

    def write(self, text: str):
        try:
            size = len(text.encode('utf-8'))
        except UnicodeEncodeError:
            raise workspace.EventLogWorkspaceError('state export contains unsupported text; no export was produced') from None
        if self.size + size > MAX_WORKLIST_BYTES:
            raise workspace.EventLogWorkspaceError('state export exceeds the 512 MiB file limit; no export was produced')
        self.size += size
        return self.out.write(text)


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


def timeline(log_id, value):
    try:
        spec = TimelineSpec.model_validate(value)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('invalid event-time state timeline request') from None
    return _analyze(log_id, spec, timeline=True)


def export_worklist(log_id: str, value, out: IO[str]):
    try:
        spec = ExportSpec.model_validate(value)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('invalid event-time state export request') from None
    if spec.offset != 0 or spec.limit != 30:
        raise workspace.EventLogWorkspaceError('state export always covers the complete filtered selection')
    return _analyze(log_id, spec, export=out)


def _result_predicate(spec, alias=""):
    prefix = alias + "." if alias else ""
    terms, params = [], []
    if spec.status is not None:
        terms.append(prefix + "status=?")
        params.append(spec.status)
    if spec.result_event_id is not None:
        terms.append(prefix + "event_id=?")
        params.append(spec.result_event_id)
    return (" WHERE " + " AND ".join(terms) if terms else ""), params


def _write_worklist(conn, log_id: str, spec: ExportSpec, revision: int, out: IO[str]) -> dict[str, int]:
    predicate, params = _result_predicate(spec, 'r')
    if spec.kind == 'events':
        expected = conn.execute('SELECT count(*) FROM st_result r' + predicate, params).fetchone()[0]
        query = '''SELECT r.event_id,r.ts,r.status,r.n_objects,r.n_true,r.n_false,r.n_unknown,
                          NULL::VARCHAR,NULL::VARCHAR,NULL::TIMESTAMP,NULL::VARCHAR FROM st_result r'''
    else:
        expected = conn.execute('''SELECT coalesce(sum(CASE WHEN r.n_objects=0 THEN 1 ELSE r.n_objects END),0)
                                   FROM st_result r''' + predicate, params).fetchone()[0]
        query = '''SELECT r.event_id,r.ts,r.status,r.n_objects,r.n_true,r.n_false,r.n_unknown,
                          o.object_id,o.status,o.valid_from,o.value_then
                   FROM st_result r LEFT JOIN st_object o ON o.event_id=r.event_id'''
    query += predicate + (' ORDER BY r.ts,r.event_id' if spec.kind == 'events' else ' ORDER BY r.ts,r.event_id,o.object_id')
    cursor = conn.execute(query, params)
    writer = csv.writer(_LimitedWorklist(out))
    writer.writerow(['record_type','log_id','log_revision','activity_review','object_type_review','attribute_review',
                     'expected_value_review','operator','quantifier','qualifier_present','qualifier_review','result_filter',
                     'event_id_review','event_time_utc','event_result','linked_objects','matched_objects',
                     'not_matched_objects','unknown_objects','object_id_review','object_result','recorded_from_utc',
                     'recorded_value_review','recorded_value_present','from_date_utc','until_date_exclusive_utc','event_id_filter_review'])
    prefix = [log_id,revision,spec.activity,spec.object_type,spec.attribute,spec.expected_value,spec.operator,
              spec.quantifier,str(spec.event_qualifier is not None).lower(),spec.event_qualifier,spec.status]
    writer.writerow([review_cell(value) for value in ['check',*prefix,None,None,None,None,None,None,None,
                                                       None,None,None,None,'false',spec.from_date,spec.until_date,spec.result_event_id]])
    written = 0
    while batch := cursor.fetchmany(5000):
        for eid,at,status,n,yes,no,unknown,oid,object_status,valid_from,recorded in batch:
            kind = 'event' if spec.kind == 'events' else ('event_without_object' if oid is None else 'event_object')
            values = [kind,*prefix,eid,_stamp(at),status,n,yes,no,unknown,oid,
                      object_status if oid is not None else None,_stamp(valid_from),recorded,str(recorded is not None).lower(),spec.from_date,spec.until_date,spec.result_event_id]
            writer.writerow([review_cell(value) for value in values])
            written += 1
    if written != expected:
        raise workspace.EventLogWorkspaceError('state evidence changed; no export was produced')
    return {'rows':written}


def _analyze(log_id, spec, witness=False, export=None, timeline=False):
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
            event_scope = "log_id=? AND activity=?"
            event_args = [log_id, spec.activity]
            if spec.from_date is not None:
                event_scope += " AND ts>=?"
                event_args.append(date.fromisoformat(spec.from_date))
            if spec.until_date is not None:
                event_scope += " AND ts<?"
                event_args.append(date.fromisoformat(spec.until_date))
            events = conn.execute("SELECT count(*) FROM events WHERE " + event_scope, event_args).fetchone()[0]
            if events > MAX_EVENTS:
                raise workspace.EventLogWorkspaceError("selected activity exceeds event-time state check event bound")
            conn.execute("CREATE TEMP TABLE st_events AS SELECT event_id,ts FROM events WHERE " + event_scope, event_args)
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
            where, args = _result_predicate(spec)
            total=conn.execute("SELECT count(*) FROM st_result "+where,args).fetchone()[0]
            if export is not None:
                return _write_worklist(conn, log_id, spec, revision, export)
            if timeline:
                # Date truncation is UTC because imported timestamps are stored in UTC.
                conn.execute("""CREATE TEMP TABLE st_buckets AS
                    SELECT date_trunc(?, ts) AS bucket_start, count(*) AS total,
                      count(*) FILTER (WHERE status='matched') AS matched,
                      count(*) FILTER (WHERE status='not_matched') AS not_matched,
                      count(*) FILTER (WHERE status='unknown') AS unknown,
                      count(*) FILTER (WHERE status='no_linked_object') AS no_linked_object
                    FROM st_result """ + where + " GROUP BY 1", [spec.bucket, *args])
                count = conn.execute('SELECT count(*) FROM st_buckets').fetchone()[0]
                if count > MAX_TIMELINE_BUCKETS:
                    raise workspace.EventLogWorkspaceError('timeline exceeds 1,000 non-empty periods; choose a larger period or date window')
                rows = conn.execute('SELECT * FROM st_buckets ORDER BY bucket_start LIMIT ? OFFSET ?',
                                    [spec.limit, spec.offset]).fetchall()
                return {'format':'openworkgraph.ocel-state-check-timeline.v1', 'log_id':log_id,
                        'revision':revision, 'bucket':spec.bucket, 'matching_events':total,
                        'total_buckets':count, 'offset':spec.offset,
                        'next_offset':spec.offset+len(rows) if spec.offset+len(rows)<count else None,
                        'rows':[{'start_utc':at.isoformat()[:10], 'events':n, 'matched':yes, 'not_matched':no,
                                 'unknown':unknown, 'no_linked_object':none}
                                for at,n,yes,no,unknown,none in rows],
                        'historical_source_completeness_verified':False, 'policy_compliance_verified':False}
            if witness:
                selected = conn.execute('SELECT n_objects, status FROM st_result' +
                                        (where + ' AND ' if where else ' WHERE ') + 'event_id=?',
                                        [*args, spec.event_id]).fetchone()
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
