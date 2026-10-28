"""Ordered priority rules, conservative unknown prefixes and default UNIQUE safety."""
from copy import deepcopy
import itertools
import json
import pytest
from pydantic import ValidationError
from shared.process_model import ProcessModel
from shared.process_decision_tables import evaluate_table
from shared.process_agent_project import process_model_digest


def graph(count=3,policy='FIRST'):
    return {'goal':'Choose one reviewed route','baseline_source':'user_declared',
        'baseline_coverage':'user_declared_design','baseline_path':['Choose','Result 0'],
        'activities':[{'id':'choose','label':'Choose','kind':'decision'}]+
            [{'id':'target_'+str(i),'label':'Result '+str(i)} for i in range(count)],
        'transitions':[{'source':'choose','target':'target_'+str(i),'rule_id':'rule_'+str(i)} for i in range(count)],
        'entry_activity_id':'choose','terminal_activity_ids':['target_'+str(i) for i in range(count)],
        'business_rules':[{'id':'rule_'+str(i),'text':'Declared rule '+str(i),'user_confirmed':True,
            'predicate':{'conditions':[{'path':'/f'+str(i),'operator':'eq','expected':True}]}} for i in range(count)],
        'decision_tables':[{'id':'priority','decision_activity_id':'choose','hit_policy':policy,
            'effective_from':'2026-01-01','effective_until':'2027-01-01',
            'source_basis':{'label':'Human-declared policy','document_sha256':'a'*64},
            'rows':[{'id':'row_'+str(i),'rule_id':'rule_'+str(i),'target_activity_id':'target_'+str(i)} for i in range(count)],
            'user_confirmed':True}]}


def checked(states,model=None,date='2026-10-27'):
    model=model or ProcessModel.model_validate(graph(len(states)))
    facts={'f'+str(i):s=='M' for i,s in enumerate(states) if s!='U'}
    return evaluate_table(model.decision_tables[0],{r.id:r for r in model.business_rules},facts,date)


def oracle(states,order=None):
    order=range(len(states)) if order is None else order
    candidates=[i for i in order if states[i]!='N']
    if not candidates:return 'gap',None,None
    first=candidates[0]
    return ('first_match',first,None) if states[first]=='M' else ('unknown',None,first)


@pytest.mark.parametrize('states',list(itertools.product('MNU',repeat=3)))
def test_independent_three_state_first_prefix_oracle(states):
    result=checked(states);status,selected,blocked=oracle(states)
    assert result['hit_policy']=='FIRST' and result['status']==status
    assert result['selected_target_activity_id']==('target_'+str(selected) if selected is not None else None)
    assert result['selected_row_id']==('row_'+str(selected) if selected is not None else None)
    assert result['blocking_row_id']==('row_'+str(blocked) if blocked is not None else None)
    assert result['execution_authorized'] is False and result['facts_authoritativeness_verified'] is False


def test_exhaustive_eight_row_truth_state_oracle_and_no_fact_retention():
    model=ProcessModel.model_validate(graph(8));rules={r.id:r for r in model.business_rules}
    total=0
    for states in itertools.product('MNU',repeat=8):
        facts={'f'+str(i):s=='M' for i,s in enumerate(states) if s!='U'}
        facts['PRIVATE_UNUSED']='PRIVATE_UNSELECTED_VALUE'
        result=evaluate_table(model.decision_tables[0],rules,facts,'2026-10-27')
        status,selected,blocked=oracle(states)
        assert result['status']==status
        assert result['selected_row_id']==('row_'+str(selected) if selected is not None else None)
        assert result['blocking_row_id']==('row_'+str(blocked) if blocked is not None else None)
        assert 'PRIVATE' not in json.dumps(result)
        total+=1
    assert total==6561


def test_reorder_changes_explicit_precedence_and_exact_model_digest():
    before=ProcessModel.model_validate(graph());value=graph();value['decision_tables'][0]['rows'].reverse()
    after=ProcessModel.model_validate(value)
    assert checked(['M','M','U'],before)['selected_target_activity_id']=='target_0'
    assert checked(['M','M','U'],after)['status']=='unknown'
    assert checked(['M','M','N'],after)['selected_target_activity_id']=='target_1'
    assert process_model_digest(before.model_dump())!=process_model_digest(after.model_dump())


@pytest.mark.parametrize('date,status',[(None,'date_required'),('2025-12-31','not_yet_effective'),
    ('2026-01-01','first_match'),('2026-12-31','first_match'),('2027-01-01','expired')])
def test_first_keeps_explicit_dates_and_exclusive_expiry(date,status):
    result=checked(['M','M','M'],date=date)
    assert result['status']==status
    if status!='first_match':assert result['selected_row_id'] is None and result['rows']==[]


def test_unconfirmed_table_or_competing_rule_never_selects_a_first_route():
    for field in ['table','rule']:
        value=graph()
        if field=='table':value['decision_tables'][0]['user_confirmed']=False
        else:
            value['business_rules'][2]['user_confirmed']=False
            value['decision_tables'][0]['user_confirmed']=False
        result=checked(['M','M','M'],ProcessModel.model_validate(value))
        assert result['status']=='unconfirmed' and result['selected_target_activity_id'] is None


def test_default_unique_still_refuses_overlap_and_unknown_competitors():
    value=graph();value['decision_tables'][0].pop('hit_policy');model=ProcessModel.model_validate(value)
    assert model.decision_tables[0].hit_policy=='UNIQUE'
    assert checked(['M','M','N'],model)['status']=='overlap'
    result=checked(['M','U','N'],model)
    assert result['status']=='unknown' and result['selected_target_activity_id'] is None
    assert 'selected_row_id' not in result and 'blocking_row_id' not in result


@pytest.mark.parametrize('policy',['ANY','COLLECT','PRIORITY','first','First',None,True])
def test_unsupported_policies_are_not_silently_interpreted_as_first(policy):
    with pytest.raises(ValidationError):ProcessModel.model_validate(graph(policy=policy))


def test_first_bpmn_roundtrip_preserves_policy_and_order_as_unconfirmed_declarations():
    from shared.process_bpmn_import import import_bpmn
    from shared.process_catalog import process_bpmn,process_document
    from test_process_decision_tables import record
    value=graph();value['decision_tables'][0]['rows'].reverse();packet=record(value)
    imported=import_bpmn(process_bpmn(packet).encode())['process_model']
    table=imported['decision_tables'][0]
    assert table['hit_policy']=='FIRST' and [r['id'] for r in table['rows']]==['row_2','row_1','row_0']
    assert table['user_confirmed'] is False
    assert 'FIRST uses declared row order' in process_document(packet)


def test_first_case_outside_a_valid_proposed_route_never_leaves_a_selected_row():
    from shared.process_rule_checks import RuleCaseBatch,check_case_rules
    from test_process_decision_tables import record
    value=ProcessModel.model_validate(graph())
    cases=RuleCaseBatch.model_validate({'cases':[{'case_id':'case_1','facts':{'f0':True},'activity_ids':['target_0']}]})
    result=check_case_rules(record(value.model_dump()),cases.cases,'2026-10-27')
    table=result['cases'][0]['decision_tables'][0]
    assert table['status']=='not_evaluated_invalid_route' and table['selected_target_activity_id'] is None
    assert table['selected_row_id'] is None and table['blocking_row_id'] is None


# Exercise FIRST through the same durable review/CAS and ephemeral HTTP paths
# as UNIQUE, rather than granting a parallel policy-specific approval path.
from test_process_collaboration import store, person, app, review, grant
from gateway.process_collaboration import SharedProcessWrite, ProcessCollaborationError
from shared.process_rule_checks import RuleCaseBatch
from fastapi.testclient import TestClient


@pytest.mark.parametrize('change',['policy','order'])
def test_first_changes_require_new_exact_revision_review_and_current_role(store,change):
    value=graph();value['decision_tables'][0]['hit_policy']='UNIQUE'
    original=store.save(person(),SharedProcessWrite(title='Ordered process',process_model=value,
        user_confirmed=True,organization_sharing_confirmed=True))['process']
    wid=original['workflow_id'];grant(store,wid,members=[{'actor_id':'bob','role':'reviewer'}])
    store.review(person('bob'),wid,review(original))
    if change=='policy':value['decision_tables'][0]['hit_policy']='FIRST'
    else:
        # Order is hashed even before FIRST is selected, so a later promotion
        # cannot inherit review of an unseen precedence.
        value['decision_tables'][0]['rows'].reverse()
    saved=store.save(person(),SharedProcessWrite(title='Ordered process',process_model=value,
        expected_revision=1,user_confirmed=True,organization_sharing_confirmed=True),wid)
    assert saved['review']['status']=='needs_review'
    assert saved['process']['content_sha256']!=original['content_sha256']
    assert store.detail(person(),wid,revision=1)['process']==original
    with pytest.raises(ProcessCollaborationError):store.review(person('bob'),wid,review(original))
    with pytest.raises(ValueError,match='Revision changed'):
        store.save(person(),SharedProcessWrite(title='Stale writer',process_model=value,
            expected_revision=1,user_confirmed=True,organization_sharing_confirmed=True),wid)
    grant(store,wid,expected=2)
    with pytest.raises(ProcessCollaborationError):store.review(person('bob'),wid,review(saved['process']))
    assert store.detail(person(),wid)['review']['status']=='needs_review'


def test_first_shared_http_consent_isolation_minimized_reports_and_disabled_project(tmp_path):
    import io
    import zipfile
    application=app(tmp_path)
    with TestClient(application) as client:
        headers={'Authorization':'Bearer alice'}
        response=client.post('/v1/human/processes',headers=headers,json={'title':'Ordered process',
            'process_model':graph(),'user_confirmed':True,'organization_sharing_confirmed':True})
        assert response.status_code==200,response.text
        saved=response.json()['process'];base='/v1/human/processes/'+saved['workflow_id']
        body={'as_of_date':'2026-10-28','cases':[
            {'case_id':'case_1','facts':{'f0':True,'f1':True,'PRIVATE':'PRIVATE_FACT'}},
            {'case_id':'case_2','facts':{'f1':True}},
            {'case_id':'case_3','facts':{'f0':False,'f1':True}},
            {'case_id':'case_4','facts':{'f0':False,'f1':False,'f2':False}}]}
        assert client.post(base+'/check-rules?revision=1',headers=headers,json=body).status_code==403
        allowed={**headers,'X-OWG-Case-Facts-Authorized':'true'}
        result=client.post(base+'/check-rules?revision=1',headers=allowed,json=body)
        assert result.status_code==200,result.text
        report=result.json();tables=[c['decision_tables'][0] for c in report['cases']]
        assert [t['status'] for t in tables]==['first_match','unknown','first_match','gap']
        assert tables[0]['selected_row_id']=='row_0' and tables[1]['blocking_row_id']=='row_0'
        assert tables[2]['selected_row_id']=='row_1'
        assert report['facts_retained'] is False and 'PRIVATE' not in result.text and '"f0":' not in result.text
        assert client.post(base+'/check-rules?revision=1',headers={**allowed,'Authorization':'Bearer foreign'},json=body).status_code in {403,404}
        assert client.get(base+'/export?revision=1&format=agent-project',headers=headers).status_code==409
        granted=client.post(base+'/access',headers=headers,json={'expected_access_revision':1,
            'members':[{'actor_id':'bob','role':'reviewer'}],'user_confirmed':True})
        assert granted.status_code==200,granted.text
        reviewed=client.post(base+'/reviews',headers={'Authorization':'Bearer bob'},json=review(saved).model_dump())
        assert reviewed.status_code==200,reviewed.text
        exported=client.get(base+'/export?revision=1&format=agent-project',headers=headers)
        assert exported.status_code==200,exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            spec=json.loads(archive.read('workflow.spec.json'))
            bindings=json.loads(archive.read('tool-bindings.example.json'))
            assert spec['decision_table_contract']=='openworkgraph.current-source-ordered-decision.v1'
            assert bindings['enabled'] is False
            table=bindings['decision_table_checks'][0]
            assert table['mode']=='first_route_before_each_action'
            assert table['row_precedence_reviewed'] is False and table['record_semantics_reviewed'] is False
        assert client.get(base,headers=headers).json()['process']==saved


def test_largest_reachable_fanout_preserves_last_row_and_prefix_unknown_guard():
    # Forty activities permit one decision and 39 distinct targets. The table's
    # standalone bound remains 40; graph limits are not relaxed for FIRST.
    model=ProcessModel.model_validate(graph(39))
    states=['N']*38+['M'];result=checked(states,model)
    assert result['selected_row_id']=='row_38'
    states[0]='U';result=checked(states,model)
    assert result['status']=='unknown' and result['blocking_row_id']=='row_0'
    with pytest.raises(ValidationError):ProcessModel.model_validate(graph(40))
