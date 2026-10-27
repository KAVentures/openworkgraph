"""Shared assessment/decision history reuses root roles and exact model lineage."""
import json
from decimal import Decimal
from shared.process_opportunity_review import Saved, Approval, Decision, assessment_digest, content_digest, score, TRANSITIONS, MAX_REVISIONS
from .process_collaboration import ProcessCollaborationError as Error, _now
from .process_hierarchies import ProcessHierarchies


def namespace(org):return json.dumps(['organization-process-opportunity-pipeline',org,'v1'],separators=(',',':'))


def erase(store,c,org,wid):store.db._execute(c,'DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(org),wid))


class SharedOpportunityStore:
    def __init__(self,store):self.store=store;self.db=store.db;self.catalog=ProcessHierarchies(store)
    def q(self,c,sql,args=()):return self.db._execute(c,sql,args)
    def latest(self,c,p,wid):return int(self.q(c,'SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(p.organization_id),wid)).fetchone()[0] or 0)

    def decode(self,row,org,wid):
        revision,payload=row
        try:
            raw=json.loads(payload);digest=raw.pop('content_sha256');saved=Saved.model_validate(raw).model_dump(mode='json')
            if (content_digest(saved)!=digest or saved['organization_id']!=org or saved['data']['process']['workflow_id']!=wid
                    or (saved['assessed_in_revision'] is not None and saved['assessed_in_revision']>int(revision))):raise ValueError('integrity')
        except (ValueError,TypeError,KeyError):raise Error('Stored opportunity failed its integrity check') from None
        return {**saved,'revision':int(revision),'content_sha256':digest}

    def get(self,c,p,wid,revision=None):
        sql='SELECT revision,payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?';args=[namespace(p.organization_id),wid]
        if revision is not None:sql+=' AND revision=?';args.append(revision)
        row=self.q(c,sql+' ORDER BY revision DESC LIMIT 1',args).fetchone()
        return self.decode(row,p.organization_id,wid) if row else None

    def permissions(self,c,p,wid,meta):
        role=self.store._role(c,p,wid,meta)
        return {'edit':not meta['archived'] and role in {'owner','editor','admin'} and 'org:process:write' in p.scopes,
                'review':not meta['archived'] and role in {'owner','reviewer','admin'} and 'org:process:review' in p.scopes,
                'delete':role in {'owner','admin'} and 'org:process:write' in p.scopes}

    def preflight(self,p,wid,*,reviewing=False,deleting=False):
        self.store.require_scope(p,'org:process:read')
        with self.db.connect() as c:
            meta=self.store._meta(c,p,wid);permissions=self.permissions(c,p,wid,meta)
            if not (permissions['delete'] if deleting else permissions['edit'] or (reviewing and permissions['review'])):raise Error('Current root opportunity permission required',403)

    def source(self,c,p,data):
        info=self.catalog.resolve(c,p,data.process.model_dump());scenario=None
        if info['available']:scenario=self.store._record(c,p,data.process.workflow_id,data.process.revision)['process_model'].get('opportunity_scenario')
        return info,scenario

    def eligibility(self,data,info,scenario):
        reasons=[]
        if not info['available'] or not info['current']:reasons.append(info['state'])
        elif info['independent_model_review']!='reviewed_design':reasons.append('independent_design_review_required')
        if data.assessment is None:reasons.append('assessment_required')
        if scenario is None:reasons.append('scenario_missing')
        if data.assessment is not None and scenario is not None:
            if data.assessment.budget_currency!=scenario['currency']:reasons.append('budget_currency_mismatch')
            elif Decimal(data.assessment.implementation_budget)<Decimal(str(scenario['one_time_implementation_cost'])):reasons.append('implementation_assumption_exceeds_budget')
        return reasons

    def view(self,c,p,wid,revision=None):
        meta=self.store._meta(c,p,wid);permissions=self.permissions(c,p,wid,meta);latest=self.latest(c,p,wid);integrity=True
        try:item=self.get(c,p,wid,revision)
        except Error:item=None;integrity=False
        if integrity and revision is not None and item is None:raise Error('Opportunity revision not found',404)
        result={'format':'openworkgraph.shared-opportunity-view.v1','organization_id':p.organization_id,'root_workflow_id':wid,'artifact':item,'integrity_ok':integrity,
            'latest_revision':latest,'viewing_historical_revision':item is not None and item['revision']!=latest,
            'access_revision':meta['access_revision'],'owner_actor_id':meta['owner_actor_id'],
            'permissions':permissions,'your_actor_id':p.actor_id,'source':None,'score':None,'approval_blockers':[],
            'effective_state':'integrity_failed' if not integrity else 'not_saved','current_approved_assessment':False,'allowed_transitions':[],
            'implementation_state_is_human_self_report':True,'business_outcomes_verified':False,'execution_authorized':False,'spending_authorized':False}
        if not item:return result
        saved=Saved.model_validate({k:v for k,v in item.items() if k not in {'revision','content_sha256'}})
        info,scenario=self.source(c,p,saved.data);reasons=self.eligibility(saved.data,info,scenario)
        result.update(source=info,score=score(saved.data,scenario),approval_blockers=reasons,effective_state=saved.state,
            current_approved_assessment=item['revision']==latest and saved.state in {'approved','implemented'} and saved.approval is not None and not reasons)
        if saved.state in {'approved','implemented'} and reasons:result['effective_state']='needs_reassessment'
        if item['revision']!=latest:
            result['effective_state']='historical_snapshot'
            result['approval_blockers']=[*reasons,'historical_opportunity_revision']
        if item['revision']==latest and not meta['archived']:
            for target in sorted(TRANSITIONS[saved.state]):
                if target=='approved':
                    if not reasons and permissions['review'] and saved.assessment_author_actor_id!=p.actor_id:result['allowed_transitions'].append(target)
                elif target in {'rejected','on_hold'}:
                    if permissions['edit'] or permissions['review']:result['allowed_transitions'].append(target)
                elif permissions['edit'] and (target!='implemented' or result['current_approved_assessment']):result['allowed_transitions'].append(target)
        return result

    def detail(self,p,wid,revision=None,*,exporting=False):
        with self.catalog.reading(p) as c:
            result=self.view(c,p,wid,revision)
            if exporting and (not result['integrity_ok'] or not result['artifact']):raise Error('A valid saved opportunity is required for export')
        result['checked_at']=_now()
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,action='process.opportunity.exported' if exporting else 'process.opportunity.read',
            details={'workflow_id':wid,'revision':result['artifact']['revision'] if result['artifact'] else 0})
        return result

    def append(self,c,p,wid,saved,latest):
        if latest>=MAX_REVISIONS:raise Error('Opportunity history retains at most fifty revisions; nothing was pruned')
        raw=saved.model_dump(mode='json');digest=content_digest(raw)
        self.q(c,'INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)',(namespace(p.organization_id),wid,latest+1,json.dumps({**raw,'content_sha256':digest})))
        self.store._audit(c,p,'process.opportunity.saved',workflow_id=wid,revision=latest+1,content_sha256=digest,state=saved.state,assessment_sha256=saved.assessment_sha256)
        return {'revision':latest+1,'content_sha256':digest,'state':saved.state}

    def save(self,p,wid,body):
        s=self.store;s.require_scope(p,'org:process:read');s.require_scope(p,'org:process:write')
        if not body.user_confirmed or not body.organization_sharing_confirmed:raise Error('Review and explicitly confirm sharing the exact opportunity',422)
        if body.data.process.workflow_id!=wid:raise Error('Opportunity process differs from the selected root',422)
        with s.write_transaction() as c:
            meta=s._meta(c,p,wid,lock=True)
            if not self.permissions(c,p,wid,meta)['edit']:raise Error('Current root opportunity editor permission required',403)
            latest=self.latest(c,p,wid)
            if latest!=body.expected_revision or meta['access_revision']!=body.expected_access_revision:raise Error('Opportunity or root access changed; reload')
            info,_=self.source(c,p,body.data)
            if not info['available'] or not info['current']:raise Error('Select the latest available active root revision')
            digest=assessment_digest(body.data);now=_now()
            if latest>=MAX_REVISIONS:raise Error('Opportunity history retains at most fifty revisions; nothing was pruned')
            saved=Saved(organization_id=p.organization_id,data=body.data,state='assessing' if digest else 'idea',assessment_author_actor_id=p.actor_id if digest else None,
                assessed_in_revision=latest+1 if digest else None,assessment_sha256=digest,author_actor_id=p.actor_id,saved_at=now)
            return self.append(c,p,wid,saved,latest)

    def transition(self,p,wid,body):
        s=self.store;s.require_scope(p,'org:process:read')
        if not body.user_confirmed:raise Error('Explicitly review this exact opportunity decision',422)
        with s.write_transaction() as c:
            meta=s._meta(c,p,wid,lock=True);permissions=self.permissions(c,p,wid,meta);latest=self.latest(c,p,wid)
            if body.expected_revision!=latest or body.expected_access_revision!=meta['access_revision']:raise Error('Opportunity or root access changed; reload')
            current=self.get(c,p,wid)
            if current is None:raise Error('Save an opportunity before making a stage decision',404)
            saved=Saved.model_validate({k:v for k,v in current.items() if k not in {'revision','content_sha256'}})
            target=body.target
            if target not in TRANSITIONS[saved.state]:raise Error('This stage transition is not available')
            if target in {'approved','rejected','on_hold'}:
                permitted=permissions['review'] or (target in {'rejected','on_hold'} and permissions['edit'])
            else:permitted=permissions['edit']
            if not permitted:raise Error('Current root opportunity decision permission required',403)
            if body.expected_assessment_sha256!=saved.assessment_sha256:raise Error('Review the exact current assessment digest')
            approval=None;now=_now()
            if target in {'approved','implemented'}:
                info,scenario=self.source(c,p,saved.data)
                if self.eligibility(saved.data,info,scenario):raise Error('Reassess the current reviewed process, scenario and budget before this decision')
                if target=='approved':
                    if saved.assessment_author_actor_id==p.actor_id:raise Error('An independent reviewer must approve this assessment',403)
                    if saved.data.assessment.compliance_review_required and not body.compliance_review_declared_complete:raise Error('Explicitly acknowledge the declared compliance-review requirement',422)
                    approval=Approval(actor_id=p.actor_id,assessment_sha256=saved.assessment_sha256,assessed_in_revision=saved.assessed_in_revision,
                        at=now,compliance_review_declared_complete=body.compliance_review_declared_complete)
                else:
                    if not saved.approval or not body.implementation_self_report_confirmed:raise Error('Explicitly confirm human-reported implementation of this approved assessment',422)
                    approval=saved.approval
            next_saved=Saved(organization_id=p.organization_id,data=saved.data,state=target,assessment_author_actor_id=saved.assessment_author_actor_id,assessed_in_revision=saved.assessed_in_revision,
                assessment_sha256=saved.assessment_sha256,approval=approval,last_decision=Decision(actor_id=p.actor_id,target=target,at=now,note=body.note,implementation_self_report=target=='implemented'),author_actor_id=p.actor_id,saved_at=now)
            return self.append(c,p,wid,next_saved,latest)

    def delete(self,p,wid,body):
        s=self.store;s.require_scope(p,'org:process:read');s.require_scope(p,'org:process:write')
        with s.write_transaction() as c:
            meta=s._meta(c,p,wid,lock=True)
            if not self.permissions(c,p,wid,meta)['delete']:raise Error('Only a current root owner or administrator may erase opportunity history',403)
            if not body.user_confirmed:raise Error('Confirm deletion of all opportunity history',422)
            if meta['access_revision']!=body.expected_access_revision or self.latest(c,p,wid)!=body.expected_revision:raise Error('Opportunity or root access changed; reload')
            erase(s,c,p.organization_id,wid);s._audit(c,p,'process.opportunity.deleted',workflow_id=wid)
            return {'deleted':True}

    def inventory(self,p,after='',limit=50):
        with self.catalog.reading(p) as c:
            rows=self.q(c,'''SELECT k.workflow_id,k.revision FROM workflow_knowledge k WHERE k.owner=? AND k.workflow_id>?
                AND k.revision=(SELECT MAX(v.revision) FROM workflow_knowledge v WHERE v.owner=k.owner AND v.workflow_id=k.workflow_id)
                ORDER BY k.workflow_id LIMIT ?''',(namespace(p.organization_id),after,limit+1)).fetchall()
            items=[]
            for wid,revision in rows[:limit]:
                try:
                    view=self.view(c,p,wid);a=view['artifact']
                    items.append({'workflow_id':wid,'revision':int(revision),'integrity_ok':view['integrity_ok'],
                        'title':a['data']['title'] if a else 'Unavailable opportunity','team_label':a['data']['team_label'] if a else '',
                        'owner_actor_id':view['owner_actor_id'],'recorded_state':a['state'] if a else None,'effective_state':view['effective_state'],
                        'current_approved_assessment':view['current_approved_assessment'],'source_state':view['source']['state'] if view['source'] else 'unavailable',
                        'process_revision':a['data']['process']['revision'] if a else None,'score':view['score']})
                except Error:items.append({'workflow_id':wid,'revision':int(revision),'integrity_ok':False,'title':'Unavailable opportunity','effective_state':'unavailable'})
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,action='process.opportunity.inventory.read',details={'returned':len(items)})
        return {'organization_id':p.organization_id,'opportunities':items,'has_more':len(rows)>limit,'next_after':rows[limit-1][0] if len(rows)>limit else None,
                'filter_scope':'loaded_page_only','company_savings_total':None,'execution_authorized':False,'business_outcomes_verified':False}
