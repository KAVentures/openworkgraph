"""Shared assessment decisions, approval drift, live roles and exact lineage."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from shared.process_opportunity_review import Opportunity,Assessment,Save,Transition,Remove,assessment_digest,score
from gateway.process_opportunity_review import SharedOpportunityStore,namespace
from gateway.process_collaboration import SharedProcessWrite,ProcessArchiveWrite,ProcessDeleteWrite,ProcessCollaborationError
from test_process_collaboration import store,person,model,grant,review,app


def scenario(**changes):return {'currency':'USD','monthly_cases':120.,'minutes_saved_per_case':10.,'eligible_fraction_percent':100.,'loaded_hourly_cost':60.,'recurring_monthly_cost':100.,'one_time_implementation_cost':1800.,'sensitivity_percent':25.,**changes}


def root(store,*,reviewed=True,estimate=True):
    value=model().model_dump()
    if estimate:value['opportunity_scenario']=scenario()
    r=store.save(person(),SharedProcessWrite(title='Root opportunity process',process_model=value,user_confirmed=True,organization_sharing_confirmed=True))['process']
    grant(store,r['workflow_id'],members=[{'actor_id':'bob','role':'reviewer'},{'actor_id':'ed','role':'editor'}])
    if reviewed:store.review(person('bob'),r['workflow_id'],review(r))
    return r


def pin(r):return {k:r[k] for k in ('workflow_id','revision','content_sha256')}


def data(r,**changes):return Opportunity(process=pin(r),title='Automation opportunity',description='A declared business proposal',coordinator_label='Implementation lead',team_label='Service',assessment={'ease':{k:4 for k in ('rules_based','structured_inputs','process_stability','digital_inputs','low_exception_rate')},'budget_currency':'USD','implementation_budget':'2000.00'},**changes)


def save(opps,store,r,value=None,expected=0,who=None):
    who=who or person();return opps.save(who,r['workflow_id'],Save(data=value or data(r),expected_revision=expected,expected_access_revision=store.detail(person(),r['workflow_id'])['access_revision'],user_confirmed=True,organization_sharing_confirmed=True))


def decide(opps,store,r,target,*,who='bob',**changes):
    view=opps.detail(person(who),r['workflow_id']);a=view['artifact']
    return opps.transition(person(who),r['workflow_id'],Transition(target=target,expected_revision=view['latest_revision'],expected_access_revision=view['access_revision'],expected_assessment_sha256=a['assessment_sha256'],user_confirmed=True,**changes))


def test_shared_lifecycle_independent_approval_and_explicit_self_report(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r)
    view=opps.detail(person(),wid);a=view['artifact'];assert a['state']=='assessing' and a['assessment_author_actor_id']=='alice'
    assert view['score']['ease_score']==75 and view['score']['modeled_hours_per_month']=='20.00'
    assert view['score']['quadrant']=='quick_win' and not view['current_approved_assessment']
    with pytest.raises(ProcessCollaborationError):decide(opps,store,r,'approved',who='alice')
    decide(opps,store,r,'approved');approved=opps.detail(person('reader'),wid)
    assert approved['artifact']['approval']['actor_id']=='bob' and approved['current_approved_assessment']
    assert not approved['execution_authorized'] and not approved['spending_authorized']
    with pytest.raises(ProcessCollaborationError):decide(opps,store,r,'implemented',who='alice')
    decide(opps,store,r,'implemented',who='alice',implementation_self_report_confirmed=True)
    implemented=opps.detail(person(),wid);assert implemented['artifact']['state']=='implemented'
    assert implemented['implementation_state_is_human_self_report'] and not implemented['business_outcomes_verified']
    assert store.detail(person(),wid)['process']==r


def test_reassessment_after_approval_and_implementation_clears_exact_basis(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r);decide(opps,store,r,'approved')
    first=opps.detail(person(),wid)['artifact'];value=data(r);value.team_label='Changed team';save(opps,store,r,value,expected=2,who=person('ed'))
    current=opps.detail(person(),wid);assert current['artifact']['state']=='assessing' and current['artifact']['approval'] is None
    assert current['artifact']['assessment_author_actor_id']=='ed' and current['artifact']['assessment_sha256']!=first['assessment_sha256']
    assert opps.detail(person(),wid,2,exporting=True)['artifact']==first
    decide(opps,store,r,'approved');decide(opps,store,r,'implemented',who='ed',implementation_self_report_confirmed=True)
    save(opps,store,r,value,expected=5)
    assert opps.detail(person(),wid)['artifact']['state']=='assessing'
    assert opps.detail(person(),wid)['artifact']['approval'] is None


def test_source_revision_changes_effective_approval_and_blocks_implementation(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r);decide(opps,store,r,'approved')
    original=opps.detail(person(),wid)['artifact'];value=deepcopy(r['process_model']);value['opportunity_scenario']['one_time_implementation_cost']=3000.
    new=store.save(person(),SharedProcessWrite(title='Updated process',process_model=value,expected_revision=1,user_confirmed=True,organization_sharing_confirmed=True),wid)['process']
    view=opps.detail(person(),wid);assert view['effective_state']=='needs_reassessment' and not view['current_approved_assessment']
    assert view['artifact']==original and 'revision_changed' in view['approval_blockers']
    with pytest.raises(ProcessCollaborationError):decide(opps,store,r,'implemented',who='alice',implementation_self_report_confirmed=True)
    store.review(person('bob'),wid,review(new));save(opps,store,new,expected=2)
    assert 'implementation_assumption_exceeds_budget' in opps.detail(person(),wid)['approval_blockers']
    with pytest.raises(ProcessCollaborationError):decide(opps,store,new,'approved')
    revised=data(new);revised.assessment.implementation_budget='4000.00';save(opps,store,new,revised,expected=3)
    decide(opps,store,new,'approved');assert opps.detail(person(),wid)['current_approved_assessment']


@pytest.mark.parametrize('case,reason',[('unreviewed','independent_design_review_required'),('missing','scenario_missing'),('currency','budget_currency_mismatch'),('budget','implementation_assumption_exceeds_budget')])
def test_source_review_scenario_and_budget_gates(store,case,reason):
    opps=SharedOpportunityStore(store);r=root(store,reviewed=case!='unreviewed',estimate=case!='missing');value=data(r)
    if case=='currency':value.assessment.budget_currency='EUR'
    if case=='budget':value.assessment.implementation_budget='1799.99'
    save(opps,store,r,value);view=opps.detail(person('bob'),r['workflow_id']);assert reason in view['approval_blockers']
    assert 'approved' not in view['allowed_transitions']
    with pytest.raises(ProcessCollaborationError):decide(opps,store,r,'approved')


def test_compliance_declaration_and_reviewer_withdrawal_are_named_decisions(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];value=data(r);value.assessment.compliance_review_required=True;save(opps,store,r,value)
    with pytest.raises(ProcessCollaborationError):decide(opps,store,r,'approved')
    decide(opps,store,r,'approved',compliance_review_declared_complete=True)
    approved=opps.detail(person(),wid)['artifact'];assert approved['approval']['compliance_review_declared_complete']
    assert not approved['approval']['compliance_evidence_verified']
    decide(opps,store,r,'on_hold');held=opps.detail(person(),wid)
    assert held['artifact']['approval'] is None and held['artifact']['last_decision']['actor_id']=='bob'
    assert not held['current_approved_assessment']


def test_idea_rejection_reopen_and_exact_revision_cas(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];value=data(r);value.assessment=None;save(opps,store,r,value)
    assert opps.detail(person(),wid)['artifact']['state']=='idea'
    with pytest.raises(ProcessCollaborationError):decide(opps,store,r,'approved')
    decide(opps,store,r,'rejected');decide(opps,store,r,'idea',who='alice')
    assert opps.detail(person(),wid)['artifact']['state']=='idea'
    with pytest.raises(ProcessCollaborationError):save(opps,store,r,expected=1)
    save(opps,store,r,expected=3)
    view=opps.detail(person('bob'),wid)
    with pytest.raises(ProcessCollaborationError):opps.transition(person('bob'),wid,Transition(target='approved',expected_revision=4,expected_access_revision=2,expected_assessment_sha256='a'*64,user_confirmed=True))
    assert view['artifact']['state']=='assessing'


def test_scope_role_tenant_and_live_revocation(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r)
    for p in [person('reader'),person('bob'),person(org='other'),person(scopes=frozenset({'org:process:read'}))]:
        with pytest.raises(ProcessCollaborationError):save(opps,store,r,expected=1,who=p)
    assert opps.inventory(person(org='other'))['opportunities']==[]
    for p in [person(org='other'),person(scopes=frozenset())]:
        with pytest.raises(ProcessCollaborationError):opps.detail(p,wid)
    grant(store,wid,members=[{'actor_id':'ed','role':'editor'}],expected=2)
    with pytest.raises(ProcessCollaborationError):decide(opps,store,r,'approved')
    assert opps.detail(person('bob'),wid)['artifact'] # reading remains tenant-wide, not edit membership
    assert not opps.detail(person('bob'),wid)['permissions']['review']


def test_concurrent_approval_vs_reassessment_only_one_revision_wins(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r);a=opps.detail(person(),wid)['artifact']
    body=Transition(target='approved',expected_revision=1,expected_access_revision=2,expected_assessment_sha256=a['assessment_sha256'],user_confirmed=True)
    edit=Save(data=data(r),expected_revision=1,expected_access_revision=2,user_confirmed=True,organization_sharing_confirmed=True)
    def action(reviewing):
        try:return opps.transition(person('bob'),wid,body) if reviewing else opps.save(person('ed'),wid,edit)
        except ProcessCollaborationError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(action,[True,False]))
    assert sum(r is not None for r in results)==1
    assert opps.detail(person(),wid)['latest_revision']==2


def test_archive_delete_corrupt_history_and_minimal_audits(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r)
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=2,archived=True,user_confirmed=True))
    assert not opps.detail(person(),wid)['permissions']['edit']
    with pytest.raises(ProcessCollaborationError):save(opps,store,r,expected=1)
    opps.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=3,user_confirmed=True))
    assert opps.detail(person(),wid)['latest_revision']==0
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=3,archived=False,user_confirmed=True));save(opps,store,r)
    with store.db.connect() as c:store.db._execute(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',('{"PRIVATE":"bad"}',namespace('acme'),wid))
    view=opps.detail(person(),wid);assert view['artifact'] is None and not view['integrity_ok']
    assert 'PRIVATE' not in json.dumps(opps.inventory(person()))
    with pytest.raises(ProcessCollaborationError):opps.detail(person(),wid,1,exporting=True)
    opps.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=4,user_confirmed=True))
    save(opps,store,r);store.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=4,user_confirmed=True))
    with store.db.connect() as c:
        assert store.db._execute(c,'SELECT COUNT(*) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace('acme'),wid)).fetchone()[0]==0
        rows=store.db._execute(c,'SELECT details_json FROM audit_log WHERE action LIKE ?',('process.opportunity.%',)).fetchall()
        assert rows and all(not {'title','description','assessment','budget','note','team_label','coordinator_label'}&json.loads(row[0]).keys() for row in rows)


def test_independent_scoring_oracle_and_budget_normalization():
    r={'workflow_id':'root','revision':1,'content_sha256':'a'*64};value=data(r)
    assert score(value,scenario())['quadrant']=='quick_win'
    almost=score(value,scenario(monthly_cases=119.9999))
    assert almost['modeled_hours_per_month']=='20.00' and almost['quadrant']=='fill_in'
    value.assessment.ease.rules_based=1;value.assessment.ease.structured_inputs=1
    assert score(value,scenario())['ease_score']==45 and score(value,scenario())['quadrant']=='major_project'
    assert score(value,scenario(monthly_cases=119.9999))['quadrant']=='deprioritize'
    parsed=Assessment.model_validate({'ease':{k:1 for k in value.assessment.ease.model_dump()},'budget_currency':'USD','implementation_budget':'20.5'})
    assert parsed.implementation_budget=='20.50'
    for budget in [20.5,'-1','1e3','01.00','1000000000.01','1.001']:
        with pytest.raises(ValidationError):Assessment.model_validate({**parsed.model_dump(),'implementation_budget':budget})
    with pytest.raises(ValidationError):Assessment.model_validate({**parsed.model_dump(),'ease':{**parsed.ease.model_dump(),'rules_based':True}})


def test_gateway_auth_parser_and_manifest(tmp_path):
    application=app(tmp_path)
    with TestClient(application) as client:
        h={'Authorization':'Bearer alice'}
        value=model().model_dump();value['opportunity_scenario']=scenario()
        r=client.post('/v1/human/processes',headers=h,json={'title':'Root','process_model':value,'user_confirmed':True,'organization_sharing_confirmed':True}).json()['process'];base='/v1/human/processes/'+r['workflow_id']+'/opportunity'
        body=Save(data=data(r),expected_access_revision=1,user_confirmed=True,organization_sharing_confirmed=True).model_dump()
        assert client.post(base,headers={'Authorization':'Bearer reader','Content-Type':'application/json'},content='not json').status_code==403
        made=client.post(base,headers=h,json=body);assert made.status_code==200,made.text
        assert client.get(base+'/export?revision=1',headers=h).status_code==200
        assert client.get(base,headers={'Authorization':'Bearer foreign'}).status_code==404
        duplicate=client.post(base,headers={**h,'Content-Type':'application/json'},content='{"expected_revision":0,"expected_revision":1}')
        assert duplicate.status_code==422
        assert client.post(base,headers={**h,'Content-Type':'application/json'},content=' '*65537).status_code==413
        assert client.get('/process-opportunity-client.js').headers['cache-control']=='no-store'


def test_approval_cannot_reuse_an_old_assessment_after_equal_value_reassessment(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r);a=opps.detail(person(),wid)['artifact']
    save(opps,store,r,expected=1)
    newer=opps.detail(person(),wid)['artifact'];assert a['assessment_sha256']==newer['assessment_sha256']
    assert newer['assessed_in_revision']==2 and newer['approval'] is None
    with pytest.raises(ProcessCollaborationError):opps.transition(person('bob'),wid,Transition(target='approved',expected_revision=1,expected_access_revision=2,expected_assessment_sha256=a['assessment_sha256'],user_confirmed=True))
    decide(opps,store,r,'approved');assert opps.detail(person(),wid)['artifact']['approval']['assessed_in_revision']==2


def test_readonly_scopes_cannot_turn_review_role_into_write_or_review_authority(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r);a=opps.detail(person(),wid)['artifact']
    p=person('bob',scopes=frozenset({'org:process:read'}))
    assert opps.detail(p,wid)['allowed_transitions']==[]
    with pytest.raises(ProcessCollaborationError):opps.transition(p,wid,Transition(target='approved',expected_revision=1,expected_access_revision=2,expected_assessment_sha256=a['assessment_sha256'],user_confirmed=True))
    with pytest.raises(ProcessCollaborationError):opps.preflight(p,wid,reviewing=True)
    read=person('alice',scopes=frozenset({'org:process:read'}))
    with pytest.raises(ProcessCollaborationError):opps.delete(read,wid,Remove(expected_revision=1,expected_access_revision=2,user_confirmed=True))


def test_review_changed_model_or_corruption_never_remains_currently_approved(store):
    from gateway.process_collaboration import namespace as process_namespace
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r);decide(opps,store,r,'approved')
    store.review(person('bob'),wid,review(r,'changes_requested',expected=1))
    view=opps.detail(person(),wid);assert not view['current_approved_assessment'] and view['effective_state']=='needs_reassessment'
    store.review(person('bob'),wid,review(r,expected=2))
    assert opps.detail(person(),wid)['current_approved_assessment']
    with store.db.connect() as c:
        row=store.db._execute(c,'SELECT payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(process_namespace('acme'),wid)).fetchone()
        bad=json.loads(row[0]);bad['title']='PRIVATE_BAD_SOURCE_TITLE'
        store.db._execute(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',(json.dumps(bad),process_namespace('acme'),wid))
    view=opps.detail(person(),wid);assert view['effective_state']=='needs_reassessment'
    assert view['score'] is None and view['source']['state']=='integrity_failed'
    assert 'PRIVATE_' not in json.dumps(view)


def test_transaction_audit_failures_rollback_assessment_and_decision(store,monkeypatch):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];original=store._audit
    def fail(c,p,action,**details):
        if action=='process.opportunity.saved':raise RuntimeError('audit unavailable')
        return original(c,p,action,**details)
    monkeypatch.setattr(store,'_audit',fail)
    with pytest.raises(RuntimeError):save(opps,store,r)
    assert opps.detail(person(),wid)['latest_revision']==0
    monkeypatch.setattr(store,'_audit',original);save(opps,store,r)
    monkeypatch.setattr(store,'_audit',fail)
    with pytest.raises(RuntimeError):decide(opps,store,r,'approved')
    assert opps.detail(person(),wid)['artifact']['state']=='assessing'
    assert opps.detail(person(),wid)['latest_revision']==1


def test_read_export_audit_failure_withholds_results_and_history_cap_does_not_prune(store,monkeypatch):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id']
    for i in range(50):save(opps,store,r,expected=i)
    with pytest.raises(ProcessCollaborationError,match='fifty'):save(opps,store,r,expected=50)
    assert opps.detail(person(),wid,1)['artifact']['revision']==1
    assert opps.detail(person(),wid)['latest_revision']==50
    monkeypatch.setattr(store.db,'audit',lambda **kw:(_ for _ in ()).throw(RuntimeError('audit unavailable')))
    with pytest.raises(RuntimeError):opps.detail(person(),wid,1,exporting=True)
    with pytest.raises(RuntimeError):opps.inventory(person())


def test_inventory_keyset_and_filter_scope_are_explicit(store):
    opps=SharedOpportunityStore(store);roots=[root(store) for _ in range(3)]
    for r in roots:save(opps,store,r)
    page=opps.inventory(person('reader'),limit=2)
    assert len(page['opportunities'])==2 and page['has_more'] and page['filter_scope']=='loaded_page_only'
    next_page=opps.inventory(person('reader'),after=page['next_after'],limit=2)
    assert len(next_page['opportunities'])==1 and not next_page['has_more']
    assert page['company_savings_total'] is None
    assert {r['workflow_id'] for r in page['opportunities']}.isdisjoint(r['workflow_id'] for r in next_page['opportunities'])


from test_process_signin import signed_portal,start,callback,credentials


def test_company_session_pipeline_scope_and_revocation(signed_portal):
    client,application,db,idp,_=signed_portal;query=start(client);_,h=credentials(callback(client,idp,query))
    response=client.get('/v1/human/process-opportunity-pipeline',headers=h)
    assert response.status_code==200 and response.json()['opportunities']==[]
    assert client.get('/v1/human/me',headers=h).status_code==401
    assert client.post('/v1/human/processes/session/logout',headers=h).status_code==200
    assert client.get('/v1/human/process-opportunity-pipeline',headers=h).status_code==401


@pytest.mark.parametrize('mutate',[
    lambda b:b.update(state='approved'),lambda b:b.update(approval={'actor_id':'fake'}),
    lambda b:b['data']['assessment'].update(implementation_budget=2000),
    lambda b:b['data']['assessment'].update(compliance_review_required='true'),
    lambda b:b['data'].update(title='bad\nvalue'),lambda b:b['data'].update(description='\ud800'),
    lambda b:b['data']['process'].update(revision=True),lambda b:b.update(user_confirmed=1),
])
def test_unsupported_or_authorizing_client_fields_refused(mutate):
    r={'workflow_id':'root','revision':1,'content_sha256':'a'*64}
    b={'data':data(r).model_dump(),'expected_access_revision':1,'user_confirmed':True,'organization_sharing_confirmed':True};mutate(b)
    with pytest.raises((ValidationError,ValueError)):Save.model_validate(b)


def test_reviewer_scope_without_write_can_approve_but_not_edit(store):
    opps=SharedOpportunityStore(store);r=root(store);wid=r['workflow_id'];save(opps,store,r);a=opps.detail(person(),wid)['artifact']
    reviewer=person('bob',scopes=frozenset({'org:process:read','org:process:review'}))
    view=opps.detail(reviewer,wid);assert view['permissions']['review'] and not view['permissions']['edit']
    opps.transition(reviewer,wid,Transition(target='approved',expected_revision=1,expected_access_revision=2,expected_assessment_sha256=a['assessment_sha256'],user_confirmed=True))
    assert opps.detail(person(),wid)['current_approved_assessment']
    with pytest.raises(ProcessCollaborationError):save(opps,store,r,expected=2,who=reviewer)
