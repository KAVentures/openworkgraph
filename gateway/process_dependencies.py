"""Declared organization dependencies with serialized cycle checks and live diagnostics."""
import json

from shared.process_dependencies import Saved, MAX_REVISIONS, MAX_ROOTS, MAX_PROCESSES, MAX_LINKS, content_digest, topology, impact
from .process_collaboration import ProcessCollaborationError as Error, _now
from .process_hierarchies import ProcessHierarchies

MAX_RESPONSE_BYTES=2*1024*1024


def namespace(org):return json.dumps(['organization-process-dependencies',org,'v1'],separators=(',',':'))


def erase(store,conn,org,wid):
    # Deletion only removes edges. It need not acquire the graph-save advisory
    # lock, particularly when the caller already holds a process metadata lock.
    store.db._execute(conn,'DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(org),wid))


class ProcessDependencies:
    def __init__(self,store):self.store=store;self.db=store.db;self.catalog=ProcessHierarchies(store)
    def q(self,c,sql,args=()):return self.db._execute(c,sql,args)

    def latest(self,c,p,wid):
        return int(self.q(c,'SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(p.organization_id),wid)).fetchone()[0] or 0)

    @staticmethod
    def decode(row,org,wid):
        revision,payload=row
        try:
            raw=json.loads(payload);digest=raw.pop('content_sha256');saved=Saved.model_validate(raw).model_dump(mode='json');d=saved['dependencies']
            if (not 1<=int(revision)<=MAX_REVISIONS or content_digest(saved)!=digest
                    or d['organization_id']!=org or d['root_process']['workflow_id']!=wid):raise ValueError('integrity')
        except (ValueError,TypeError,KeyError):raise Error('Stored process dependencies failed their integrity check') from None
        return {**saved,'revision':int(revision),'content_sha256':digest}

    def get(self,c,p,wid,revision=None):
        sql='SELECT revision,payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?';args=[namespace(p.organization_id),wid]
        if revision is not None:sql+=' AND revision=?';args.append(revision)
        row=self.q(c,sql+' ORDER BY revision DESC LIMIT 1',args).fetchone()
        return self.decode(row,p.organization_id,wid) if row else None

    def scan(self,c,p):
        rows=self.q(c,'''SELECT k.workflow_id,k.revision,k.payload FROM workflow_knowledge k
            WHERE k.owner=? AND k.revision=(SELECT MAX(v.revision) FROM workflow_knowledge v
                WHERE v.owner=k.owner AND v.workflow_id=k.workflow_id)
            ORDER BY k.workflow_id LIMIT ?''',(namespace(p.organization_id),MAX_ROOTS+1)).fetchall()
        return rows

    def detail(self,p,wid,revision=None,*,exporting=False):
        with self.catalog.reading(p) as c:
            meta=self.store._meta(c,p,wid);latest=self.latest(c,p,wid);integrity=True
            try:item=self.get(c,p,wid,revision)
            except Error:integrity=False;item=None
            if integrity and revision is not None and item is None:raise Error('Dependency revision not found',404)
            if exporting and (not item or not integrity):raise Error('A valid saved dependency declaration is required')
            role=self.store._role(c,p,wid,meta);resolutions=[]
            if item:
                d=item['dependencies'];refs=[('root',d['root_process'])]+[(link['id'],link['prerequisite']) for link in d['links']]
                for identifier,ref in refs:resolutions.append({'link_id':identifier,**self.catalog.resolve(c,p,ref)})
            result={'format':'openworkgraph.process-dependency-view.v1','artifact':item,'integrity_ok':integrity,
                'root_workflow_id':wid,'latest_revision':latest,'access_revision':meta['access_revision'],
                'can_write':integrity and not meta['archived'] and role in {'owner','editor','admin'} and 'org:process:write' in p.scopes,
                'can_delete':role in {'owner','admin'} and 'org:process:write' in p.scopes,'resolutions':resolutions,
                'all_references_current':bool(item) and all(r['current'] for r in resolutions),
                'all_references_reviewed':bool(item) and all(r.get('active_latest_independently_reviewed_model') for r in resolutions),
                'execution_authorized':False,'model_reviews_propagated':False,'normal_exports_include_dependencies':False}
        result['checked_at']=_now()
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,
            action='process.dependencies.exported' if exporting else 'process.dependencies.read',
            details={'workflow_id':wid,'revision':item['revision'] if item else 0})
        return result

    def save(self,p,wid,body):
        s=self.store;s.require_scope(p,'org:process:read');s.require_scope(p,'org:process:write')
        if not body.user_confirmed or not body.organization_sharing_confirmed:raise Error('Review and explicitly confirm sharing these dependency declarations',422)
        d=body.dependencies.model_dump(mode='json')
        if d['organization_id']!=p.organization_id or d['root_process']['workflow_id']!=wid:raise Error('Dependency root or organization differs from the selected process',422)
        refs=[d['root_process']]+[link['prerequisite'] for link in d['links']]
        with s.write_transaction() as c:
            # Serialize only dependency additions/replacements per organization,
            # before taking metadata locks in the same order as other multi-root
            # operations. READ COMMITTED sees the previous lock holder's commit.
            if self.db.is_postgres:self.q(c,'SELECT pg_advisory_xact_lock(hashtext(?),hashtext(?))',('owg-process-dependency-save-v1',p.organization_id))
            metas={key:s._meta(c,p,key,lock=True) for key in sorted({ref['workflow_id'] for ref in refs})}
            meta=metas[wid]
            if meta['archived'] or s._role(c,p,wid,meta) not in {'owner','editor','admin'}:raise Error('Current root process editor permission required',403)
            if meta['access_revision']!=body.expected_access_revision:raise Error('Root access changed; reload')
            latest=self.latest(c,p,wid)
            if latest!=body.expected_revision:raise Error('Dependency declarations changed; reload')
            if latest>=MAX_REVISIONS:raise Error('Dependencies retain at most twenty revisions; nothing was pruned')
            if latest:self.get(c,p,wid)  # Integrity failure requires owner erasure.
            for ref in refs:
                info=self.catalog.resolve(c,p,ref)
                if not info['available'] or not info['current']:raise Error('Every dependency reference must match an available active latest process revision')
            rows=self.scan(c,p)
            if len(rows)>MAX_ROOTS:raise Error('Dependency scan exceeds one hundred roots; no partial cycle check is used')
            declarations=[self.decode((revision,payload),p.organization_id,key)['dependencies'] for key,revision,payload in rows if key!=wid]
            try:topology([*declarations,d])
            except ValueError as exc:raise Error(str(exc)) from None
            saved=Saved(dependencies=body.dependencies,author_actor_id=p.actor_id,saved_at=_now()).model_dump(mode='json');digest=content_digest(saved)
            self.q(c,'INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)',
                (namespace(p.organization_id),wid,latest+1,json.dumps({**saved,'content_sha256':digest})))
            s._audit(c,p,'process.dependencies.saved',workflow_id=wid,revision=latest+1,content_sha256=digest,link_count=len(d['links']))
            return {'revision':latest+1,'content_sha256':digest}

    def delete(self,p,wid,body):
        s=self.store;s.require_scope(p,'org:process:read');s.require_scope(p,'org:process:write')
        with s.write_transaction() as c:
            meta=s._meta(c,p,wid,lock=True)
            if s._role(c,p,wid,meta) not in {'owner','admin'}:raise Error('Only a current root owner or administrator can erase dependency history',403)
            if not body.user_confirmed:raise Error('Confirm erasure of all dependency revisions',422)
            if meta['access_revision']!=body.expected_access_revision or self.latest(c,p,wid)!=body.expected_revision:raise Error('Dependencies or root access changed; reload')
            erase(s,c,p.organization_id,wid);s._audit(c,p,'process.dependencies.deleted',workflow_id=wid)
            return {'deleted':True}

    def snapshot(self,c,p,focus=None):
        rows=self.scan(c,p)
        result={'format':'openworkgraph.organization-process-dependencies.v1','organization_id':p.organization_id,
            'status':'withheld','graph':None,'impact':None,'blockers':[],
            'complete_declared_scan':len(rows)<=MAX_ROOTS,'current_basis':False,
            'dependency_coverage':'saved_human_declarations_only','business_dependency_completeness_verified':False,
            'causal_impact_verified':False,'execution_authorized':False,'benefit_totals_included':False,
            'max_roots':MAX_ROOTS,'max_processes':MAX_PROCESSES,'max_links_per_root':MAX_LINKS}
        evidence=[[wid,int(revision),content_digest(payload)] for wid,revision,payload in rows];items={}
        if len(rows)>MAX_ROOTS:result['blockers'].append({'code':'root_scan_limit'})
        else:
            for wid,revision,payload in rows:
                try:items[wid]=self.decode((revision,payload),p.organization_id,wid)
                except Error:result['blockers'].append({'code':'declaration_integrity_failed','workflow_id':wid})
        if not result['blockers']:
            try:graph=topology([item['dependencies'] for item in items.values()])
            except ValueError:result['blockers'].append({'code':'invalid_or_oversized_declared_graph'})
        if not result['blockers']:
            pins={wid:set() for wid in graph['process_ids']}
            for item in items.values():
                d=item['dependencies']
                for ref in [d['root_process']]+[link['prerequisite'] for link in d['links']]:pins[ref['workflow_id']].add((ref['revision'],ref['content_sha256']))
            nodes=[]
            for wid in graph['process_ids']:
                try:
                    latest=self.store._record(c,p,wid)
                    ref={key:latest[key] for key in ('workflow_id','revision','content_sha256')}
                    info=self.catalog.resolve(c,p,ref)
                except Error as exc:
                    if exc.status!=404:raise
                    info={'workflow_id':wid,'available':False,'state':'unavailable','current':False}
                except (ValueError,TypeError,KeyError):info={'workflow_id':wid,'available':False,'state':'integrity_failed','current':False}
                matches=bool(info['available']) and all((rev,digest)==(info['revision'],info['content_sha256']) for rev,digest in pins[wid])
                nodes.append({**info,'all_pins_match_latest':matches,'distinct_pin_count':len(pins[wid]),
                    'dependencies_assessed':wid in items,'declaration_revision':items[wid]['revision'] if wid in items else None,
                    'current_declared_basis':matches and info.get('active_latest_independently_reviewed_model',False) and wid in items})
            graph['nodes']=nodes
            # Retain exact edge lineage, but not reason text in the organization
            # overview. The per-root manifest retains that explicitly shared text.
            for edge in graph['edges']:
                item=items[edge['consumer']];d=item['dependencies'];link=next(link for link in d['links'] if link['id']==edge['link_id'])
                edge.update(consumer_process=d['root_process'],prerequisite_process=link['prerequisite'],
                    declaration_revision=item['revision'],declaration_sha256=item['content_sha256'])
            result['graph']=graph;result['current_basis']=bool(items) and all(n['current_declared_basis'] for n in nodes)
            result['status']='empty' if not items else 'current_declared_basis' if result['current_basis'] else 'needs_reassessment'
            if focus:
                try:result['impact']=impact(graph,focus)
                except ValueError:raise Error('Selected process is absent from the declared dependency graph',404) from None
            evidence.append([{key:value for key,value in node.items() if key!='title'} for node in nodes])
        result['fingerprint']=content_digest({'organization_id':p.organization_id,'records_and_resolutions':evidence,'blockers':result['blockers']})
        if len(json.dumps(result,allow_nan=False).encode())>MAX_RESPONSE_BYTES:
            result.update(status='withheld',graph=None,impact=None,current_basis=False,blockers=[{'code':'response_byte_limit'}])
        return result

    def graph(self,p,focus=None,*,expected_fingerprint=None,exporting=False):
        with self.catalog.reading(p) as c:
            result=self.snapshot(c,p,focus)
            if expected_fingerprint is not None and expected_fingerprint!=result['fingerprint']:raise Error('Dependency graph or source basis changed; refresh before exporting')
        result['checked_at']=_now()
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,
            action='process.dependencies.graph.exported' if exporting else 'process.dependencies.graph.read',
            details={'fingerprint':result['fingerprint'],'focus':focus,'complete_scan':result['complete_declared_scan']})
        return result
