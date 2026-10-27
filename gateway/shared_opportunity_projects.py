"""Exact shared opportunity/project metadata; no archive store or executor."""
from hashlib import sha256
import json
from zipfile import BadZipFile
from shared.analysis_snapshot import content_digest
from shared.process_agent_project import build_runtime_project,build_runtime_spec
from shared.shared_opportunity_project import Saved,MAX_PROJECTS,link_archive,spec_digest
from .process_collaboration import ProcessCollaborationError as Error,_now
from .process_opportunity_review import SharedOpportunityStore


def namespace(org):return json.dumps(['organization-opportunity-projects',org,'v1'],separators=(',',':'))
def pin(item):return {'workflow_id':item.get('workflow_id') or item['data']['process']['workflow_id'],'revision':item['revision'],'content_sha256':item['content_sha256']}
def erase(store,c,org,wid):store.db._execute(c,'DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(org),wid))


class SharedOpportunityProjects:
    def __init__(self,store):self.store=store;self.db=store.db;self.opportunities=SharedOpportunityStore(store);self.catalog=self.opportunities.catalog
    def q(self,c,sql,args=()):return self.db._execute(c,sql,args)
    def latest(self,c,p,wid):return int(self.q(c,'SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(p.organization_id),wid)).fetchone()[0] or 0)

    def records(self,c,p,wid):
        latest=self.latest(c,p,wid)
        if latest>MAX_PROJECTS:raise Error('Project registry exceeds its supported bound')
        rows=self.q(c,'SELECT revision,payload FROM workflow_knowledge WHERE owner=? AND workflow_id=? ORDER BY revision LIMIT 51',(namespace(p.organization_id),wid)).fetchall()
        if [int(row[0]) for row in rows]!=list(range(1,latest+1)):raise Error('Project registry is incomplete')
        result=[]
        try:
            for revision,payload in rows:
                raw=json.loads(payload);digest=raw.pop('content_sha256');saved=Saved.model_validate(raw).model_dump(mode='json')
                if content_digest(saved)!=digest or saved['manifest']['organization_id']!=p.organization_id or saved['manifest']['process']['workflow_id']!=wid:raise ValueError('integrity')
                result.append({**saved,'revision':int(revision),'content_sha256':digest})
            if len({r['manifest']['project_id'] for r in result})!=len(result):raise ValueError('duplicate')
        except (ValueError,TypeError,KeyError):raise Error('Stored project registry failed its integrity check') from None
        return result

    def approved_record(self,c,p,wid,opportunity):
        revision=opportunity['revision']-(1 if opportunity['state']=='implemented' else 0)
        approved=self.opportunities.get(c,p,wid,revision)
        if approved is None or approved['state']!='approved' or approved['approval']!=opportunity['approval']:raise Error('Exact named opportunity approval is unavailable')
        return approved

    def basis_status(self,c,p,wid,manifest,current):
        reasons=[];latest=current['artifact']
        try:
            origin=self.opportunities.get(c,p,wid,manifest['opportunity']['revision'])
            if origin is None or pin(origin)!=manifest['opportunity']:raise Error('unavailable')
        except Error:origin=None;reasons.append('exported_opportunity_unavailable_or_changed')
        if not current['integrity_ok'] or latest is None:reasons.append('current_opportunity_unavailable_or_corrupt')
        else:
            if not current['current_approved_assessment']:reasons.append('current_independent_opportunity_approval_required')
            if latest['data']['process']!=manifest['process']:reasons.append('process_reference_changed')
            if latest['assessment_sha256']!=manifest['assessment_sha256'] or latest['assessed_in_revision']!=manifest['assessed_in_revision']:reasons.append('opportunity_assessment_changed')
            try:approved=self.approved_record(c,p,wid,latest)
            except Error:reasons.append('current_named_approval_unavailable')
            else:
                if pin(approved)!=manifest['approved_opportunity']:reasons.append('opportunity_approval_replaced')
        return {'current_approved_assessment_basis':not reasons,'opportunity_snapshot_is_latest':latest is not None and pin(latest)==manifest['opportunity'],
            'blockers':reasons,'archive_reproduction_status':'not_rechecked','execution_authorized':False,'deployment_verified':False,'per_run_binary_attribution_verified':False,'business_outcomes_verified':False}

    def detail(self,p,wid,*,exporting_project=None):
        with self.catalog.reading(p) as c:
            meta=self.store._meta(c,p,wid);permissions=self.opportunities.permissions(c,p,wid,meta);latest=self.latest(c,p,wid)
            try:records=self.records(c,p,wid);integrity=True
            except Error:records=[];integrity=False
            current=self.opportunities.view(c,p,wid)
            if exporting_project is not None:
                if not integrity:raise Error('A valid project registry is required for manifest export')
                records=[r for r in records if r['manifest']['project_id']==exporting_project]
                if not records:raise Error('Shared opportunity project not found',404)
            result={'format':'openworkgraph.shared-opportunity-projects-view.v1','organization_id':p.organization_id,'root_workflow_id':wid,
                'integrity_ok':integrity,'latest_project_revision':latest,'max_projects':MAX_PROJECTS,'access_revision':meta['access_revision'],
                'permissions':permissions,'can_generate':integrity and permissions['edit'] and current['current_approved_assessment'],
                'current_opportunity':pin(current['artifact']) if current['artifact'] else None,
                'items':[{**r,'source_status':self.basis_status(c,p,wid,r['manifest'],current)} for r in reversed(records)],
                'archives_stored':False,'execution_authorized':False,'spending_authorized':False,'deployment_verified':False,'per_run_binary_attribution_verified':False,'business_outcomes_verified':False}
        result['checked_at']=_now();self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,
            action='process.opportunity_project.manifest.exported' if exporting_project else 'process.opportunity_projects.read',
            details={'workflow_id':wid,'returned':len(result['items'])})
        return result

    def current(self,c,p,wid,access_revision):
        meta=self.store._meta(c,p,wid,lock=True)
        if not self.opportunities.permissions(c,p,wid,meta)['edit']:raise Error('Current root editor permission required for project generation',403)
        if meta['access_revision']!=access_revision:raise Error('Root access changed; reload before project generation')
        return self.opportunities.view(c,p,wid)

    def packet(self,c,p,wid,reference):
        detail=self.store._detail(c,p,wid,revision=reference['revision']);record=detail['process']
        if pin(record)!=reference or detail['archived'] or detail['viewing_historical_revision'] or detail['review']['status']!='reviewed_design':raise Error('An exact active latest independently reviewed process is required')
        return {'format':'openworkgraph.versioned-process-export.v1',**{k:record[k] for k in ('workflow_id','revision','content_sha256','process_model')},
            'execution_authorization':False,'organization_catalog_reference':{'organization_id':p.organization_id}}

    def generate(self,p,wid,body):
        self.store.require_scope(p,'org:process:read');self.store.require_scope(p,'org:process:write')
        if not body.user_confirmed:raise Error('Review this exact approved opportunity before generating its disabled starter',422)
        with self.store.write_transaction() as c:
            current=self.current(c,p,wid,body.expected_access_revision);opportunity=current['artifact'];records=self.records(c,p,wid)
            if body.expected_project_revision!=len(records):raise Error('Project registry changed; reload')
            if not current['current_approved_assessment'] or opportunity is None or pin(opportunity)!=body.expected_opportunity.model_dump(mode='json'):raise Error('Select the exact current independently approved opportunity assessment')
            approved=self.approved_record(c,p,wid,opportunity);reference=opportunity['data']['process'];packet=self.packet(c,p,wid,reference)
            try:
                base=build_runtime_project(packet);archive,manifest=link_archive(base,{
                    'organization_id':p.organization_id,'opportunity':pin(opportunity),'approved_opportunity':pin(approved),'process':reference,
                    'assessment_sha256':opportunity['assessment_sha256'],'assessed_in_revision':opportunity['assessed_in_revision'],
                    'spec_sha256':spec_digest(build_runtime_spec(packet)),'base_archive_sha256':sha256(base).hexdigest()})
            except (ValueError,BadZipFile,KeyError):raise Error('Exact process project could not be generated; no record was saved') from None
            archive_sha=sha256(archive).hexdigest();existing=next((r for r in records if r['manifest']['project_id']==manifest['project_id']),None)
            if existing:
                if existing['archive_sha256']!=archive_sha:raise Error('Generated artifact changed; previous metadata was preserved')
                record=existing
            else:
                if len(records)>=MAX_PROJECTS:raise Error('At most fifty distinct shared projects are retained; nothing was pruned')
                saved=Saved(manifest=manifest,archive_sha256=archive_sha,created_at=_now(),created_by_actor_id=p.actor_id).model_dump(mode='json')
                digest=content_digest(saved);revision=len(records)+1
                self.q(c,'INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)',(namespace(p.organization_id),wid,revision,json.dumps({**saved,'content_sha256':digest})))
                record={**saved,'revision':revision,'content_sha256':digest}
            self.store._audit(c,p,'process.opportunity_project.generated',workflow_id=wid,project_id=manifest['project_id'],project_revision=record['revision'],archive_sha256=archive_sha,deduplicated=existing is not None)
        return archive,record

    def regenerate(self,p,wid,project_id,body):
        self.store.require_scope(p,'org:process:read');self.store.require_scope(p,'org:process:write')
        if not body.user_confirmed:raise Error('Confirm exact archive regeneration; this does not authorize execution',422)
        with self.store.write_transaction() as c:
            current=self.current(c,p,wid,body.expected_access_revision);records=self.records(c,p,wid)
            record=next((r for r in records if r['manifest']['project_id']==project_id),None)
            if record is None:raise Error('Shared opportunity project not found',404)
            if record['content_sha256']!=body.expected_record_sha256:raise Error('Project record differs; reload')
            manifest=record['manifest']
            if not self.basis_status(c,p,wid,manifest,current)['current_approved_assessment_basis']:raise Error('Project basis changed or is unavailable; inspect its historical manifest')
            packet=self.packet(c,p,wid,manifest['process'])
            try:archive,generated=link_archive(build_runtime_project(packet),{k:v for k,v in manifest.items() if k!='project_id'})
            except (ValueError,BadZipFile,KeyError):raise Error('Current generator cannot reproduce this archived digest; metadata remains available') from None
            if generated!=manifest or sha256(archive).hexdigest()!=record['archive_sha256']:raise Error('Current generator cannot reproduce the exact archive; metadata remains available')
            self.store._audit(c,p,'process.opportunity_project.regenerated',workflow_id=wid,project_id=project_id,archive_sha256=record['archive_sha256'])
        return archive,record

    def delete(self,p,wid,body):
        self.store.require_scope(p,'org:process:read');self.store.require_scope(p,'org:process:write')
        with self.store.write_transaction() as c:
            meta=self.store._meta(c,p,wid,lock=True)
            if not self.opportunities.permissions(c,p,wid,meta)['delete']:raise Error('Only the current root owner or administrator can erase project history',403)
            if not body.user_confirmed:raise Error('Confirm erasure of all project metadata; downloaded copies remain separate',422)
            if body.expected_access_revision!=meta['access_revision'] or body.expected_project_revision!=self.latest(c,p,wid):raise Error('Project registry or root access changed; reload')
            erase(self.store,c,p.organization_id,wid);self.store._audit(c,p,'process.opportunity_projects.deleted',workflow_id=wid)
        return {'deleted':True}
