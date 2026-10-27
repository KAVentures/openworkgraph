"""Independent phase reviews, exact baselines, reported/unknown costs and source drift."""
from copy import deepcopy
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from shared.process_implementation_plan import Baseline,PhaseReport,SaveBaseline,SaveReports,Freeze,Remove,totals
from gateway.process_implementation_plans import ImplementationPlans,namespace,pin
from gateway.process_collaboration import ProcessCollaborationError as Error,ProcessArchiveWrite,ProcessDeleteWrite
from gateway.process_opportunity_review import SharedOpportunityStore
from test_shared_opportunity_review import root,data,save,decide
from test_process_collaboration import store,person,grant,app


def setup(store,approved=True):
    r=root(store);opps=SharedOpportunityStore(store);save(opps,store,r)
    if approved:decide(opps,store,r,'approved')
    opportunity=opps.detail(person(),r['workflow_id'])['artifact']
    baseline=Baseline(title='Implementation plan',currency='USD',opportunity=pin(opportunity),phases=[
        {'id':'analysis','name':'Analysis','cost':'1100','effort_hours':'20','starts_on':'2026-09-01','finishes_on':'2026-09-04'},
        {'id':'build','name':'Build','cost':'700','effort_hours':'10','starts_on':'2026-09-03','finishes_on':'2026-09-10'}])
    return r,opps,baseline

def baseline_save(plans,r,baseline,revision=0,who='alice'):
    return plans.save_baseline(person(who),r['workflow_id'],SaveBaseline(baseline=baseline,expected_revision=revision,expected_access_revision=2,user_confirmed=True,organization_sharing_confirmed=True))

def freeze(plans,r,who='bob'):
    view=plans.detail(person(who),r['workflow_id']);return plans.freeze(person(who),r['workflow_id'],Freeze(expected_approved_opportunity=view['source']['opportunity_reference'],expected_revision=view['latest_revision'],expected_access_revision=view['access_revision'],expected_baseline_sha256=view['artifact']['baseline_sha256'],user_confirmed=True))

def reports(plans,r,rows,who='alice',**changes):
    view=plans.detail(person(who),r['workflow_id']);values={'expected_revision':view['latest_revision'],'expected_access_revision':view['access_revision'],'expected_baseline_sha256':view['artifact']['baseline_sha256'],'reports':rows,'user_confirmed':True,'organization_sharing_confirmed':True,**changes}
    return plans.save_reports(person(who),r['workflow_id'],SaveReports.model_validate(values))


def test_frozen_baseline_and_independent_actor_survive_separate_reports_with_exact_history(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id'];opportunity=deepcopy(opps.detail(person(),wid)['artifact'])
    baseline_save(plans,r,baseline);before=plans.detail(person(),wid)
    assert before['effective_state']=='draft' and before['totals']['cost_to_date'] is None
    with pytest.raises(Error,match='different'):freeze(plans,r,who='alice')
    freeze(plans,r);frozen=plans.detail(person(),wid);a=deepcopy(frozen['artifact'])
    assert frozen['current_reviewed_baseline'] and a['review']['actor_id']=='bob'
    reports(plans,r,[{'phase_id':'analysis','status':'in_progress','cost_to_date':'1000','effort_hours_to_date':'18','remaining_cost_estimate':'200','remaining_effort_hours_estimate':'4','started_on':'2026-09-01'}],who='ed')
    current=plans.detail(person(),wid);saved=current['artifact']
    assert saved['baseline']==a['baseline'] and saved['baseline_sha256']==a['baseline_sha256'] and saved['review']==a['review']
    assert saved['baseline_author_actor_id']=='alice' and saved['author_actor_id']=='ed'
    assert saved['report_attributions'][0]['actor_id']=='ed' and saved['report_attributions'][0]['reported_in_revision']==3
    assert current['current_reviewed_baseline'] and current['totals']['cost_to_date'] is None
    assert plans.detail(person(),wid,2,exporting=True)['artifact']==a
    assert not plans.detail(person(),wid,2)['current_reviewed_baseline'] and plans.detail(person(),wid,2)['effective_state']=='historical_snapshot'
    assert opps.detail(person(),wid)['artifact']==opportunity
    assert plans.store.detail(person(),wid)['process']==r


def test_decimal_unknown_coverage_overlapping_calendar_span_and_no_double_count_or_realized_value():
    ref={'workflow_id':'process','revision':1,'content_sha256':'a'*64}
    baseline=Baseline(title='Costs',currency='USD',opportunity=ref,phases=[{'id':'a','name':'A','cost':'0.10','effort_hours':'1.10','starts_on':'2026-01-01','finishes_on':'2026-01-05'},
        {'id':'b','name':'B','cost':'0.20','effort_hours':'2.20','starts_on':'2026-01-03','finishes_on':'2026-01-06'}]).model_dump(mode='json')
    values=[PhaseReport(phase_id='a',cost_to_date='0.10',effort_hours_to_date='1.10',remaining_cost_estimate='0.20',remaining_effort_hours_estimate='1.10').model_dump(mode='json')]
    first=totals(baseline,values);assert first['baseline_cost']=='0.30' and first['baseline_effort_hours']=='3.30'
    assert first['baseline_calendar_span_days']==6 and first['cost_to_date'] is None and first['coverage']['cost_to_date']=={'known_phases':1,'unknown_phases':1}
    values.append(PhaseReport(phase_id='b',cost_to_date='0.20',effort_hours_to_date='2.20',remaining_cost_estimate='0.40',remaining_effort_hours_estimate='2.20').model_dump(mode='json'))
    result=totals(baseline,values);assert result['cost_to_date']=='0.30' and result['remaining_cost_estimate']=='0.60' and result['new_total_cost_estimate']=='0.90'
    assert result['variance_cost_from_baseline']=='0.60' and result['new_total_effort_hours_estimate']=='6.60'
    assert not result['actual_values_independently_verified'] and not result['realized_savings_verified'] and not result['spending_authorized']
    values[1]['remaining_cost_estimate']=None
    assert totals(baseline,values)['new_total_cost_estimate'] is None


def test_reported_overbudget_is_retained_as_data_without_spending_authority(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);baseline_save(plans,r,baseline);freeze(plans,r)
    reports(plans,r,[{'phase_id':'analysis','cost_to_date':'2500','effort_hours_to_date':'25','remaining_cost_estimate':'0','remaining_effort_hours_estimate':'0'},
        {'phase_id':'build','cost_to_date':'0','effort_hours_to_date':'0','remaining_cost_estimate':'700','remaining_effort_hours_estimate':'10'}])
    result=plans.detail(person(),r['workflow_id']);assert result['current_reviewed_baseline']
    assert result['totals']['new_total_cost_estimate']=='3200.00' and result['totals']['variance_cost_from_baseline']=='1400.00'
    assert result['totals']['cost_to_date_exceeds_budget_context'] and result['totals']['new_total_cost_estimate_exceeds_budget_context']
    assert not result['spending_authorized'] and not result['actual_values_independently_verified']


def test_draft_can_precede_opportunity_approval_and_direct_implementation_preserves_named_approval_basis(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store,approved=False);wid=r['workflow_id'];baseline_save(plans,r,baseline)
    with pytest.raises(Error):freeze(plans,r)
    decide(opps,store,r,'approved');freeze(plans,r)
    assert plans.detail(person(),wid)['current_reviewed_baseline']
    decide(opps,store,r,'implemented',who='alice',implementation_self_report_confirmed=True)
    assert plans.detail(person(),wid)['current_reviewed_baseline']  # Same named approval, only implementation self-report.
    assert plans.detail(person(),wid)['artifact']['review']['approved_opportunity']['revision']==2


def test_new_approval_after_hold_requires_reassessment_even_with_identical_time_and_payload_fields(store,monkeypatch):
    import gateway.process_opportunity_review as opportunity_module
    monkeypatch.setattr(opportunity_module,'_now',lambda:'2026-09-01T00:00:00+00:00')
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id'];baseline_save(plans,r,baseline);freeze(plans,r)
    old=opps.detail(person(),wid)['artifact']['approval']
    decide(opps,store,r,'on_hold');decide(opps,store,r,'assessing',who='alice');decide(opps,store,r,'approved')
    assert opps.detail(person(),wid)['artifact']['approval']==old
    result=plans.detail(person(),wid);assert not result['current_reviewed_baseline'] and 'opportunity_approval_changed' in result['source']['blockers']


def test_reassessment_clears_current_baseline_but_allows_historical_reports_then_rebaseline_review(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id'];baseline_save(plans,r,baseline);freeze(plans,r);original=deepcopy(plans.detail(person(),wid)['artifact'])
    save(opps,store,r,expected=2)
    result=plans.detail(person(),wid);assert result['effective_state']=='needs_reassessment' and not result['current_reviewed_baseline']
    assert plans.detail(person(),wid)['artifact']==original
    reports(plans,r,[{'phase_id':'analysis','cost_to_date':'100'}],who='ed')
    assert plans.detail(person(),wid)['artifact']['review']==original['review']
    baseline.opportunity=type(baseline.opportunity).model_validate(pin(opps.detail(person(),wid)['artifact']));baseline.phases[0].cost='1200.00'
    baseline_save(plans,r,baseline,revision=3)
    revised=plans.detail(person(),wid);assert revised['artifact']['review'] is None and revised['artifact']['reports'][0]['cost_to_date']=='100.00'
    assert revised['artifact']['report_attributions'][0]['actor_id']=='ed'
    with pytest.raises(Error):freeze(plans,r)
    decide(opps,store,r,'approved');freeze(plans,r)
    assert plans.detail(person(),wid)['current_reviewed_baseline']
    assert plans.detail(person(),wid,2)['artifact']==original


def test_reported_phase_cannot_be_removed_or_renamed_and_each_report_keeps_its_actual_submitter(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);baseline_save(plans,r,baseline)
    reports(plans,r,[{'phase_id':'analysis','cost_to_date':'100'}],who='ed')
    reports(plans,r,[{'phase_id':'build','cost_to_date':'10'}],who='alice')
    attribution={r['phase_id']:r['actor_id'] for r in plans.detail(person(),r['workflow_id'])['artifact']['report_attributions']};assert attribution=={'analysis':'ed','build':'alice'}
    for mode in ('remove','rename'):
        bad=baseline.model_copy(deep=True)
        if mode=='remove':bad.phases=bad.phases[1:]
        else:bad.phases[0].name='Different work'
        with pytest.raises(Error):baseline_save(plans,r,bad,revision=3)
    baseline_save(plans,r,baseline,revision=3,who='ed')
    current=plans.detail(person(),r['workflow_id']);assert current['artifact']['baseline_author_actor_id']=='ed' and current['artifact']['review'] is None
    freeze(plans,r,who='alice');assert plans.detail(person(),r['workflow_id'])['artifact']['review']['actor_id']=='alice'


@pytest.mark.parametrize('changes',[
    {'cost_to_date':1},{'cost_to_date':'NaN'},{'cost_to_date':'01.00'},{'cost_to_date':'1000000001'},
    {'effort_hours_to_date':'1000001'},{'started_on':'2026-02-30'},
    {'status':'completed'},{'actual_values_independently_verified':True},{'business_outcome_verified':True},
])
def test_invalid_or_promoted_report_claims_refused(changes):
    with pytest.raises(ValidationError):PhaseReport.model_validate({'phase_id':'analysis',**changes})


def test_completion_is_explicit_and_cancelled_unstarted_phase_needs_zero_remainder():
    complete={'phase_id':'a','status':'completed','cost_to_date':'0','effort_hours_to_date':'0','remaining_cost_estimate':'0','remaining_effort_hours_estimate':'0','started_on':'2026-01-01','finished_on':'2026-01-02','completion_self_report_confirmed':True}
    assert PhaseReport.model_validate(complete).cost_to_date=='0.00'
    for change in ({'completion_self_report_confirmed':False},{'remaining_cost_estimate':'1'},{'finished_on':'2025-01-01'},{'started_on':None}):
        with pytest.raises(ValidationError):PhaseReport.model_validate({**complete,**change})
    cancelled={**complete,'status':'cancelled','started_on':None};assert PhaseReport.model_validate(cancelled).started_on is None


def test_budget_currency_stale_reference_and_role_scope_are_currently_enforced(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id']
    for principal in (person('bob'),person('reader'),person(org='foreign'),person(scopes=frozenset({'org:process:read'}))):
        with pytest.raises(Error):plans.save_baseline(principal,wid,SaveBaseline(baseline=baseline,expected_access_revision=2,user_confirmed=True,organization_sharing_confirmed=True))
    bad=baseline.model_copy(deep=True);bad.currency='EUR'
    with pytest.raises(Error):baseline_save(plans,r,bad)
    bad=baseline.model_copy(deep=True);bad.opportunity.content_sha256='0'*64
    with pytest.raises(Error):baseline_save(plans,r,bad)
    baseline.phases[0].cost='2000.00';baseline_save(plans,r,baseline)
    assert 'baseline_cost_exceeds_budget_context' in plans.detail(person(),wid)['source']['blockers']
    with pytest.raises(Error):freeze(plans,r)
    with pytest.raises(Error):plans.detail(person(org='foreign'),wid)
    with pytest.raises(Error):plans.detail(person(scopes=frozenset()),wid)


def test_root_owner_can_erase_archived_plan_and_source_deletion_erases_plan_atomically(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id'];baseline_save(plans,r,baseline)
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=2,archived=True,user_confirmed=True))
    with pytest.raises(Error):reports(plans,r,[{'phase_id':'analysis','cost_to_date':'0'}])
    plans.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=3,user_confirmed=True))
    assert plans.detail(person(),wid)['artifact'] is None
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=3,archived=False,user_confirmed=True))
    plans.save_baseline(person(),wid,SaveBaseline(baseline=baseline,expected_access_revision=4,user_confirmed=True,organization_sharing_confirmed=True))
    store.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=4,user_confirmed=True))
    with store.db.connect() as c:assert plans.latest(c,person(),wid)==0


def test_access_cas_revocation_and_concurrent_reporting_do_not_lose_updates(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id'];baseline_save(plans,r,baseline);freeze(plans,r)
    view=plans.detail(person(),wid);barrier=Barrier(2)
    def update(phase):
        barrier.wait(timeout=10)
        try:plans.save_reports(person('ed'),wid,SaveReports(expected_revision=2,expected_access_revision=2,expected_baseline_sha256=view['artifact']['baseline_sha256'],reports=[{'phase_id':phase,'cost_to_date':'1'}],user_confirmed=True,organization_sharing_confirmed=True));return 'saved'
        except Error:return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(update,'analysis'),pool.submit(update,'build')];assert sorted(f.result(timeout=30) for f in futures)==['refused','saved']
    grant(store,wid,members=[{'actor_id':'bob','role':'reviewer'}],expected=2)
    with pytest.raises(Error):reports(plans,r,[{'phase_id':'analysis','cost_to_date':'2'}],who='ed')
    assert len(plans.detail(person(),wid)['artifact']['reports'])==1


def test_fifty_revision_bound_does_not_prune_and_audit_failures_roll_back(store,monkeypatch):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id'];original=store._audit
    def failure(*args,**kwargs):raise RuntimeError('Audit unavailable')
    monkeypatch.setattr(store,'_audit',failure)
    with pytest.raises(RuntimeError):baseline_save(plans,r,baseline)
    with store.db.connect() as c:assert plans.latest(c,person(),wid)==0
    monkeypatch.setattr(store,'_audit',original);baseline_save(plans,r,baseline);first=plans.detail(person(),wid)['artifact']
    for i in range(49):reports(plans,r,[{'phase_id':'analysis','cost_to_date':str(i)}])
    with pytest.raises(Error):reports(plans,r,[{'phase_id':'analysis','cost_to_date':'50'}])
    with pytest.raises(Error):baseline_save(plans,r,baseline,revision=50)
    assert plans.detail(person(),wid,1)['artifact']==first
    monkeypatch.setattr(store.db,'audit',failure)
    with pytest.raises(RuntimeError):plans.detail(person(),wid,exporting=True)


def test_corrupt_plan_withholds_values_and_owner_erases_it_without_source_changes(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id'];baseline_save(plans,r,baseline)
    with store.write_transaction() as c:plans.q(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',('{"PRIVATE_CORRUPTION":true}',namespace('acme'),wid))
    result=plans.detail(person(),wid);assert not result['integrity_ok'] and result['artifact'] is None and result['totals'] is None
    with pytest.raises(Error):plans.detail(person(),wid,exporting=True)
    plans.delete(person(),wid,Remove(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    assert plans.detail(person(),wid)['artifact'] is None and store.detail(person(),wid)['process']==r


def test_report_unknown_phase_baseline_digest_and_new_source_context_are_not_accepted(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id'];baseline_save(plans,r,baseline)
    with pytest.raises(Error):reports(plans,r,[{'phase_id':'missing','cost_to_date':'1'}])
    with pytest.raises(Error):reports(plans,r,[{'phase_id':'analysis','cost_to_date':'1'}],expected_baseline_sha256='0'*64)
    view=plans.detail(person(),wid);before=Freeze(expected_revision=1,expected_access_revision=2,expected_baseline_sha256=view['artifact']['baseline_sha256'],expected_approved_opportunity=view['source']['opportunity_reference'],user_confirmed=True)
    decide(opps,store,r,'implemented',who='alice',implementation_self_report_confirmed=True)
    with pytest.raises(Error,match='changed'):plans.freeze(person('bob'),wid,before)
    freeze(plans,r)
    assert plans.detail(person(),wid)['current_reviewed_baseline']


def test_completion_report_erasure_and_source_loss_remain_explicit_unverified_data(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id'];baseline_save(plans,r,baseline);freeze(plans,r)
    rows=[{'phase_id':'analysis','status':'completed','cost_to_date':'1000','effort_hours_to_date':'20','remaining_cost_estimate':'0','remaining_effort_hours_estimate':'0','started_on':'2026-09-01','finished_on':'2026-09-02','completion_self_report_confirmed':True}]
    reports(plans,r,rows);saved=deepcopy(plans.detail(person(),wid)['artifact'])
    from shared.process_opportunity_review import Remove as OpportunityRemove
    opps.delete(person(),wid,OpportunityRemove(expected_revision=2,expected_access_revision=2,user_confirmed=True))
    after=plans.detail(person(),wid);assert not after['current_reviewed_baseline'] and after['effective_state']=='needs_reassessment' and after['artifact']==saved
    reports(plans,r,[{'phase_id':'analysis','cost_to_date':'1100'}])
    assert not plans.detail(person(),wid)['actual_values_independently_verified']


def test_human_routes_preflight_parser_and_exact_history_export(tmp_path):
    from test_process_collaboration import write
    from test_shared_opportunity_review import scenario
    with TestClient(app(tmp_path)) as client:
        headers={'Authorization':'Bearer alice'};value=write('Phased process').model_dump();value['process_model']['opportunity_scenario']=scenario()
        r=client.post('/v1/human/processes',headers=headers,json=value).json()['process'];wid=r['workflow_id'];base='/v1/human/processes/'+wid
        client.post(base+'/access',headers=headers,json={'expected_access_revision':1,'members':[{'actor_id':'bob','role':'reviewer'}],'user_confirmed':True})
        client.post(base+'/reviews',headers={'Authorization':'Bearer bob'},json={'revision':1,'content_sha256':r['content_sha256'],'decision':'reviewed_design','user_confirmed':True})
        client.post(base+'/opportunity',headers=headers,json={'expected_access_revision':2,'data':data(r).model_dump(mode='json'),'user_confirmed':True,'organization_sharing_confirmed':True})
        origin=client.get(base+'/opportunity',headers=headers).json()['artifact'];b=Baseline(title='Phase plan',currency='USD',opportunity=pin(origin),phases=[{'id':'one','name':'Implementation','cost':'100','effort_hours':'10','starts_on':'2026-01-01','finishes_on':'2026-01-02'}])
        assert client.post(base+'/implementation-plan',headers={'Authorization':'Bearer reader'},content='not json').status_code==403
        assert client.post(base+'/implementation-plan',headers={**headers,'Content-Type':'application/json'},content='{"baseline":{},"baseline":{}}').status_code==422
        saved=client.post(base+'/implementation-plan',headers=headers,json=SaveBaseline(baseline=b,expected_access_revision=2,user_confirmed=True,organization_sharing_confirmed=True).model_dump(mode='json'));assert saved.status_code==200,saved.text
        exported=client.get(base+'/implementation-plan/export?revision=1',headers=headers);assert exported.status_code==200
        assert exported.json()['artifact']['baseline']['opportunity']==pin(origin) and not exported.json()['execution_authorized']
        script=client.get('/process-implementation-client.js');assert script.status_code==200 and script.headers['cache-control']=='no-store'


def test_twelve_phase_forecasts_against_independent_integer_cent_oracle_for_every_coverage_mask():
    """All 4096 missing/known phase combinations, independent integer arithmetic."""
    phases=[{'id':f'p{i}','name':f'Phase {i}','cost':f'{i+1}.01','effort_hours':f'{i+2}.02',
        'starts_on':'2026-01-01','finishes_on':'2026-01-31'} for i in range(12)]
    baseline=Baseline(title='Twelve phases',currency='SEK',opportunity={'workflow_id':'root','revision':1,'content_sha256':'a'*64},phases=phases).model_dump(mode='json')
    values=[PhaseReport(phase_id=f'p{i}',cost_to_date=f'{i+3}.03',effort_hours_to_date=f'{i+4}.04',remaining_cost_estimate=f'{i+5}.05',remaining_effort_hours_estimate=f'{i+6}.06').model_dump(mode='json') for i in range(12)]
    cents=lambda value:int(value.replace('.',''))
    text=lambda value:f'{value//100}.{value%100:02d}'
    for mask in range(1<<12):
        rows=[v for i,v in enumerate(values) if mask&(1<<i)];result=totals(baseline,rows)
        known=mask.bit_count()
        assert result['baseline_cost']==text(sum(cents(p['cost']) for p in phases))
        assert result['baseline_calendar_span_days']==31 and result['phase_count']==12
        for key in ('cost_to_date','effort_hours_to_date','remaining_cost_estimate','remaining_effort_hours_estimate'):
            assert result['coverage'][key]=={'known_phases':known,'unknown_phases':12-known}
            assert result[key]==(text(sum(cents(v[key]) for v in values)) if known==12 else None)
        for suffix,actual,remaining,base in [('cost','cost_to_date','remaining_cost_estimate','cost'),('effort_hours','effort_hours_to_date','remaining_effort_hours_estimate','effort_hours')]:
            total=sum(cents(v[actual])+cents(v[remaining]) for v in values)
            assert result['new_total_'+suffix+'_estimate']==(text(total) if known==12 else None)
            assert result['variance_'+suffix+'_from_baseline']==(text(total-sum(cents(p[base]) for p in phases)) if known==12 else None)


def test_twelve_phase_maximum_amounts_roundtrip_without_changing_source_payloads(store):
    plans=ImplementationPlans(store);r,opps,baseline=setup(store);wid=r['workflow_id']
    baseline.phases=[type(baseline.phases[0]).model_validate({'id':f'p{i}','name':f'Phase {i}','cost':'1000000000','effort_hours':'1000000','starts_on':'2026-01-01','finishes_on':'2026-01-31'}) for i in range(12)]
    with store.db.connect() as c:
        original=plans.q(c,'SELECT owner,workflow_id,revision,payload FROM workflow_knowledge ORDER BY owner,workflow_id,revision').fetchall()
    baseline_save(plans,r,baseline)
    with pytest.raises(Error):freeze(plans,r)  # Deliberately exceeds budget context.
    first=plans.detail(person(),wid)['artifact']
    reports(plans,r,[{'phase_id':f'p{i}','cost_to_date':'1000000000','effort_hours_to_date':'1000000','remaining_cost_estimate':'1000000000','remaining_effort_hours_estimate':'1000000'} for i in range(12)])
    result=plans.detail(person(),wid);assert result['totals']['baseline_cost']=='12000000000.00'
    assert result['totals']['new_total_cost_estimate']=='24000000000.00' and result['totals']['new_total_effort_hours_estimate']=='24000000.00'
    assert plans.detail(person(),wid,1,exporting=True)['artifact']==first and not result['spending_authorized']
    with store.db.connect() as c:
        current=plans.q(c,'SELECT owner,workflow_id,revision,payload FROM workflow_knowledge WHERE owner<>? ORDER BY owner,workflow_id,revision',(namespace('acme'),)).fetchall()
    assert [tuple(row) for row in current]==[tuple(row) for row in original]
    too_many=baseline.model_dump(mode='json');too_many['phases'].append({**too_many['phases'][0],'id':'extra'})
    with pytest.raises(ValidationError):Baseline.model_validate(too_many)
