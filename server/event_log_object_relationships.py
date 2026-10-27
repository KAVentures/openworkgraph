"""Retained caller-supplied OCEL O2O assertions, separate from co-event links."""
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
        from .event_log_quality import _set_digest
        profile['relationships_sha256']=_set_digest(conn,'''SELECT sha256(to_json(
            [source_object_id,target_object_id,qualifier])) AS h FROM object_relationships WHERE log_id=?''',[log_id])
        profile['digest_scope']='retained_whole_log_assertions'
    return {'object_relationships':profile}


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
