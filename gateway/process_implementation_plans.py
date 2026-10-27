"""Versioned implementation planning over existing shared opportunity evidence."""
import json
from decimal import Decimal
from shared.process_implementation_plan import Saved,Review,ReportAttribution,MAX_REVISIONS,content_digest,totals
from .process_collaboration import ProcessCollaborationError as Error,_now
from .process_opportunity_review import SharedOpportunityStore


def namespace(org):return json.dumps(['organization-process-implementation-plans',org,'v1'],separators=(',',':'))
def erase(store,c,org,wid):store.db._execute(c,'DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(org),wid))
def pin(record):return {'workflow_id':record.get('workflow_id') or record['data']['process']['workflow_id'],'revision':record['revision'],'content_sha256':record['content_sha256']}


class ImplementationPlans:
    def __init__(self,store):self.store=store;self.db=store.db;self.opportunities=SharedOpportunityStore(store);self.catalog=self.opportunities.catalog
    def q(self,c,sql,args=()):return self.db._execute(c,sql,args)
    def latest(self,c,p,wid):return int(self.q(c,'SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(p.organization_id),wid)).fetchone()[0] or 0)

    def decode(self,row,org,wid):
        revision,payload=row
        try:
            raw=json.loads(payload);digest=raw.pop('content_sha256');saved=Saved.model_validate(raw).model_dump(mode='json')
            if (not 1<=int(revision)<=MAX_REVISIONS or content_digest(saved)!=digest or saved['organization_id']!=org
                or saved['baseline']['opportunity']['workflow_id']!=wid or saved['assessed_in_revision']>int(revision)
                or any(r['reported_in_revision']>int(revision) for r in saved['report_attributions'])):raise ValueError('integrity')
        except (ValueError,TypeError,KeyError):raise Error('Stored implementation plan failed its integrity check') from None
        return {**saved,'revision':int(revision),'content_sha256':digest}

    def get(self,c,p,wid,revision=None):
        sql='SELECT revision,payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?';args=[namespace(p.organization_id),wid]
        if revision is not None:sql+=' AND revision=?';args.append(revision)
        row=self.q(c,sql+' ORDER BY revision DESC LIMIT 1',args).fetchone()
        return self.decode(row,p.organization_id,wid) if row else None

    def opportunity(self,c,p,reference):
        item=self.opportunities.get(c,p,reference['workflow_id'],reference['revision'])
        if item is None or item['content_sha256']!=reference['content_sha256']:raise Error('Referenced opportunity snapshot is unavailable')
        return item

    def source(self,c,p,wid,baseline,review=None):
        result={'opportunity_reference':None,'opportunity_state':'unavailable','current_approved_assessment':False,
            'currency':None,'implementation_budget':None,'blockers':[]}
        try:
            origin=self.opportunity(c,p,baseline['opportunity']);view=self.opportunities.view(c,p,wid);latest=view['artifact']
        except Error:result['blockers']=['opportunity_unavailable_or_corrupt'];return result
        if not view['integrity_ok'] or latest is None:result['blockers']=['opportunity_unavailable_or_corrupt'];return result
        result.update(opportunity_reference=pin(latest),opportunity_state=view['effective_state'],current_approved_assessment=view['current_approved_assessment'])
        reasons=[];assessment=latest['data']['assessment']
        if not view['current_approved_assessment']:reasons.append('current_independent_opportunity_approval_required')
        if (origin['assessment_sha256'] is None or origin['assessment_sha256']!=latest['assessment_sha256']
                or origin['assessed_in_revision']!=latest['assessed_in_revision']):reasons.append('opportunity_assessment_changed_or_missing')
        if assessment is None:reasons.append('opportunity_budget_missing')
        else:
            result.update(currency=assessment['budget_currency'],implementation_budget=assessment['implementation_budget'])
            if baseline['currency']!=assessment['budget_currency']:reasons.append('budget_currency_mismatch')
            elif sum((Decimal(phase['cost']) for phase in baseline['phases']),Decimal(0))>Decimal(assessment['implementation_budget']):reasons.append('baseline_cost_exceeds_budget_context')
        if review is not None:
            try:approved=self.opportunity(c,p,review['approved_opportunity'])
            except Error:reasons.append('reviewed_opportunity_unavailable')
            else:
                approved_revision=approved['revision']-(1 if approved['state']=='implemented' else 0)
                latest_approval_revision=latest['revision']-(1 if latest['state']=='implemented' else 0)
                if (approved['approval'] is None or approved['approval']!=latest['approval']
                        or approved_revision!=latest_approval_revision):reasons.append('opportunity_approval_changed')
        result['blockers']=reasons
        return result

    def view(self,c,p,wid,revision=None):
        meta=self.store._meta(c,p,wid);permissions=self.opportunities.permissions(c,p,wid,meta);latest=self.latest(c,p,wid);integrity=True
        try:item=self.get(c,p,wid,revision)
        except Error:item=None;integrity=False
        if integrity and revision is not None and item is None:raise Error('Implementation-plan revision not found',404)
        historical=item is not None and item['revision']!=latest
        result={'format':'openworkgraph.implementation-phase-plan-view.v1','organization_id':p.organization_id,'root_workflow_id':wid,
            'artifact':item,'integrity_ok':integrity,'latest_revision':latest,'access_revision':meta['access_revision'],
            'viewing_historical_revision':historical,'permissions':permissions,'your_actor_id':p.actor_id,'source':None,'totals':None,
            'effective_state':'integrity_failed' if not integrity else 'not_saved','current_reviewed_baseline':False,'can_freeze':False,
            'execution_authorized':False,'spending_authorized':False,'actual_values_independently_verified':False,'realized_savings_verified':False,
            'normal_exports_include_plan':False,'benefit_rollup_included':False}
        if not item:return result
        source=self.source(c,p,wid,item['baseline'],item['review']);summary=totals(item['baseline'],item['reports'])
        result.update(source=source,totals=summary,effective_state='draft' if item['review'] is None else 'frozen')
        if item['review'] is not None and source['blockers']:result['effective_state']='needs_reassessment'
        result['current_reviewed_baseline']=not historical and item['review'] is not None and not source['blockers']
        if historical:result['effective_state']='historical_snapshot'
        result['can_freeze']=not historical and item['review'] is None and not source['blockers'] and permissions['review'] and item['baseline_author_actor_id']!=p.actor_id
        budget=source['implementation_budget']
        for key in ('baseline_cost','cost_to_date','new_total_cost_estimate'):
            summary[key+'_exceeds_budget_context']=(Decimal(summary[key])>Decimal(budget)) if budget is not None and summary['currency']==source['currency'] and summary[key] is not None else None
        return result

    def detail(self,p,wid,revision=None,*,exporting=False):
        with self.catalog.reading(p) as c:
            result=self.view(c,p,wid,revision)
            if exporting and (not result['integrity_ok'] or not result['artifact']):raise Error('A valid saved implementation plan is required for export')
        result['checked_at']=_now();self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,
            action='process.implementation_plan.exported' if exporting else 'process.implementation_plan.read',
            details={'workflow_id':wid,'revision':result['artifact']['revision'] if result['artifact'] else 0})
        return result

    def append(self,c,p,wid,saved,revision,action):
        if revision>=MAX_REVISIONS:raise Error('Implementation plans retain at most fifty revisions; nothing was pruned')
        raw=saved.model_dump(mode='json');digest=content_digest(raw)
        self.q(c,'INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)',(namespace(p.organization_id),wid,revision+1,json.dumps({**raw,'content_sha256':digest})))
        self.store._audit(c,p,action,workflow_id=wid,revision=revision+1,content_sha256=digest,baseline_sha256=saved.baseline_sha256)
        return {'revision':revision+1,'content_sha256':digest}

    def current(self,c,p,wid,body,*,reviewing=False):
        meta=self.store._meta(c,p,wid,lock=True);permissions=self.opportunities.permissions(c,p,wid,meta)
        if not permissions['review' if reviewing else 'edit']:raise Error('Current implementation-plan permission required',403)
        latest=self.latest(c,p,wid)
        if body.expected_revision!=latest or body.expected_access_revision!=meta['access_revision']:raise Error('Implementation plan or root access changed; reload')
        if latest>=MAX_REVISIONS:raise Error('Implementation plans retain at most fifty revisions; nothing was pruned')
        return latest,self.get(c,p,wid) if latest else None

    def save_baseline(self,p,wid,body):
        self.store.require_scope(p,'org:process:read');self.store.require_scope(p,'org:process:write')
        if not body.user_confirmed or not body.organization_sharing_confirmed:raise Error('Explicitly review and confirm sharing the phase baseline',422)
        baseline=body.baseline.model_dump(mode='json')
        if baseline['opportunity']['workflow_id']!=wid:raise Error('Baseline opportunity differs from the selected root',422)
        with self.store.write_transaction() as c:
            latest,old=self.current(c,p,wid,body)
            source=self.opportunity(c,p,baseline['opportunity']);current=self.opportunities.view(c,p,wid)
            if (not current['integrity_ok'] or current['artifact'] is None or current['artifact']['revision']!=source['revision']
                or current['source'] is None or not current['source']['current'] or source['data']['assessment'] is None):raise Error('Select the latest active opportunity assessment before changing a baseline')
            if baseline['currency']!=source['data']['assessment']['budget_currency']:raise Error('Baseline currency must match its opportunity budget',422)
            reports=old['reports'] if old else [];attributions=old['report_attributions'] if old else []
            previous={p['id']:p['name'] for p in old['baseline']['phases']} if old else {};following={p['id']:p['name'] for p in baseline['phases']}
            if any(r['phase_id'] not in following or following[r['phase_id']]!=previous[r['phase_id']] for r in reports):raise Error('Retain the identity and name of every reported phase; cancel it explicitly instead of removing or repurposing it')
            saved=Saved(organization_id=p.organization_id,baseline=body.baseline,baseline_author_actor_id=p.actor_id,
                assessed_in_revision=latest+1,baseline_sha256=content_digest(baseline),reports=reports,report_attributions=attributions,
                author_actor_id=p.actor_id,saved_at=_now())
            return self.append(c,p,wid,saved,latest,'process.implementation_plan.baseline.saved')

    def save_reports(self,p,wid,body):
        self.store.require_scope(p,'org:process:read');self.store.require_scope(p,'org:process:write')
        if not body.user_confirmed or not body.organization_sharing_confirmed:raise Error('Explicitly confirm sharing human-reported actuals and estimates',422)
        with self.store.write_transaction() as c:
            latest,old=self.current(c,p,wid,body)
            if old is None:raise Error('Save a phase baseline before reporting',404)
            if body.expected_baseline_sha256!=old['baseline_sha256']:raise Error('The baseline changed; reload before reporting')
            ids={p['id'] for p in old['baseline']['phases']}
            if any(r.phase_id not in ids for r in body.reports):raise Error('Reports must identify existing baseline phases',422)
            reports={r['phase_id']:r for r in old['reports']};authors={r['phase_id']:r for r in old['report_attributions']};now=_now()
            for report in body.reports:
                reports[report.phase_id]=report.model_dump(mode='json');authors[report.phase_id]=ReportAttribution(phase_id=report.phase_id,actor_id=p.actor_id,at=now,reported_in_revision=latest+1).model_dump(mode='json')
            raw={key:value for key,value in old.items() if key not in {'revision','content_sha256'}}
            raw.update(reports=[reports[key] for key in sorted(reports)],report_attributions=[authors[key] for key in sorted(authors)],author_actor_id=p.actor_id,saved_at=now)
            return self.append(c,p,wid,Saved.model_validate(raw),latest,'process.implementation_plan.reports.saved')

    def freeze(self,p,wid,body):
        self.store.require_scope(p,'org:process:read');self.store.require_scope(p,'org:process:review')
        if not body.user_confirmed:raise Error('Explicitly confirm review of this exact phase baseline',422)
        with self.store.write_transaction() as c:
            latest,old=self.current(c,p,wid,body,reviewing=True)
            if old is None:raise Error('Save a baseline before reviewing it',404)
            if body.expected_baseline_sha256!=old['baseline_sha256'] or old['review'] is not None:raise Error('Review the exact pending baseline')
            if old['baseline_author_actor_id']==p.actor_id:raise Error('A different current reviewer must freeze the baseline',403)
            source=self.source(c,p,wid,old['baseline'])
            if source['blockers']:raise Error('Review the current approved opportunity, source assessment, currency and budget before freezing')
            if body.expected_approved_opportunity.model_dump(mode='json')!=source['opportunity_reference']:raise Error('The approved opportunity changed; reload its exact current review context')
            now=_now();review=Review(actor_id=p.actor_id,at=now,baseline_sha256=old['baseline_sha256'],assessed_in_revision=old['assessed_in_revision'],approved_opportunity=source['opportunity_reference'])
            raw={key:value for key,value in old.items() if key not in {'revision','content_sha256'}};raw.update(review=review.model_dump(mode='json'),author_actor_id=p.actor_id,saved_at=now)
            return self.append(c,p,wid,Saved.model_validate(raw),latest,'process.implementation_plan.baseline.frozen')

    def delete(self,p,wid,body):
        self.store.require_scope(p,'org:process:read');self.store.require_scope(p,'org:process:write')
        with self.store.write_transaction() as c:
            meta=self.store._meta(c,p,wid,lock=True)
            if not self.opportunities.permissions(c,p,wid,meta)['delete']:raise Error('Only the current root owner or administrator can erase phase-plan history',403)
            if not body.user_confirmed:raise Error('Confirm erasure of all phase-plan history',422)
            if body.expected_access_revision!=meta['access_revision'] or body.expected_revision!=self.latest(c,p,wid):raise Error('Phase plan or root access changed; reload')
            erase(self.store,c,p.organization_id,wid);self.store._audit(c,p,'process.implementation_plan.deleted',workflow_id=wid)
            return {'deleted':True}
