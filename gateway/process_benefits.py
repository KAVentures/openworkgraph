"""Versioned contribution plans reuse exact shared process/scenario revisions."""
import json
from shared.process_benefits import Saved, content_digest, modeled_totals, MAX_PLANS, MAX_REVISIONS
from .process_collaboration import ProcessCollaborationError as Error, _now
from .process_hierarchies import ProcessHierarchies


def namespace(org):return json.dumps(['organization-process-benefit-plans',org,'v1'],separators=(',',':'))


def erase(store,conn,org,workflow_id):
    store.db._execute(conn,'DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(org),workflow_id))


class ProcessBenefits:
    def __init__(self,store):self.store=store;self.db=store.db;self.catalog=ProcessHierarchies(store)
    def q(self,c,sql,args=()):return self.db._execute(c,sql,args)

    def latest(self,c,p,wid):
        return int(self.q(c,'SELECT MAX(revision) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace(p.organization_id),wid)).fetchone()[0] or 0)

    def decode(self,row,org,wid):
        rev,payload=row
        try:
            raw=json.loads(payload);digest=raw.pop('content_sha256');saved=Saved.model_validate(raw).model_dump(mode='json')
            if content_digest(saved)!=digest or saved['plan']['organization_id']!=org or saved['plan']['root_process']['workflow_id']!=wid:raise ValueError('integrity')
        except (ValueError,KeyError,TypeError):raise Error('Stored benefit plan failed its integrity check') from None
        return {**saved,'revision':int(rev),'content_sha256':digest}

    def get(self,c,p,wid,revision=None):
        sql='SELECT revision,payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?';args=[namespace(p.organization_id),wid]
        if revision is not None:sql+=' AND revision=?';args.append(revision)
        row=self.q(c,sql+' ORDER BY revision DESC LIMIT 1',args).fetchone()
        return self.decode(row,p.organization_id,wid) if row else None

    def resolve(self,c,p,ref,memo):
        key=(ref['workflow_id'],ref['revision'],ref['content_sha256'])
        if key not in memo:
            info=self.catalog.resolve(c,p,ref);scenario=None
            if info['available']:
                record=self.store._record(c,p,ref['workflow_id'],ref['revision'])
                scenario=record['process_model'].get('opportunity_scenario')
            memo[key]={**info,'scenario_available':scenario is not None,'scenario':scenario}
        return memo[key]

    @staticmethod
    def reason(info,*,scenario=False):
        if not info['available']:return info['state']
        if not info['current']:return info['state']
        if info['independent_model_review']!='reviewed_design':return 'independent_design_review_required'
        if scenario and not info['scenario_available']:return 'scenario_missing'
        return None

    def view(self,c,p,wid,revision=None,memo=None):
        memo={} if memo is None else memo;meta=self.store._meta(c,p,wid);latest=self.latest(c,p,wid);role=self.store._role(c,p,wid,meta)
        integrity=True
        try:item=self.get(c,p,wid,revision)
        except Error:integrity=False;item=None
        if integrity and revision is not None and item is None:raise Error('Benefit plan revision not found',404)
        base={'format':'openworkgraph.process-benefit-view.v1','root_workflow_id':wid,'artifact':item,'integrity_ok':integrity,
              'latest_revision':latest,'access_revision':meta['access_revision'],
              'can_write':integrity and not meta['archived'] and role in {'owner','editor','admin'} and 'org:process:write' in p.scopes,
              'can_delete':role in {'owner','admin'} and 'org:process:write' in p.scopes,
              'root_resolution':None,'resolutions':[],'included_count':None if not integrity else 0,
              'status':'integrity_failed' if not integrity else 'not_saved','blockers':[],'totals':None,
              'realized_savings_verified':False,'scope_nonoverlap_independently_verified':False,'execution_authorized':False,
              'normal_implementation_exports_include_plan':False}
        if not item:return base
        plan=item['plan'];root=self.resolve(c,p,plan['root_process'],memo);base['root_resolution']=root
        included=[];blockers=[];scopes=set()
        for link in plan['links']:
            info=self.resolve(c,p,link['scenario_process'],memo)
            base['resolutions'].append({'link_id':link['id'],**info,'include_in_estimate':link['include_in_estimate'],'benefit_scope_id':link['benefit_scope_id']})
            if not link['include_in_estimate']:continue
            included.append(info)
            reason=self.reason(info,scenario=True)
            if reason:blockers.append({'code':reason,'link_id':link['id']})
            elif info['scenario']['currency']!=plan['currency']:blockers.append({'code':'currency_mismatch','link_id':link['id']})
            if link['benefit_scope_id'] in scopes:blockers.append({'code':'overlapping_declared_scope','link_id':link['id']})
            scopes.add(link['benefit_scope_id'])
        base['included_count']=len(included)
        if not included:base['status']='no_contributions';return base
        reason=self.reason(root)
        if reason:blockers.append({'code':'root_'+reason})
        if len(included)>1 and not plan['disjoint_scopes_reviewed']:blockers.append({'code':'disjoint_scope_review_required'})
        base['blockers']=blockers;base['status']='withheld' if blockers else 'modeled_estimate'
        if not blockers:base['totals']=modeled_totals([i['scenario'] for i in included])
        return base

    def detail(self,p,wid,revision=None,*,exporting=False):
        with self.catalog.reading(p) as c:
            result=self.view(c,p,wid,revision)
            if exporting and (not result['integrity_ok'] or not result['artifact']):raise Error('A valid saved benefit plan is required for export')
        result['checked_at']=_now()
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,
            action='process.benefits.exported' if exporting else 'process.benefits.read',
            details={'workflow_id':wid,'revision':result['artifact']['revision'] if result['artifact'] else 0})
        return result

    def save(self,p,wid,body):
        s=self.store;s.require_scope(p,'org:process:read');s.require_scope(p,'org:process:write')
        if not body.user_confirmed or not body.organization_sharing_confirmed:raise Error('Review and explicitly confirm sharing this benefit plan',422)
        plan=body.plan.model_dump(mode='json')
        if plan['organization_id']!=p.organization_id or plan['root_process']['workflow_id']!=wid:raise Error('Benefit plan root or organization differs from the selected process',422)
        refs=[plan['root_process']]+[r['scenario_process'] for r in plan['links']]
        with s.write_transaction() as c:
            metas={key:s._meta(c,p,key,lock=True) for key in sorted({r['workflow_id'] for r in refs})}
            meta=metas[wid]
            if meta['archived'] or s._role(c,p,wid,meta) not in {'owner','editor','admin'}:raise Error('Current root process editor permission required',403)
            if meta['access_revision']!=body.expected_access_revision:raise Error('Root access changed; reload')
            if self.latest(c,p,wid)!=body.expected_revision:raise Error('Benefit plan changed; reload')
            if body.expected_revision>=MAX_REVISIONS:raise Error('Benefit plans retain at most twenty revisions; nothing was pruned')
            memo={}
            for ref in refs:
                info=self.resolve(c,p,ref,memo)
                if not info['available'] or info['archived']:raise Error('Every reference must resolve to an available active process revision')
            if not self.resolve(c,p,plan['root_process'],memo)['current']:raise Error('Root process changed; select its latest revision')
            for link in plan['links']:
                if not self.resolve(c,p,link['scenario_process'],memo)['scenario_available']:raise Error('Every linked process revision must declare a scenario')
            saved=Saved(plan=body.plan,author_actor_id=p.actor_id,saved_at=_now()).model_dump(mode='json');digest=content_digest(saved)
            revision=body.expected_revision+1
            self.q(c,'INSERT INTO workflow_knowledge(owner,workflow_id,revision,payload) VALUES (?,?,?,?)',
                (namespace(p.organization_id),wid,revision,json.dumps({**saved,'content_sha256':digest})))
            s._audit(c,p,'process.benefits.saved',workflow_id=wid,revision=revision,content_sha256=digest,link_count=len(plan['links']))
            return {'revision':revision,'content_sha256':digest}

    def delete(self,p,wid,body):
        s=self.store;s.require_scope(p,'org:process:read');s.require_scope(p,'org:process:write')
        with s.write_transaction() as c:
            meta=s._meta(c,p,wid,lock=True)
            if s._role(c,p,wid,meta) not in {'owner','admin'}:raise Error('Only a current root owner or administrator can erase benefit history',403)
            if not body.user_confirmed:raise Error('Confirm deletion of all benefit plan revisions',422)
            if meta['access_revision']!=body.expected_access_revision or self.latest(c,p,wid)!=body.expected_revision:raise Error('Benefit plan or root access changed; reload')
            erase(s,c,p.organization_id,wid);s._audit(c,p,'process.benefits.deleted',workflow_id=wid)
            return {'deleted':True}

    def portfolio_snapshot(self,c,p,currency):
        rows=self.q(c,'''SELECT k.workflow_id,k.revision,k.payload FROM workflow_knowledge k
            WHERE k.owner=? AND k.revision=(SELECT MAX(v.revision) FROM workflow_knowledge v
                WHERE v.owner=k.owner AND v.workflow_id=k.workflow_id) ORDER BY k.workflow_id LIMIT ?''',
            (namespace(p.organization_id),MAX_PLANS+1)).fetchall()
        blockers=[];plans=[];unique={};scopes={};memo={};other=0
        if len(rows)>MAX_PLANS:blockers.append({'code':'portfolio_scan_truncated'})
        for wid,rev,payload in rows[:MAX_PLANS]:
            try:view=self.view(c,p,wid,memo=memo)
            except Error:view={'integrity_ok':False,'artifact':None,'root_workflow_id':wid,'latest_revision':int(rev),'status':'unavailable','included_count':None,'totals':None,'blockers':[]}
            if not view['integrity_ok'] or not view['artifact']:
                blockers.append({'code':'unavailable_plan','root_workflow_id':wid});plans.append(view);continue
            plan=view['artifact']['plan']
            if plan['currency']!=currency:other+=1;continue
            # A portfolio never exposes successful partial subtotals. Individual
            # plan estimates remain available through their own explicit detail.
            view={**view,'totals':None}
            plans.append(view)
            if not view['included_count']:continue
            for reason in view['blockers']:blockers.append({'root_workflow_id':wid,**reason})
            for link in view['resolutions']:
                if not link['include_in_estimate']:continue
                key=link['workflow_id'];pin=(link['revision'],link['content_sha256'],link['benefit_scope_id'])
                if key in unique:
                    old=unique[key]
                    if (old['revision'],old['content_sha256'],old['benefit_scope_id'])!=pin:blockers.append({'code':'inconsistent_reused_scenario','workflow_id':key})
                    old['linked_root_ids'].append(wid);continue
                scope=link['benefit_scope_id']
                if scope in scopes and scopes[scope]!=key:blockers.append({'code':'overlapping_declared_scope','workflow_id':key})
                scopes[scope]=key
                unique[key]={**link,'linked_root_ids':[wid]}
        result={'format':'openworkgraph.process-benefit-portfolio.v1','organization_id':p.organization_id,'currency':currency,
            'plans':plans,'other_currency_plan_count':other,'scan_limit':MAX_PLANS,'scan_truncated':len(rows)>MAX_PLANS,
            'contributors':list(unique.values()),'unique_scenario_count':len(unique),'blockers':blockers,
            'status':'withheld' if blockers else 'ready_for_review' if unique else 'no_contributions','totals':None,
            'case_scope_nonoverlap_verified':False,'realized_savings_verified':False,'execution_authorized':False,
            'scenarios_counted_once_by_workflow_id':True,'live_source_freshness_verified':False}
        result['portfolio_sha256']=content_digest(result)
        result['generated_at']=_now()
        return result

    def portfolio(self,p,currency,review=None,*,exporting=False):
        with self.catalog.reading(p) as c:
            result=self.portfolio_snapshot(c,p,currency)
            if review is not None:
                if not review.user_confirmed:raise Error('Explicit portfolio review or export confirmation required',422)
                if review.expected_portfolio_sha256!=result['portfolio_sha256']:raise Error('Portfolio or referenced designs changed; reload and review')
                if review.disjoint_scopes_confirmed:
                    if result['blockers'] or not result['contributors']:raise Error('The complete portfolio estimate is withheld; resolve every blocker and reload')
                    result['totals']=modeled_totals([i['scenario'] for i in result['contributors']]);result['status']='modeled_estimate'
                    result['human_disjoint_scope_confirmation']=True
                elif not exporting:raise Error('Explicitly confirm disjoint benefit scopes before estimating',422)
        self.db.audit(organization_id=p.organization_id,principal_id=p.audit_id,
            action='process.benefits.portfolio.exported' if exporting else 'process.benefits.portfolio.estimated' if review is not None else 'process.benefits.portfolio.read',
            details={'currency':currency,'portfolio_sha256':result['portfolio_sha256'],'unique_scenarios':result['unique_scenario_count']})
        return result
