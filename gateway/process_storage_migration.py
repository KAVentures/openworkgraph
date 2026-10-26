"""Separate legacy shared-process storage from two-component personal knowledge keys.

A coordinated Gateway upgrade is required: stop old-version workers first.
Only revisions attributed to the shared catalog are moved, with payload bytes
unchanged. Personal/unattributed rows stay personal. Conflicts fail closed.
"""
import json

MIGRATION = 'shared-process-domain-v2'


def migrate(store, conn):
    q=lambda sql,params=():store.db._execute(conn,sql,params)
    # Serialize creation and migration, including an empty first PostgreSQL deployment.
    if store.db.is_postgres:q('SELECT pg_advisory_xact_lock(1657281941, 2)')
    q('''CREATE TABLE IF NOT EXISTS gateway_process_storage_migrations (
        migration_id TEXT PRIMARY KEY, completed_at TEXT NOT NULL, moved_revisions INTEGER NOT NULL)''')
    if q('SELECT 1 FROM gateway_process_storage_migrations WHERE migration_id=?',(MIGRATION,)).fetchone():return
    roots=q('SELECT organization_id,workflow_id FROM gateway_shared_processes ORDER BY organization_id,workflow_id'+
        (' FOR UPDATE' if store.db.is_postgres else '')).fetchall()
    moved=0;org_counts={}
    for org,wid in roots:
        legacy=json.dumps(['organization-process-catalog',org],separators=(',',':'))
        target=json.dumps(['organization-process-catalog',org,'v2'],separators=(',',':'))
        rows=q('''SELECT k.revision,k.payload FROM workflow_knowledge k
            JOIN gateway_process_authors a ON a.organization_id=? AND a.workflow_id=k.workflow_id AND a.revision=k.revision
            WHERE k.owner=? AND k.workflow_id=? ORDER BY k.revision''',(org,legacy,wid)).fetchall()
        for revision,payload in rows:
            existing=q('SELECT payload FROM workflow_knowledge WHERE owner=? AND workflow_id=? AND revision=?',
                (target,wid,revision)).fetchone()
            if existing and existing[0]!=payload:
                raise RuntimeError('Conflicting shared process namespace migration; stop Gateway workers and restore/reconcile the database backup')
            if not existing:q('INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)',(target,wid,revision,payload))
            q('DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=? AND revision=?',(legacy,wid,revision))
            moved+=1;org_counts[org]=org_counts.get(org,0)+1
        if not q('SELECT 1 FROM workflow_knowledge WHERE owner=? AND workflow_id=? LIMIT 1',(target,wid)).fetchone():
            raise RuntimeError('Shared process history lacks publication attribution; stop Gateway workers and restore/reconcile the database backup')
    from .process_collaboration import _now
    for org,count in org_counts.items():
        q('''INSERT INTO audit_log(observed_at,organization_id,principal_id,action,details_json) VALUES (?,?,?,?,?)''',
            (_now(),org,'system:process-storage-migration','process.storage.namespace.migrated',json.dumps({'migration':MIGRATION,'moved_revisions':count})))
    q('INSERT INTO gateway_process_storage_migrations VALUES (?,?,?)',(MIGRATION,_now(),moved))
