"""Explicit contributions: independent arithmetic, reuse/overlap and all-or-nothing totals."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from shared.process_benefits import BenefitPlan, Save, Remove, PortfolioReview, modeled_totals
from gateway.process_benefits import ProcessBenefits, namespace
from gateway.process_collaboration import SharedProcessWrite, ProcessArchiveWrite, ProcessDeleteWrite, ProcessCollaborationError, ProcessReviewWrite
from test_process_collaboration import store, person, write, grant, review, app, model


def scenario(**changes):
    return {'currency':'USD','monthly_cases':100.,'minutes_saved_per_case':10.,'eligible_fraction_percent':100.,
            'loaded_hourly_cost':60.,'recurring_monthly_cost':100.,'one_time_implementation_cost':1800.,'sensitivity_percent':25.,**changes}


def publish(store,title='Root',estimate=False,reviewed=True,who=None):
    who=who or person();value=model().model_dump()
    if estimate:value['opportunity_scenario']=scenario() if estimate is True else estimate
    result=store.save(who,SharedProcessWrite(title=title,process_model=value,user_confirmed=True,organization_sharing_confirmed=True))['process']
    if reviewed:
        grant(store,result['workflow_id'],members=[{'actor_id':'bob','role':'reviewer'}],actor=who)
        store.review(person('bob',org=who.organization_id),result['workflow_id'],review(result))
    return result


def pin(p):return {k:p[k] for k in ('workflow_id','revision','content_sha256')}


def declaration(root,*sources,include=True,scopes=None,disjoint=True,currency='USD'):
    return {'organization_id':'acme','root_process':pin(root),'title':'Support benefit plan','currency':currency,
        'links':[{'id':'link_'+str(i),'scenario_process':pin(p),'include_in_estimate':include,
                  'benefit_scope_id':scopes[i-1] if scopes else 'scope_'+str(i)} for i,p in enumerate(sources,1)],'disjoint_scopes_reviewed':disjoint}


def save(plans,store,root,*sources,revision=0,**changes):
    access=store.detail(person(),root['workflow_id'])['access_revision']
    return plans.save(person(),root['workflow_id'],Save(plan=declaration(root,*sources,**changes),expected_revision=revision,
        expected_access_revision=access,user_confirmed=True,organization_sharing_confirmed=True))


def estimate(plans,currency='USD',who=None,snapshot=None):
    who=who or person();snapshot=snapshot or plans.portfolio(who,currency)
    return plans.portfolio(who,currency,PortfolioReview(currency=currency,expected_portfolio_sha256=snapshot['portfolio_sha256'],
        user_confirmed=True,disjoint_scopes_confirmed=True))


def test_independent_decimal_oracle_no_child_rounding_or_unsafe_json_numbers():
    # 100*10/60 hours, $1000 gross minus $100 recurring, $1800 implementation.
    result=modeled_totals([scenario()])
    assert result['assumed_hours_saved_per_month']=='16.67'
    assert result['gross_labor_value_per_month']=='1000.00' and result['net_monthly_value_before_other_costs']=='900.00'
    assert result['simple_payback_months']=='2.00' and result['sensitivity_net_monthly_low']=='650.00'
    assert result['sensitivity_net_monthly_high']=='1150.00'
    # Two sub-cent estimates round once after summing, not child-first to zero.
    tiny=scenario(monthly_cases=1.,minutes_saved_per_case=1.,loaded_hourly_cost=.24,recurring_monthly_cost=0.,one_time_implementation_cost=0.)
    assert modeled_totals([tiny,tiny])['gross_labor_value_per_month']=='0.01'
    huge=scenario(monthly_cases=10000000.,minutes_saved_per_case=10080.,loaded_hourly_cost=1000000.,recurring_monthly_cost=0.)
    assert modeled_totals([huge]*20)['gross_labor_value_per_month']=='33600000000000000.00'
    assert modeled_totals([scenario(recurring_monthly_cost=1000.)])['simple_payback_months'] is None
    with pytest.raises(ValueError):modeled_totals([scenario(),scenario(currency='EUR')])
    assert result['realized_savings_verified'] is False and not result['overlap_independently_verified']


def test_saved_plan_history_exact_pins_and_no_process_changes(store):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);b=publish(store,'B',True);wid=root['workflow_id']
    save(plans,store,root,a,b)
    first=plans.detail(person('reader'),wid);artifact=deepcopy(first['artifact'])
    assert first['status']=='modeled_estimate' and first['totals']['gross_labor_value_per_month']=='2000.00'
    assert first['included_count']==2 and not first['can_write']
    assert store.detail(person(),wid)['process']==root
    save(plans,store,root,a,b,revision=1,include=False)
    assert plans.detail(person(),wid)['status']=='no_contributions'
    assert plans.detail(person(),wid,1,exporting=True)['artifact']==artifact
    assert plans.detail(person(),wid,1)['totals']['gross_labor_value_per_month']=='2000.00'
    assert not first['normal_implementation_exports_include_plan']


def test_organization_reuse_counted_once_costs_and_benefits_not_multiplied(store):
    plans=ProcessBenefits(store);r1=publish(store,'Root1');r2=publish(store,'Root2');a=publish(store,'Reusable',True)
    save(plans,store,r1,a);save(plans,store,r2,a)
    snapshot=plans.portfolio(person('reader'),'USD');again=plans.portfolio(person('reader'),'USD')
    assert snapshot['portfolio_sha256']==again['portfolio_sha256']
    assert snapshot['totals'] is None and snapshot['status']=='ready_for_review'
    assert snapshot['unique_scenario_count']==1 and len(snapshot['contributors'][0]['linked_root_ids'])==2
    assert all(plan['totals'] is None for plan in snapshot['plans'])
    result=estimate(plans,who=person('reader'),snapshot=snapshot)
    assert result['totals']['gross_labor_value_per_month']=='1000.00'
    assert result['totals']['recurring_monthly_cost']=='100.00' and result['totals']['one_time_implementation_cost']=='1800.00'
    assert result['totals']['net_monthly_value_before_other_costs']=='900.00'
    assert result['totals']['realized_savings_verified'] is False and not result['execution_authorized']
    foreign=plans.portfolio(person(org='elsewhere'),'USD')
    assert foreign['plans']==[] and foreign['contributors']==[] and foreign['totals'] is None


def test_distinct_scope_confirmation_and_overlap_never_produce_partial_totals(store):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);b=publish(store,'B',True)
    save(plans,store,root,a,b,disjoint=False)
    view=plans.detail(person(),root['workflow_id']);assert view['totals'] is None
    assert 'disjoint_scope_review_required' in {b['code'] for b in view['blockers']}
    save(plans,store,root,a,b,revision=1,scopes=['same_pool','same_pool'])
    snapshot=plans.portfolio(person(),'USD');assert snapshot['totals'] is None and snapshot['status']=='withheld'
    assert 'overlapping_declared_scope' in {b['code'] for b in snapshot['blockers']}
    assert all(p['totals'] is None for p in snapshot['plans'])
    with pytest.raises(ProcessCollaborationError):estimate(plans,snapshot=snapshot)
    save(plans,store,root,a,b,revision=2)
    snapshot=plans.portfolio(person(),'USD')
    with pytest.raises(ProcessCollaborationError):plans.portfolio(person(),'USD',PortfolioReview(currency='USD',expected_portfolio_sha256=snapshot['portfolio_sha256'],user_confirmed=True))
    assert estimate(plans)['totals']['gross_labor_value_per_month']=='2000.00'


def test_reused_scenario_scope_or_revision_conflicts_withhold_entire_portfolio(store):
    plans=ProcessBenefits(store);r1=publish(store,'R1');r2=publish(store,'R2');a=publish(store,'Reusable',True)
    save(plans,store,r1,a);save(plans,store,r2,a,scopes=['different_scope'])
    assert 'inconsistent_reused_scenario' in {b['code'] for b in plans.portfolio(person(),'USD')['blockers']}
    next_a=store.save(person(),SharedProcessWrite(title='A latest',process_model=a['process_model'],expected_revision=1,user_confirmed=True,organization_sharing_confirmed=True),a['workflow_id'])['process']
    store.review(person('bob'),a['workflow_id'],review(next_a))
    save(plans,store,r2,next_a,revision=1)
    snapshot=plans.portfolio(person(),'USD');codes={b['code'] for b in snapshot['blockers']}
    assert {'revision_changed','inconsistent_reused_scenario'}<=codes and snapshot['totals'] is None


@pytest.mark.parametrize('change,code', [('archive','archived'),('delete','unavailable'),('review','independent_design_review_required'),('revision','revision_changed')])
def test_contributor_drift_withholds_and_historical_artifacts_do_not_change(store,change,code):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);b=publish(store,'B',True);wid=root['workflow_id']
    save(plans,store,root,a,b);before=plans.detail(person(),wid)['artifact'];aw=a['workflow_id']
    if change=='archive':store.archive(person(),aw,ProcessArchiveWrite(expected_access_revision=2,archived=True,user_confirmed=True))
    if change=='delete':store.delete(person(),aw,ProcessDeleteWrite(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    if change=='review':store.review(person('bob'),aw,review(a,'changes_requested',expected=1))
    if change=='revision':store.save(person(),SharedProcessWrite(title='Changed',process_model=a['process_model'],expected_revision=1,user_confirmed=True,organization_sharing_confirmed=True),aw)
    view=plans.detail(person(),wid,1,exporting=True)
    assert view['artifact']==before and view['totals'] is None and code in {b['code'] for b in view['blockers']}
    snapshot=plans.portfolio(person(),'USD');assert snapshot['totals'] is None and snapshot['status']=='withheld'
    if change=='delete':assert 'A' not in json.dumps(next(r for r in view['resolutions'] if r['link_id']=='link_1'))


def test_excluded_unavailable_or_other_currency_links_do_not_contribute(store):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);b=publish(store,'B',scenario(currency='EUR'));wid=root['workflow_id']
    plan=declaration(root,a,b);plan['links'][1]['include_in_estimate']=False
    plans.save(person(),wid,Save(plan=plan,expected_access_revision=2,user_confirmed=True,organization_sharing_confirmed=True))
    store.delete(person(),b['workflow_id'],ProcessDeleteWrite(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    assert plans.detail(person(),wid)['totals']['gross_labor_value_per_month']=='1000.00'
    assert estimate(plans)['totals']['gross_labor_value_per_month']=='1000.00'


def test_currency_mismatch_and_separate_portfolio_currencies(store):
    plans=ProcessBenefits(store);root=publish(store);e=publish(store,'Euro',scenario(currency='EUR'))
    save(plans,store,root,e)
    assert plans.detail(person(),root['workflow_id'])['totals'] is None
    assert 'currency_mismatch' in {b['code'] for b in plans.portfolio(person(),'USD')['blockers']}
    save(plans,store,root,e,revision=1,currency='EUR')
    assert plans.portfolio(person(),'USD')['status']=='no_contributions'
    assert plans.portfolio(person(),'USD')['other_currency_plan_count']==1
    assert estimate(plans,'EUR')['totals']['currency']=='EUR'


def test_fingerprint_review_rejects_source_changes_between_inspection_and_calculation(store):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);save(plans,store,root,a)
    snapshot=plans.portfolio(person(),'USD')
    store.archive(person(),a['workflow_id'],ProcessArchiveWrite(expected_access_revision=2,archived=True,user_confirmed=True))
    with pytest.raises(ProcessCollaborationError,match='changed'):estimate(plans,snapshot=snapshot)
    assert plans.portfolio(person(),'USD')['portfolio_sha256']!=snapshot['portfolio_sha256']


def test_current_scopes_roles_access_counter_and_concurrent_cas(store):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);wid=root['workflow_id'];body=Save(plan=declaration(root,a),expected_access_revision=2,user_confirmed=True,organization_sharing_confirmed=True)
    for p in [person('reader'),person(org='other'),person(scopes=frozenset({'org:process:read'}))]:
        with pytest.raises(ProcessCollaborationError):plans.save(p,wid,body)
    for p in [person(org='other'),person(scopes=frozenset())]:
        with pytest.raises(ProcessCollaborationError):plans.detail(p,wid)
    def attempt():
        try:return plans.save(person(),wid,body)['revision']
        except ProcessCollaborationError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(lambda _:attempt(),range(2)),key=str)==[1,'conflict']
    grant(store,wid,members=[{'actor_id':'editor','role':'editor'}],expected=2)
    with pytest.raises(ProcessCollaborationError,match='access changed'):plans.save(person('editor'),wid,body)
    grant(store,wid,members=[],expected=3)
    with pytest.raises(ProcessCollaborationError):plans.save(person('editor'),wid,Save(**{**body.model_dump(),'expected_access_revision':4,'expected_revision':1}))
    assert plans.detail(person('reader'),wid)['artifact'] # current tenant-wide process-read contract


def test_corrupt_plan_or_source_withholds_and_owner_can_erase(store):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);wid=root['workflow_id'];save(plans,store,root,a)
    with store.db.connect() as c:store.db._execute(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',('{"bad":"PRIVATE_PAYLOAD"}',namespace('acme'),wid))
    view=plans.detail(person(),wid);assert not view['integrity_ok'] and view['artifact'] is None and view['totals'] is None
    result=plans.portfolio(person(),'USD');assert result['status']=='withheld' and 'PRIVATE_' not in json.dumps(result)
    with pytest.raises(ProcessCollaborationError):plans.detail(person(),wid,1,exporting=True)
    plans.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    assert plans.detail(person(),wid)['latest_revision']==0 and store.detail(person(),wid)['process']==root


def test_root_deletion_erases_only_its_plans_and_keeps_other_unavailable_links(store):
    plans=ProcessBenefits(store);r1=publish(store,'Root1',True);r2=publish(store,'Root2');a=publish(store,'A',True)
    save(plans,store,r1,a);save(plans,store,r2,r1)
    store.delete(person(),r1['workflow_id'],ProcessDeleteWrite(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    with store.db.connect() as c:assert store.db._execute(c,'SELECT COUNT(*) FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(namespace('acme'),r1['workflow_id'])).fetchone()[0]==0
    view=plans.detail(person(),r2['workflow_id']);assert view['totals'] is None and view['resolutions'][0]['state']=='unavailable'
    assert store.detail(person(),a['workflow_id'])['process']==a


@pytest.mark.parametrize('mutate',[
    lambda p:p['links'].append(deepcopy(p['links'][0])),lambda p:p.update(execution_authorized=True),
    lambda p:p.update(realized_savings_verified=True),lambda p:p.update(disjoint_scopes_reviewed='true'),
    lambda p:p['links'][0].update(include_in_estimate=1),lambda p:p['links'][0].update(benefit_scope_id='private key with spaces'),
    lambda p:p['root_process'].update(revision=True),lambda p:p.update(title='   '),lambda p:p.update(title='Private\ntext'),
])
def test_malformed_or_authorizing_plans_refused_before_storage(store,mutate):
    root=publish(store);a=publish(store,'A',True);p=declaration(root,a);mutate(p)
    with pytest.raises((ValidationError,ValueError)):BenefitPlan.model_validate(p)
    assert ProcessBenefits(store).detail(person(),root['workflow_id'])['artifact'] is None


def test_history_limit_and_audit_failure_roll_back_changes(store,monkeypatch):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);wid=root['workflow_id']
    for i in range(20):save(plans,store,root,a,revision=i)
    with pytest.raises(ProcessCollaborationError,match='twenty'):save(plans,store,root,a,revision=20)
    assert plans.detail(person(),wid)['latest_revision']==20
    # Inspection audit failures must withhold the computed response too.
    monkeypatch.setattr(store.db,'audit',lambda **kw:(_ for _ in ()).throw(RuntimeError('audit unavailable')))
    with pytest.raises(RuntimeError):plans.detail(person(),wid)
    with pytest.raises(RuntimeError):estimate(plans)


def test_gateway_scopes_parser_preflight_audits_and_manifest(tmp_path):
    application=app(tmp_path)
    with TestClient(application) as client:
        owner={'Authorization':'Bearer alice'}
        value=model().model_dump();value['opportunity_scenario']=scenario()
        r=client.post('/v1/human/processes',headers=owner,json={'title':'Root scenario','process_model':value,'user_confirmed':True,'organization_sharing_confirmed':True}).json()['process']
        wid=r['workflow_id'];base='/v1/human/processes/'+wid+'/benefits'
        body=Save(plan=declaration(r,r),expected_access_revision=1,user_confirmed=True,organization_sharing_confirmed=True).model_dump()
        refused=client.post(base,headers={'Authorization':'Bearer reader','Content-Type':'application/json'},content='not json')
        assert refused.status_code==403
        made=client.post(base,headers=owner,json=body);assert made.status_code==200,made.text
        assert client.get(base+'/export?revision=1',headers=owner).status_code==200
        assert client.get(base,headers={'Authorization':'Bearer foreign'}).status_code==404
        invalid=client.post('/v1/human/process-benefit-portfolio/estimate',headers=owner,json={'currency':'USD','expected_portfolio_sha256':'a'*64,'user_confirmed':True,'disjoint_scopes_confirmed':True})
        assert invalid.status_code==409
        duplicate=client.post(base,headers={**owner,'Content-Type':'application/json'},content='{"expected_revision":0,"expected_revision":1}')
        assert duplicate.status_code==422
        large=client.post(base,headers={**owner,'Content-Type':'application/json'},content=' '*65537)
        assert large.status_code==413
        script=client.get('/process-benefit-client.js');assert script.status_code==200 and script.headers['cache-control']=='no-store'


def test_corrupt_source_never_yields_partial_value_or_its_title(store):
    from gateway.process_collaboration import namespace as process_namespace
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'PRIVATE_SOURCE_TITLE',True);b=publish(store,'B',True);save(plans,store,root,a,b)
    with store.db.connect() as c:
        row=store.db._execute(c,'SELECT payload FROM workflow_knowledge WHERE owner=? AND workflow_id=?',(process_namespace('acme'),a['workflow_id'])).fetchone()
        payload=json.loads(row[0]);payload['title']='CORRUPT_UNTRUSTED_TITLE'
        store.db._execute(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',(json.dumps(payload),process_namespace('acme'),a['workflow_id']))
    view=plans.detail(person(),root['workflow_id']);assert view['totals'] is None
    bad=next(r for r in view['resolutions'] if r['workflow_id']==a['workflow_id'])
    assert bad['state']=='integrity_failed' and 'title' not in bad and bad['scenario'] is None
    snapshot=plans.portfolio(person(),'USD');assert snapshot['totals'] is None and all(p['totals'] is None for p in snapshot['plans'])
    assert 'PRIVATE_' not in json.dumps(snapshot) and 'CORRUPT_' not in json.dumps(snapshot)


def test_opposite_root_reuse_locks_in_one_global_order(store):
    plans=ProcessBenefits(store);a=publish(store,'A',True);b=publish(store,'B',True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(save,plans,store,a,b)
        second=pool.submit(save,plans,store,b,a)
        assert first.result(timeout=20)['revision']==second.result(timeout=20)['revision']==1
    assert store.detail(person(),a['workflow_id'])['process']==a
    assert store.detail(person(),b['workflow_id'])['process']==b


def test_scan_cap_withholds_incomplete_portfolios_without_pruning(store):
    plans=ProcessBenefits(store)
    for i in range(101):
        root=publish(store,'Root '+str(i),reviewed=False)
        save(plans,store,root)
    snapshot=plans.portfolio(person(),'USD')
    assert snapshot['scan_truncated'] and snapshot['status']=='withheld' and snapshot['totals'] is None
    assert len(snapshot['plans'])==100
    assert 'portfolio_scan_truncated' in {b['code'] for b in snapshot['blockers']}
    with pytest.raises(ProcessCollaborationError):estimate(plans,snapshot=snapshot)
    with store.db.connect() as c:assert store.db._execute(c,'SELECT COUNT(*) FROM workflow_knowledge WHERE owner=?',(namespace('acme'),)).fetchone()[0]==101


def test_save_and_erase_audit_failure_roll_back_and_export_is_fresh_audited(store,monkeypatch):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);wid=root['workflow_id'];original=store._audit
    def fail_save(c,p,action,**details):
        if action=='process.benefits.saved':raise RuntimeError('audit failure')
        return original(c,p,action,**details)
    monkeypatch.setattr(store,'_audit',fail_save)
    with pytest.raises(RuntimeError):save(plans,store,root,a)
    assert plans.detail(person(),wid)['latest_revision']==0
    monkeypatch.setattr(store,'_audit',original)
    save(plans,store,root,a)
    snapshot=plans.portfolio(person(),'USD');export=plans.portfolio(person(),'USD',PortfolioReview(currency='USD',expected_portfolio_sha256=snapshot['portfolio_sha256'],user_confirmed=True),exporting=True)
    assert export['totals'] is None and export['portfolio_sha256']==snapshot['portfolio_sha256']
    computed=estimate(plans,snapshot=snapshot)
    exported=plans.portfolio(person(),'USD',PortfolioReview(currency='USD',expected_portfolio_sha256=snapshot['portfolio_sha256'],user_confirmed=True,disjoint_scopes_confirmed=True),exporting=True)
    assert exported['totals']==computed['totals']
    def fail_delete(c,p,action,**details):
        if action=='process.benefits.deleted':raise RuntimeError('audit failure')
        return original(c,p,action,**details)
    monkeypatch.setattr(store,'_audit',fail_delete)
    with pytest.raises(RuntimeError):plans.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    assert plans.detail(person(),wid)['latest_revision']==1
    with store.db.connect() as c:
        audits=[json.loads(r[0]) for r in store.db._execute(c,'SELECT details_json FROM audit_log WHERE action LIKE ?',('process.benefits.%',)).fetchall()]
        assert audits and all(not {'title','scenario','links','totals','benefit_scope_id'}&r.keys() for r in audits)
        exported_count=store.db._execute(c,'SELECT COUNT(*) FROM audit_log WHERE action=?',('process.benefits.portfolio.exported',)).fetchone()[0]
        assert exported_count==2


def test_different_roots_cannot_add_alternative_scenarios_in_the_same_scope(store):
    plans=ProcessBenefits(store);r1=publish(store,'Root1');r2=publish(store,'Root2');a=publish(store,'A',True);b=publish(store,'B',True)
    save(plans,store,r1,a);save(plans,store,r2,b)
    assert plans.detail(person(),r1['workflow_id'])['totals']['gross_labor_value_per_month']=='1000.00'
    snapshot=plans.portfolio(person(),'USD');assert snapshot['totals'] is None
    assert all(p['totals'] is None for p in snapshot['plans'])
    assert 'overlapping_declared_scope' in {b['code'] for b in snapshot['blockers']}
    with pytest.raises(ProcessCollaborationError):estimate(plans,snapshot=snapshot)


def test_refreshing_root_pin_does_not_inherit_review_of_the_previous_model(store):
    plans=ProcessBenefits(store);root=publish(store);a=publish(store,'A',True);wid=root['workflow_id'];save(plans,store,root,a)
    model=deepcopy(root['process_model']);model['goal']='Review changed root business process'
    new=store.save(person(),SharedProcessWrite(title='Latest root',process_model=model,expected_revision=1,user_confirmed=True,organization_sharing_confirmed=True),wid)['process']
    assert {'root_revision_changed'}<={b['code'] for b in plans.detail(person(),wid)['blockers']}
    save(plans,store,new,a,revision=1)
    assert {'root_independent_design_review_required'}<={b['code'] for b in plans.detail(person(),wid)['blockers']}
    store.review(person('bob'),wid,review(new))
    assert plans.detail(person(),wid)['totals']['gross_labor_value_per_month']=='1000.00'


def test_link_count_scope_key_and_title_bounds():
    r={'workflow_id':'root','revision':1,'content_sha256':'a'*64}
    p=declaration(r)
    p['links']=[{'id':'l'+str(i),'scenario_process':{**r,'workflow_id':'scenario_'+str(i)},'include_in_estimate':True,'benefit_scope_id':'scope_'+str(i)} for i in range(21)]
    with pytest.raises(ValidationError):BenefitPlan.model_validate(p)
    for title in ['x'*121,'\ud800','bad\u2028label']:
        p=declaration(r);p['title']=title
        with pytest.raises((ValidationError,ValueError)):BenefitPlan.model_validate(p)


from test_process_signin import signed_portal, start, callback, credentials


def test_company_process_session_can_inspect_and_export_but_revocation_still_applies(signed_portal):
    client,application,db,idp,_=signed_portal
    query=start(client);_,headers=credentials(callback(client,idp,query))
    response=client.get('/v1/human/process-benefit-portfolio?currency=USD',headers=headers)
    assert response.status_code==200,response.text
    snapshot=response.json();assert snapshot['status']=='no_contributions' and snapshot['totals'] is None
    exported=client.post('/v1/human/process-benefit-portfolio/export',headers=headers,json={
        'currency':'USD','expected_portfolio_sha256':snapshot['portfolio_sha256'],'user_confirmed':True})
    assert exported.status_code==200 and exported.json()['totals'] is None
    assert client.get('/v1/human/me',headers=headers).status_code==401
    assert client.post('/v1/human/processes/session/logout',headers=headers).status_code==200
    assert client.get('/v1/human/process-benefit-portfolio?currency=USD',headers=headers).status_code==401
