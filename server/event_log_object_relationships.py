"""Retained caller-supplied OCEL O2O assertions, separate from co-event links."""
from contextlib import contextmanager
import hashlib
import json
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,ValidationError,field_validator
from shared.event_log_formats import _exact_key
from shared.event_log_formats import EventLogFormatError,MAX_OBJECT_RELATIONSHIPS,MAX_LOG_OBJECT_RELATIONSHIPS

FORMAT='openworkgraph.supplied-ocel-object-relationships.v1'
MAX_RELATIONSHIPS=MAX_LOG_OBJECT_RELATIONSHIPS
SCHEMA='''
CREATE TABLE IF NOT EXISTS object_relationships (
 log_id VARCHAR NOT NULL,source_object_id VARCHAR NOT NULL,target_object_id VARCHAR NOT NULL,qualifier VARCHAR NOT NULL,
 PRIMARY KEY(log_id,source_object_id,target_object_id,qualifier));
'''


def count(conn,log_id):
    return conn.execute('SELECT count(*) FROM object_relationships WHERE log_id=?',[log_id]).fetchone()[0]


def validate_index(conn,log_id):
    if not count(conn,log_id):return
    if count(conn,log_id)>MAX_RELATIONSHIPS:
        raise EventLogFormatError('The retained object relationship count exceeds the supported limit')
    if conn.execute('''SELECT source_object_id FROM object_relationships WHERE log_id=?
        GROUP BY 1 HAVING count(*)>? LIMIT 1''',[log_id,MAX_OBJECT_RELATIONSHIPS]).fetchone():
        raise EventLogFormatError('One object has more supplied relationships than the supported limit')
    if conn.execute('''SELECT 1 FROM object_relationships r
        LEFT JOIN objects s ON s.log_id=r.log_id AND s.object_id=r.source_object_id
        LEFT JOIN objects t ON t.log_id=r.log_id AND t.object_id=r.target_object_id
        WHERE r.log_id=? AND (s.object_id IS NULL OR t.object_id IS NULL) LIMIT 1''',[log_id]).fetchone():
        raise EventLogFormatError('A supplied object relationship has a missing source or target object')
    if conn.execute('SELECT object_id FROM objects WHERE log_id=? GROUP BY 1 HAVING count(*)>1 LIMIT 1',[log_id]).fetchone():
        raise EventLogFormatError('Object relationship endpoints have ambiguous identifiers')
    if conn.execute('''SELECT 1 FROM object_relationships WHERE log_id=? AND
        (length(source_object_id)>200 OR length(target_object_id)>200 OR length(qualifier)>80
         OR trim(source_object_id)='' OR trim(target_object_id)='' OR
         regexp_matches(source_object_id||target_object_id||qualifier,?)) LIMIT 1''',
        [log_id,r'[\x00-\x1f\x7f-\x9f\x{fffe}\x{ffff}]']).fetchone():
        raise EventLogFormatError('The saved object relationship index contains unsupported values')


def metadata(conn,log_id,*,include_digest=False):
    retained=count(conn,log_id)
    if not retained:return {}
    profile={'format':FORMAT,'retained_relationships':retained,'basis':'caller_supplied_ocel_object_relationships',
        'independently_verified':False,'direction':'source_to_target','case_membership_inferred':False,
        'causal_claims':False,'execution_authorized':False,'historical_completeness':'unknown',
        'counts_describe':'retained_assertion_set_not_execution_frequency',
        'relationship_preserving_formats':['ocel','ocelxml']}
    if include_digest:
        profile['relationships_sha256']=_relationship_digest(conn,log_id)
        profile['digest_scope']='retained_whole_log_assertions'
    return {'object_relationships':profile}


def _relationship_digest(conn,log_id):
    from .event_log_quality import _set_digest
    return _set_digest(conn,'''SELECT sha256(to_json([source_object_id,target_object_id,qualifier])) AS h
        FROM object_relationships WHERE log_id=?''',[log_id])


def snapshot(conn,log_id):
    """Pin the exact retained graph, including the empty set; not source completeness."""
    from .event_log_quality import _set_digest
    validate_index(conn,log_id)
    return {'format':FORMAT,'retained_relationships':count(conn,log_id),
        'relationships_sha256':_relationship_digest(conn,log_id),'digest_scope':'retained_whole_log_assertions',
        'retained_objects':conn.execute('SELECT count(*) FROM objects WHERE log_id=?',[log_id]).fetchone()[0],
        'object_index_sha256':_set_digest(conn,'''SELECT sha256(to_json([object_id,object_type])) AS h
            FROM objects WHERE log_id=?''',[log_id]),'object_digest_scope':'retained_object_ids_and_types',
        'revision':_revision(conn,log_id),'revision_basis':'local_log_append_batch',
        'basis':'caller_supplied_ocel_object_relationships','independently_verified':False,
        'source_completeness':'unknown','relationship_validity':'unknown','execution_authorized':False}


def load_csv(conn,log_id,path,opts):
    if not path.stat().st_size:return
    conn.execute(f'''INSERT INTO object_relationships SELECT DISTINCT ?,c0,c1,coalesce(c2,'')
        FROM read_csv(?,{opts},columns={{'c0':'VARCHAR','c1':'VARCHAR','c2':'VARCHAR'}})''',[log_id,str(path)])
    validate_index(conn,log_id)


def copy_append(conn,log_id,staging):
    added=conn.execute('''INSERT INTO object_relationships SELECT ?,s.source_object_id,s.target_object_id,s.qualifier
        FROM object_relationships s WHERE s.log_id=? AND NOT EXISTS(
        SELECT 1 FROM object_relationships t WHERE t.log_id=? AND t.source_object_id=s.source_object_id
        AND t.target_object_id=s.target_object_id AND t.qualifier=s.qualifier)''',[log_id,staging,log_id]).fetchone()[0]
    validate_index(conn,log_id)
    return added


def export_map(conn,log_id):
    validate_index(conn,log_id)
    result={}
    cursor=conn.execute('''SELECT source_object_id,target_object_id,qualifier FROM object_relationships
        WHERE log_id=? ORDER BY 1,2,3''',[log_id])
    while rows:=cursor.fetchmany(10000):
        for source,target,qualifier in rows:result.setdefault(source,[]).append((target,qualifier))
    return result


class RelationshipCursor(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    revision:int=Field(ge=0)
    query_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    after:list[str]=Field(min_length=3,max_length=3)

    @field_validator('after')
    @classmethod
    def literal_tuple(cls,values):
        if not _exact_key(values[0]) or not _exact_key(values[1]):raise ValueError('Literal identifiers required')
        _exact_key(values[2],80)
        return values


class RelationshipQuery(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    object_id:str|None=Field(default=None,max_length=200)
    direction:Literal['both','outgoing','incoming']='both'
    source_type:str|None=Field(default=None,max_length=200)
    target_type:str|None=Field(default=None,max_length=200)
    qualifier:str|None=Field(default=None,max_length=80)
    limit:int=Field(default=50,ge=1,le=200)
    cursor:RelationshipCursor|None=None

    @field_validator('object_id','source_type','target_type')
    @classmethod
    def literal_identifier(cls,value):
        if value is not None and not _exact_key(value):raise ValueError('A literal identifier is required')
        return value

    @field_validator('qualifier')
    @classmethod
    def literal_qualifier(cls,value):
        if value is not None:_exact_key(value,80)
        return value


@contextmanager
def _read_log(log_id):
    from . import event_log_workspace as workspace
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            info=workspace._check_log(conn,log_id)
            validate_index(conn,log_id)
            yield conn,info
        except EventLogFormatError as error:
            raise workspace.EventLogWorkspaceError(str(error)) from None
        finally:conn.close()


def _revision(conn,log_id):
    return int(conn.execute('SELECT coalesce(max(batch),0) FROM log_appends WHERE log_id=?',[log_id]).fetchone()[0])


def overview(log_id):
    with _read_log(log_id) as (conn,info):
        groups=conn.execute('''SELECT s.object_type,t.object_type,r.qualifier,count(*),
            count(DISTINCT r.source_object_id),count(DISTINCT r.target_object_id),count(*) OVER()
            FROM object_relationships r JOIN objects s ON s.log_id=r.log_id AND s.object_id=r.source_object_id
            JOIN objects t ON t.log_id=r.log_id AND t.object_id=r.target_object_id
            WHERE r.log_id=? GROUP BY 1,2,3 ORDER BY 4 DESC,1,2,3 LIMIT 201''',[log_id]).fetchall()
        return {'log':info,'revision':_revision(conn,log_id),'revision_basis':'local_log_append_batch',
            'retained_relationships':count(conn,log_id),'groups_total':int(groups[0][-1]) if groups else 0,
            'groups_truncated':len(groups)>200,'groups':[{'source_type':s,'target_type':t,'qualifier':q,
                'relationships':n,'source_objects':ns,'target_objects':nt} for s,t,q,n,ns,nt,_ in groups[:200]],
            'basis':'caller_supplied_ocel_object_relationships','independently_verified':False,
            'case_membership_inferred':False,'causal_claims':False,'execution_authorized':False,
            'historical_completeness':'unknown','static_relationships_have_no_observed_validity_period':True,
            'shared_event_associations_are_separate':True}


def query(log_id,spec):
    from . import event_log_workspace as workspace
    try:settings=RelationshipQuery.model_validate(spec)
    except ValidationError:
        raise workspace.EventLogWorkspaceError('Use bounded literal object/type/qualifier filters, a supported direction, limit from 1 to 200 and a current cursor') from None
    if settings.object_id is None and settings.direction!='both':
        raise workspace.EventLogWorkspaceError('Choose an object before filtering incoming or outgoing relationships')
    basis=settings.model_dump(exclude={'cursor','limit'})
    digest=hashlib.sha256(json.dumps({'log_id':log_id,**basis},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    where,params=['r.log_id=?'],[log_id]
    if settings.object_id is not None:
        if settings.direction=='both':
            where.append('(r.source_object_id=? OR r.target_object_id=?)');params.extend([settings.object_id]*2)
        else:
            column='source_object_id' if settings.direction=='outgoing' else 'target_object_id'
            where.append('r.'+column+'=?');params.append(settings.object_id)
    for name,column in [('source_type','s.object_type'),('target_type','t.object_type'),('qualifier','r.qualifier')]:
        value=getattr(settings,name)
        if value is not None:where.append(column+'=?');params.append(value)
    joins=''' FROM object_relationships r JOIN objects s ON s.log_id=r.log_id AND s.object_id=r.source_object_id
        JOIN objects t ON t.log_id=r.log_id AND t.object_id=r.target_object_id WHERE '''
    with _read_log(log_id) as (conn,info):
        revision=_revision(conn,log_id)
        if settings.cursor and (settings.cursor.revision!=revision or settings.cursor.query_sha256!=digest):
            raise workspace.EventLogWorkspaceError('The relationship data or filters changed; restart from the first page')
        selected=None
        if settings.object_id is not None:
            object_type=conn.execute('SELECT object_type FROM objects WHERE log_id=? AND object_id=?',[log_id,settings.object_id]).fetchone()
            if not object_type:raise workspace.EventLogWorkspaceError('The selected object was not found in this log')
            observed=conn.execute('''SELECT count(DISTINCT eo.event_id) FROM event_objects eo
                JOIN events e ON e.log_id=eo.log_id AND e.event_id=eo.event_id WHERE eo.log_id=? AND eo.object_id=?''',
                [log_id,settings.object_id]).fetchone()[0]
            selected={'object_id':settings.object_id,'object_type':object_type[0],'observed_events':observed}
        total=conn.execute('SELECT count(*)'+joins+' AND '.join(where),params).fetchone()[0]
        if settings.cursor:
            where.append('ROW(r.source_object_id,r.target_object_id,r.qualifier)>ROW(?,?,?)');params.extend(settings.cursor.after)
        rows=conn.execute('''WITH page AS (SELECT r.source_object_id,r.target_object_id,r.qualifier,
              s.object_type AS source_type,t.object_type AS target_type'''+joins+' AND '.join(where)+'''
              ORDER BY 1,2,3 LIMIT ?),
            refs AS (SELECT source_object_id AS object_id FROM page UNION SELECT target_object_id FROM page),
            observed AS (SELECT eo.object_id,count(DISTINCT eo.event_id) AS n FROM event_objects eo
              JOIN refs ON refs.object_id=eo.object_id JOIN events e ON e.log_id=eo.log_id AND e.event_id=eo.event_id
              WHERE eo.log_id=? GROUP BY 1)
            SELECT p.*,coalesce(s.n,0),coalesce(t.n,0) FROM page p
              LEFT JOIN observed s ON s.object_id=p.source_object_id
              LEFT JOIN observed t ON t.object_id=p.target_object_id ORDER BY 1,2,3''',
            [*params,settings.limit+1,log_id]).fetchall()
        more=len(rows)>settings.limit;shown=rows[:settings.limit]
        cursor=({'revision':revision,'query_sha256':digest,'after':list(shown[-1][:3])} if more else None)
        return {'log':info,'revision':revision,'revision_basis':'local_log_append_batch',
            'matching_relationships':int(total),'has_more':more,'next_cursor':cursor,'selected_object':selected,
            'rows':[{'source_id':source,'target_id':target,'qualifier':qualifier,'source_type':st,'target_type':tt,
                     'source_observed_events':ns,'target_observed_events':nt} for source,target,qualifier,st,tt,ns,nt in shown],
            'scope':'matching_retained_supplied_assertions','independently_verified':False,
            'case_membership_inferred':False,'causal_claims':False,'execution_authorized':False,
            'historical_completeness':'unknown','static_relationships_have_no_observed_validity_period':True}
