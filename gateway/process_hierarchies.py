"""Versioned repository maps in the existing knowledge store, under a separate namespace."""
from contextlib import contextmanager
import hashlib
import json

from shared.process_hierarchy import Saved, content_digest
from .process_collaboration import ProcessCollaborationError as Error, _now

MAX_REVISIONS = 20


def namespace(org):return json.dumps(['organization-process-hierarchies',org,'v1'],separators=(',',':'))


def erase(store,conn,org,workflow_id):
    store.db._execute(conn,'DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(org),workflow_id))


class ProcessHierarchies:
    def __init__(self,store):self.store=store;self.db=store.db

    def q(self,c,sql,params=()):return self.db._execute(c,sql,params)

    @contextmanager
    def reading(self,p):
        self.store.require_scope(p,'org:process:read')
        with self.db.connect() as c:
            self.q(c,'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY' if self.db.is_postgres else 'BEGIN')
            yield c

    def preflight(self,p,workflow_id,*,writing=False,owner=False):
        self.store.require_scope(p,'org:process:read')
        if writing:self.store.require_scope(p,'org:process:write')
        with self.db.connect() as c:
            meta=self.store._meta(c,p,workflow_id)
            role=self.store._role(c,p,workflow_id,meta)
            if writing and ((meta['archived'] and not owner) or role not in ({'owner','admin'} if owner else {'owner','editor','admin'})):
                raise Error('Current root process editor permission required',403)

    @staticmethod
    def decode(row,org,workflow_id):
        revision,payload=row
        try:
            raw=json.loads(payload);digest=raw.pop('content_sha256')
            saved=Saved.model_validate(raw).model_dump(mode='json')
            h=saved['hierarchy']
            if (content_digest(saved)!=digest or h['organization_id']!=org
                    or next(n for n in h['nodes'] if n['id']=='root')['process']['workflow_id']!=workflow_id):raise ValueError('integrity')
        except (ValueError,KeyError,TypeError,StopIteration):raise Error('Stored repository map failed its integrity check') from None
        return {**saved,'content_sha256':digest,'revision':int(revision)}

    def get(self,c,p,workflow_id,revision=None):
        sql='SELECT revision,payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?';params=[namespace(p.organization_id),workflow_id]
        if revision is not None:sql+=' AND revision=?';params.append(revision)
        row=self.q(c,sql+' ORDER BY revision DESC LIMIT 1',params).fetchone()
        return self.decode(row,p.organization_id,workflow_id) if row else None

    def latest(self,c,p,workflow_id):
        return int(self.q(c,'SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',
            (namespace(p.organization_id),workflow_id)).fetchone()[0] or 0)

    def resolve(self,c,p,reference):
        """Every resolution uses the current caller's organization and process-read scope."""
        self.store.require_scope(p,'org:process:read')
        wid=reference['workflow_id'];expected=reference['content_sha256']
        try:
            meta=self.store._meta(c,p,wid)
            record=self.store._record(c,p,wid,reference['revision'])
            latest=self.store._record(c,p,wid)['revision']
        except Error as exc:
            if exc.status!=404:raise
            return {**reference,'available':False,'state':'unavailable','current':False}
        except (ValueError,TypeError):
            return {**reference,'available':False,'state':'integrity_failed','current':False}
        try:
            from shared.process_model import ProcessModel
            ProcessModel.model_validate(record['process_model'])
            if not isinstance(record['title'],str):raise ValueError('title')
            # Validate the existing knowledge payload hash without trusting its declared digest.
            check={k:v for k,v in record.items() if k not in {'revision','content_sha256'}}
            actual=hashlib.sha256(json.dumps(check,sort_keys=True).encode()).hexdigest()
            if actual!=record['content_sha256'] or actual!=expected:raise ValueError('digest')
            if not self.q(c,'SELECT 1 FROM gateway_process_authors WHERE organization_id=? AND workflow_id=? AND revision=?',(p.organization_id,wid,reference['revision'])).fetchone():raise ValueError('unattributed revision')
        except (KeyError,TypeError,ValueError):
            return {**reference,'available':False,'state':'integrity_failed','current':False}
        review=self.store._review_state(c,p,wid,reference['revision'])['status']
        current=not meta['archived'] and latest==reference['revision']
        return {**reference,'available':True,'title':record['title'],'latest_revision':latest,
            'archived':meta['archived'],'state':'archived' if meta['archived'] else 'current' if current else 'revision_changed',
            'current':current,'independent_model_review':review,
            'active_latest_independently_reviewed_model':current and review=='reviewed_design'}

    def detail(self,p,workflow_id,revision=None,*,exporting=False):
        with self.reading(p) as c:
            meta=self.store._meta(c,p,workflow_id)
            integrity=True
            try:item=self.get(c,p,workflow_id,revision)
            except Error:integrity=False;item=None
            latest=self.latest(c,p,workflow_id)
            if exporting and (not integrity or not item):raise Error('A valid saved repository map is required for export')
            role=self.store._role(c,p,workflow_id,meta)
            resolved=[];memo={}
            if item:
                for node in item['hierarchy']['nodes']:
                    ref=node['process'];key=(ref['workflow_id'],ref['revision'],ref['content_sha256'])
                    if key not in memo:memo[key]=self.resolve(c,p,ref)
                    resolved.append({'node_id':node['id'],**memo[key]})
            if integrity and revision is not None and item is None:raise Error('Repository map revision not found',404)
            result={'format':'openworkgraph.process-hierarchy-view.v1','artifact':item,'integrity_ok':integrity,'latest_revision':latest,
                'access_revision':meta['access_revision'],'root_workflow_id':workflow_id,
                'can_write':integrity and not meta['archived'] and role in {'owner','editor','admin'} and 'org:process:write' in p.scopes,
                'can_delete':role in {'owner','admin'} and 'org:process:write' in p.scopes,
                'resolutions':resolved,'all_references_current':bool(item) and all(n['current'] for n in resolved),
                'declared_node_count':len(resolved) if integrity else None,'unique_referenced_processes':len({n['workflow_id'] for n in resolved}) if integrity else None,
                'model_reviews_propagated':False,'execution_authorized':False,'benefit_rollup_included':False,
                'bpmn_call_semantics_included':False,'implementation_exports_include_hierarchy':False}
        # A read-only snapshot cannot also write an audit on PostgreSQL.
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,
            action='process.hierarchy.exported' if exporting else 'process.hierarchy.read',
            details={'workflow_id':workflow_id,'revision':item['revision'] if item else 0,'node_count':len(resolved)})
        return result

    def save(self,p,workflow_id,body):
        s=self.store;s.require_scope(p,'org:process:read');s.require_scope(p,'org:process:write')
        if not body.user_confirmed or not body.organization_sharing_confirmed:raise Error('Review and explicitly confirm sharing this repository map',422)
        h=body.hierarchy.model_dump(mode='json')
        if h['organization_id']!=p.organization_id or body.hierarchy.root().process.workflow_id!=workflow_id:raise Error('Repository root or organization differs from the selected process',422)
        with s.write_transaction() as c:
            # Match order across concurrent maps sharing/reusing processes; process writes lock one metadata row.
            metas={wid:s._meta(c,p,wid,lock=True) for wid in sorted({n['process']['workflow_id'] for n in h['nodes']})}
            meta=metas[workflow_id];role=s._role(c,p,workflow_id,meta)
            if meta['archived'] or role not in {'owner','editor','admin'}:raise Error('Current root process editor permission required',403)
            if body.expected_access_revision!=meta['access_revision']:raise Error('Root access changed; reload')
            root_info=None
            for n in h['nodes']:
                info=self.resolve(c,p,n['process'])
                if not info['available'] or info['archived']:raise Error('Every reference must resolve to an available active process revision')
                if n['id']=='root':root_info=info
            if root_info['latest_revision']!=body.hierarchy.root().process.revision:raise Error('Root process changed; select its latest revision')
            latest=self.latest(c,p,workflow_id)
            if latest!=body.expected_revision:raise Error('Repository map changed; reload')
            if latest>=MAX_REVISIONS:raise Error('Repository maps retain at most twenty revisions; nothing was pruned')
            saved=Saved(hierarchy=body.hierarchy,author_actor_id=p.actor_id,saved_at=_now()).model_dump(mode='json')
            digest=content_digest(saved);payload={**saved,'content_sha256':digest}
            self.q(c,'INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)',
                (namespace(p.organization_id),workflow_id,latest+1,json.dumps(payload)))
            s._audit(c,p,'process.hierarchy.saved',workflow_id=workflow_id,revision=latest+1,content_sha256=digest,node_count=len(h['nodes']))
            return {'revision':latest+1,'content_sha256':digest}

    def delete(self,p,workflow_id,body):
        s=self.store;s.require_scope(p,'org:process:read');s.require_scope(p,'org:process:write')
        with s.write_transaction() as c:
            meta=s._meta(c,p,workflow_id,lock=True)
            if s._role(c,p,workflow_id,meta) not in {'owner','admin'}:raise Error('Only a current root owner or administrator can erase map history',403)
            if not body.user_confirmed:raise Error('Confirm deletion of all map revisions',422)
            if body.expected_access_revision!=meta['access_revision'] or body.expected_revision!=self.latest(c,p,workflow_id):raise Error('Repository map or root access changed; reload')
            erase(s,c,p.organization_id,workflow_id)
            s._audit(c,p,'process.hierarchy.deleted',workflow_id=workflow_id)
            return {'deleted':True}

    def inventory(self,p,after='',limit=50):
        with self.reading(p) as c:
            rows=self.q(c,'''SELECT k.workflow_id,k.revision,k.payload FROM workflow_knowledge k
                WHERE k.owner=? AND k.workflow_id>? AND k.revision=(SELECT MAX(v.revision)
                    FROM workflow_knowledge v WHERE v.owner=k.owner AND v.workflow_id=k.workflow_id)
                ORDER BY k.workflow_id LIMIT ?''',(namespace(p.organization_id),after,limit+1)).fetchall()
            items=[]
            for wid,rev,payload in rows[:limit]:
                try:meta=self.store._meta(c,p,wid)
                except Error:continue
                try:
                    item=self.decode((rev,payload),p.organization_id,wid);h=item['hierarchy']
                    items.append({'workflow_id':wid,'revision':int(rev),'title':h['title'],'category':h['category'],
                        'capability':h['capability'],'declared_node_count':len(h['nodes']),'root_archived':meta['archived'],'integrity_ok':True})
                except Error:items.append({'workflow_id':wid,'revision':int(rev),'title':'Unavailable: integrity check failed','integrity_ok':False})
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,action='process.hierarchy.inventory.read',details={'returned':len(items)})
        return {'maps':items,'has_more':len(rows)>limit,'next_after':rows[limit-1][0] if len(rows)>limit else None,
            'filter_scope':'loaded_page_only','execution_authorized':False}
