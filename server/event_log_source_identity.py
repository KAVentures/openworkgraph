"""Explicit source event keys over the existing local event tables, never provider proof."""
from dataclasses import dataclass
import csv
import re
from typing import Any
from shared.event_log_formats import EventLogFormatError,ReadStats,MAX_ATTRIBUTES,MAX_ATTRIBUTE_VALUE

FORMAT='openworkgraph.supplied-source-event-identity.v1'
CASE_TYPE='source_case'
MAX_NAMESPACES=20
NS_PATTERN=r'[A-Za-z0-9][A-Za-z0-9_.-]{0,39}'
FIELDS=('event_id_column','source_namespace','source_namespace_column','case_namespace','case_namespace_column','attributes_json_column')
SCHEMA='''
CREATE TABLE IF NOT EXISTS event_source_namespaces (
 log_id VARCHAR NOT NULL, source_namespace VARCHAR NOT NULL, case_namespace VARCHAR NOT NULL,
 PRIMARY KEY(log_id,source_namespace));
CREATE TABLE IF NOT EXISTS event_source_keys (
 log_id VARCHAR NOT NULL, event_id VARCHAR NOT NULL, source_namespace VARCHAR NOT NULL, source_event_id VARCHAR NOT NULL,
 PRIMARY KEY(log_id,source_namespace,source_event_id), UNIQUE(log_id,event_id));
'''

@dataclass
class Mode:
    event_id_column:str
    source_namespace:str=''
    source_namespace_column:str=''
    case_namespace:str=''
    case_namespace_column:str=''
    attributes_json_column:str=''

    def expressions(self,q):
        ns=q(self.source_namespace_column) if self.source_namespace_column else "'"+self.source_namespace+"'"
        case=q(self.case_namespace_column) if self.case_namespace_column else "'"+self.case_namespace+"'" if self.case_namespace else ns
        return f'{q(self.event_id_column)} AS source_id, {ns} AS source_ns, {case} AS case_ns'

@dataclass
class SourceReadStats(ReadStats):
    source_identity:dict[str,Any]|None=None

def settings(mapping):
    mapping=mapping or {};values={key:('' if mapping.get(key) is None else mapping[key]) for key in FIELDS}
    if any(not isinstance(value,str) or len(value)>200 or '\0' in value for value in values.values()):raise EventLogFormatError('Invalid source event identity mapping')
    if not any(values.values()):return None
    mode=Mode(**values)
    if not mode.event_id_column or bool(mode.source_namespace)==bool(mode.source_namespace_column):raise EventLogFormatError('Choose an event ID column and exactly one declared source namespace or namespace column')
    if mode.case_namespace and mode.case_namespace_column:raise EventLogFormatError('Choose a constant case namespace or a case namespace column, not both')
    if any(value and not re.fullmatch(NS_PATTERN,value) for value in (mode.source_namespace,mode.case_namespace)):raise EventLogFormatError('Source and case namespaces must be 1 to 40 letters, digits, dots, underscores or hyphens')
    if mapping.get('keep_rejected'):raise EventLogFormatError('Source event identity mode requires a complete valid batch; quarantine is not supported for this mode')
    if mode.attributes_json_column and mapping.get('attribute_columns'):raise EventLogFormatError('Choose attribute columns or one source attribute JSON column, not both')
    return mode

def validate_columns(mode,header):
    if any(value and value not in header for value in (mode.event_id_column,mode.source_namespace_column,mode.case_namespace_column,mode.attributes_json_column)):
        raise EventLogFormatError('A selected source identity or attribute JSON column is missing')

def prepare_csv(conn,mode,invalid):
    """Validate and deduplicate the private staged CSV before any event is inserted."""
    if conn.execute('SELECT 1 FROM staged WHERE '+invalid+' LIMIT 1').fetchone():raise EventLogFormatError('Source event identity mode requires every case, activity and timestamp in the batch to be valid')
    controls=r'[\x00-\x1f\x7f-\x9f\x{fffe}\x{ffff}]'
    if conn.execute('''SELECT 1 FROM staged WHERE source_id IS NULL OR trim(source_id)='' OR length(source_id)>200
        OR regexp_matches(source_id,?) OR NOT regexp_full_match(coalesce(source_ns,''),?)
        OR NOT regexp_full_match(coalesce(case_ns,''),?) OR length('src:'||case_ns||':'||case_id)>200 LIMIT 1''',[controls,NS_PATTERN,NS_PATTERN]).fetchone():
        raise EventLogFormatError('Source event IDs or namespaced case IDs exceed their bounds, are missing or contain invalid characters')
    if conn.execute('SELECT source_ns FROM staged GROUP BY source_ns HAVING count(DISTINCT case_ns)>1 LIMIT 1').fetchone():raise EventLogFormatError('One source event namespace must use one declared case namespace')
    if conn.execute('SELECT count(DISTINCT source_ns) FROM staged').fetchone()[0]>MAX_NAMESPACES:raise EventLogFormatError('Use at most twenty source event namespaces per log')
    if mode.attributes_json_column:
        if conn.execute(f'''SELECT 1 FROM staged WHERE attributes IS NULL OR json_type(attributes)!='OBJECT'
            OR len(json_keys(attributes))>{MAX_ATTRIBUTES} OR len(json_keys(attributes))!=len(list_distinct(json_keys(attributes))) LIMIT 1''').fetchone():
            raise EventLogFormatError('Source attributes must be a bounded JSON object with unique keys')
        if conn.execute(f'''SELECT 1 FROM staged, json_each(attributes) j WHERE j.type!='VARCHAR'
            OR length(j.key)=0 OR length(j.key)>80 OR length(json_extract_string(j.value,'$'))>{MAX_ATTRIBUTE_VALUE}
            OR contains(j.key,chr(0)) OR contains(json_extract_string(j.value,'$'),chr(0)) LIMIT 1''').fetchone():
            raise EventLogFormatError('Source attribute names and string values exceed their bounds or have unsupported types')
    conn.execute('CREATE OR REPLACE TEMP TABLE source_first AS SELECT source_ns,source_id,min(n) AS first_n FROM staged GROUP BY source_ns,source_id')
    if conn.execute('''SELECT 1 FROM staged a JOIN source_first f ON f.source_ns=a.source_ns AND f.source_id=a.source_id
        JOIN staged b ON b.n=f.first_n WHERE a.case_id IS DISTINCT FROM b.case_id OR a.activity IS DISTINCT FROM b.activity
        OR a.ts IS DISTINCT FROM b.ts OR a.resource IS DISTINCT FROM b.resource
        OR NOT (json_contains(a.attributes,b.attributes) AND json_contains(b.attributes,a.attributes)) LIMIT 1''').fetchone():
        raise EventLogFormatError('A supplied source event ID has conflicting payloads within this batch')
    before=conn.execute('SELECT count(*) FROM staged').fetchone()[0]
    conn.execute('DELETE FROM staged WHERE n NOT IN (SELECT first_n FROM source_first)')
    duplicates=before-conn.execute('SELECT count(*) FROM staged').fetchone()[0]
    conn.execute("UPDATE staged SET case_id='src:'||case_ns||':'||case_id")
    return duplicates

def capture_csv_keys(conn,log_id):
    conn.execute('INSERT INTO event_source_namespaces SELECT DISTINCT ?,source_ns,case_ns FROM staged',[log_id])
    conn.execute("INSERT INTO event_source_keys SELECT ?, 'e'||n,source_ns,source_id FROM staged",[log_id])

def description(conn,log_id):
    rows=conn.execute('''SELECT n.source_namespace,n.case_namespace,count(k.event_id) FROM event_source_namespaces n
        LEFT JOIN event_source_keys k ON k.log_id=n.log_id AND k.source_namespace=n.source_namespace
        WHERE n.log_id=? GROUP BY n.source_namespace,n.case_namespace ORDER BY 1''',[log_id]).fetchall()
    if not rows:return None
    return {'format':FORMAT,'deduplication_basis':'declared_source_namespace_and_supplied_event_id',
        'namespaces':[{'source_namespace':ns,'case_namespace':case,'events':count} for ns,case,count in rows],
        'case_type':CASE_TYPE,'source_events':sum(r[2] for r in rows),'provider_identity_independently_verified':False,
        'case_identity_independently_verified':False,'case_namespace_basis':'caller_declared_exact_case_key',
        'historical_completeness':'unknown','plain_exports_preserve_source_keys':False,'execution_authorized':False}

def metadata(conn,log_id,stats):
    if not isinstance(stats,SourceReadStats):return {}
    return {'source_identity':{**description(conn,log_id),**stats.source_identity}}

def validate_index(conn,log_id):
    if conn.execute('''SELECT 1 FROM event_source_keys k LEFT JOIN events e ON e.log_id=k.log_id AND e.event_id=k.event_id
        LEFT JOIN event_source_namespaces n ON n.log_id=k.log_id AND n.source_namespace=k.source_namespace
        WHERE k.log_id=? AND (e.event_id IS NULL OR n.source_namespace IS NULL) LIMIT 1''',[log_id]).fetchone():
        raise EventLogFormatError('The saved source event identity index is incomplete')
    if conn.execute('''SELECT 1 FROM event_objects l JOIN objects o ON o.log_id=l.log_id AND o.object_id=l.object_id
        WHERE l.log_id=? AND o.object_type=? AND NOT EXISTS
        (SELECT 1 FROM event_source_keys k WHERE k.log_id=l.log_id AND k.event_id=l.event_id) LIMIT 1''',[log_id,CASE_TYPE]).fetchone():
        raise EventLogFormatError('The source case notion contains events without a supplied source event identity')
    if conn.execute('''SELECT k.event_id FROM event_source_keys k
        JOIN event_source_namespaces n ON n.log_id=k.log_id AND n.source_namespace=k.source_namespace
        LEFT JOIN event_objects l ON l.log_id=k.log_id AND l.event_id=k.event_id
        LEFT JOIN objects o ON o.log_id=l.log_id AND o.object_id=l.object_id
        WHERE k.log_id=? GROUP BY k.event_id,n.case_namespace
        HAVING count(DISTINCT o.object_id)!=1 OR NOT bool_and(coalesce(
          o.object_type=? AND starts_with(o.object_id,'src:'||n.case_namespace||':'),false)) LIMIT 1''',[log_id,CASE_TYPE]).fetchone():
        raise EventLogFormatError('The saved source event identity index has missing or inconsistent case links')

def choose_append(conn,log_id,staging):
    """Fill the existing keep table by exact supplied key, including time corrections."""
    validate_index(conn,log_id)
    if conn.execute('''SELECT 1 FROM event_source_namespaces a JOIN event_source_namespaces b
        ON a.source_namespace=b.source_namespace WHERE a.log_id=? AND b.log_id=? AND a.case_namespace!=b.case_namespace LIMIT 1''',[log_id,staging]).fetchone():
        raise EventLogFormatError('This source event namespace is already bound to another case namespace')
    if conn.execute('SELECT count(DISTINCT source_namespace) FROM event_source_namespaces WHERE log_id IN (?,?)',[log_id,staging]).fetchone()[0]>MAX_NAMESPACES:raise EventLogFormatError('Use at most twenty source event namespaces per log')
    # Source-ID lookup intentionally has no new-batch time-window restriction.
    conn.execute('''CREATE OR REPLACE TEMP TABLE incoming_source AS SELECT k.source_namespace,k.source_event_id,e.*,
          (SELECT list(object_id ORDER BY object_id) FROM event_objects o WHERE o.log_id=e.log_id AND o.event_id=e.event_id) AS objs
        FROM event_source_keys k JOIN events e ON e.log_id=k.log_id AND e.event_id=k.event_id WHERE k.log_id=?''',[staging])
    conn.execute('''CREATE OR REPLACE TEMP TABLE existing_source AS SELECT k.source_namespace,k.source_event_id,e.*,
          (SELECT list(object_id ORDER BY object_id) FROM event_objects o WHERE o.log_id=e.log_id AND o.event_id=e.event_id) AS objs
        FROM event_source_keys k LEFT JOIN events e ON e.log_id=k.log_id AND e.event_id=k.event_id
        WHERE k.log_id=? AND EXISTS(SELECT 1 FROM incoming_source n WHERE n.source_namespace=k.source_namespace AND n.source_event_id=k.source_event_id)''',[log_id])
    if conn.execute('''SELECT 1 FROM incoming_source n JOIN existing_source o USING(source_namespace,source_event_id)
        WHERE o.event_id IS NULL OR n.activity IS DISTINCT FROM o.activity OR n.ts IS DISTINCT FROM o.ts
        OR n.resource IS DISTINCT FROM o.resource OR n.objs IS DISTINCT FROM o.objs
        OR NOT(json_contains(n.attributes,o.attributes) AND json_contains(o.attributes,n.attributes)) LIMIT 1''').fetchone():
        raise EventLogFormatError('A supplied source event ID conflicts with the saved event payload; no source events were appended')
    conn.execute('''CREATE OR REPLACE TEMP TABLE keep AS SELECT n.event_id FROM incoming_source n WHERE NOT EXISTS
        (SELECT 1 FROM existing_source o WHERE o.source_namespace=n.source_namespace AND o.source_event_id=n.source_event_id)''')

def copy_append_keys(conn,log_id,staging,prefix):
    conn.execute('''INSERT INTO event_source_namespaces SELECT ?,n.source_namespace,n.case_namespace FROM event_source_namespaces n
        WHERE n.log_id=? AND NOT EXISTS(SELECT 1 FROM event_source_namespaces t WHERE t.log_id=? AND t.source_namespace=n.source_namespace)''',[log_id,staging,log_id])
    conn.execute('''INSERT INTO event_source_keys SELECT ?,?||s.event_id,s.source_namespace,s.source_event_id FROM event_source_keys s
        JOIN keep k ON k.event_id=s.event_id WHERE s.log_id=?''',[log_id,prefix,staging])

def export_csv(conn,log_id,case_type,out):
    if case_type!=CASE_TYPE:raise EventLogFormatError('Choose the source_case notion for identity-preserving source CSV export')
    validate_index(conn,log_id)
    if conn.execute('''SELECT k.event_id FROM event_source_keys k
        LEFT JOIN event_objects l ON l.log_id=k.log_id AND l.event_id=k.event_id
        LEFT JOIN objects o ON o.log_id=l.log_id AND o.object_id=l.object_id AND o.object_type=?
        JOIN event_source_namespaces n ON n.log_id=k.log_id AND n.source_namespace=k.source_namespace
        WHERE k.log_id=? GROUP BY k.event_id,n.case_namespace
        HAVING count(DISTINCT o.object_id)!=1 OR NOT bool_and(coalesce(starts_with(o.object_id,'src:'||n.case_namespace||':'),false)) LIMIT 1''',[CASE_TYPE,log_id]).fetchone():
        raise EventLogFormatError('Source CSV requires exactly one consistent declared source case per event')
    writer=csv.writer(out);writer.writerow(['source_namespace','source_event_id','case_namespace','case_id','activity','timestamp','resource','attributes'])
    cursor=conn.execute('''SELECT k.source_namespace,k.source_event_id,n.case_namespace,
        substring(o.object_id,length('src:'||n.case_namespace||':')+1),e.activity,e.ts,e.resource,e.attributes
        FROM event_source_keys k JOIN event_source_namespaces n ON n.log_id=k.log_id AND n.source_namespace=k.source_namespace
        JOIN events e ON e.log_id=k.log_id AND e.event_id=k.event_id
        JOIN (SELECT DISTINCT log_id,event_id,object_id FROM event_objects) l ON l.log_id=e.log_id AND l.event_id=e.event_id
        JOIN objects o ON o.log_id=l.log_id AND o.object_id=l.object_id AND o.object_type=?
        WHERE k.log_id=? ORDER BY o.object_id,e.ts,e.event_id''',[CASE_TYPE,log_id])
    while rows:=cursor.fetchmany(10000):
        for ns,event,case,key,activity,stamp,resource,attributes in rows:
            writer.writerow([ns,event,case,key,activity,stamp.isoformat()+'Z',resource,attributes])
