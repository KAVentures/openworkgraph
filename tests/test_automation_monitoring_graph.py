"""Imported SDK activity reports retain graph semantics and unknown coverage."""
from copy import deepcopy
from itertools import combinations

import pytest

from server import automation_monitoring as am
from shared.process_agent_project import build_runtime_spec
from test_automation_monitoring import _report, _run
from test_parallel_process_model import parallel_model


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(am, 'DATA_DIR', tmp_path)


def spec():
    model=parallel_model().model_dump()
    for node in model['activities']:
        if node['kind']=='activity':node['label']='Same label'
    return build_runtime_spec({'format':'openworkgraph.versioned-process-export.v1', 'workflow_id':'synthetic_graph',
        'revision':2, 'content_sha256':'a'*64, 'process_model':model, 'execution_authorization':False})


def run(i, path):
    value=_run(i,'succeeded',1)
    value['steps']=[{'step_id':node,'activity':'Same label','status':'completed','at':value['started_at']} for node in path]
    return value


def attach(s, runs):
    dep=am.import_report(_report(runs, runtime='openai-agents', workflow_spec_sha256=am.spec_digest(s)))['deployment']['deployment_id']
    am.attach_design(dep,s)
    return dep


def test_all_parallel_interleavings_match_ids_without_flattening_and_missing_branch_costs_edits():
    s=spec();runs=[]
    for pos in combinations(range(4),2):
        a=iter(('A1','A2'));b=iter(('B1','B2'))
        runs.append(run(len(runs),[next(a) if p in pos else next(b) for p in range(4)]+['close']))
    runs += [run(10,['A1','A2','close']),run(11,['A2','A1','B1','B2','close']),run(12,[])]
    dep=attach(s,runs);result=am.monitor(dep)['design_conformance']
    assert result['kind']=='runtime_graph' and result['runs_exact_structural_match']==6
    assert result['runs_compared']==8 and result['runs_not_compared']==1
    assert result['not_compared_by_reason']['no_completed_steps']==1
    assert result['mean_minimum_activity_edits_among_compared_runs']>0
    assert result['activity_ids_never_reported_completed']==[]
    assert result['unexpected_step_ids']==[]
    assert result['process_reference']=={'workflow_id':'synthetic_graph','revision':2,'content_sha256':'a'*64}
    for key in ('business_rules_evaluated','policy_compliance_verified','business_outcome_verified','current_catalog_review_verified'):
        assert result[key] is False
    assert am.list_deployments()['deployments'][0]['design_graph']['process_model']==s['process_model']


def test_unobserved_human_activity_is_not_invented_or_treated_as_verified_deviation():
    s=spec();s['process_model']['activities'][1]['implementation']='human'
    from shared.process_agent_project import process_model_digest
    s['process_model_sha256']=process_model_digest(s['process_model'])
    human=s['process_model']['activities'][1]['id']
    dep=attach(s,[run(1,[x for x in ['A1','A2','B1','B2','close'] if x!=human])])
    result=am.monitor(dep)['design_conformance']
    assert result['runs_exact_structural_match']==0
    assert result['human_activity_ids_never_reported_completed']==[human]
    assert result['missing_activity_is_missing_reported_evidence_not_proven_process_deviation'] is True


def test_unknown_steps_failed_attempts_and_gateway_ids_do_not_become_successful_activities():
    s=spec();value=run(1,['A1','A2','B1','B2','close','fork','unexpected'])
    value['steps'][1]['status']='failed'
    result=am.monitor(attach(s,[value]))['design_conformance']
    assert result['runs_exact_structural_match']==0
    assert result['activity_ids_never_reported_completed']==['A2']
    assert result['unexpected_step_ids']==[{'step_id':'fork','runs':1},{'step_id':'unexpected','runs':1}]


def test_distinct_path_and_parallel_state_budgets_leave_explicit_unknown_runs(monkeypatch):
    s=spec();monkeypatch.setattr(am,'MAX_GRAPH_PATHS',1)
    dep=attach(s,[run(1,['A1','A2','B1','B2','close']),run(2,['A1','A2','B1','B2','close']),run(3,['A1','close'])])
    result=am.monitor(dep)['design_conformance']
    assert result['runs_compared']==2 and result['runs_exact_structural_match']==2
    assert result['not_compared_by_reason']['distinct_path_limit']==1
    from shared import process_parallel
    monkeypatch.setattr(process_parallel,'MAX_BATCH_ALIGNMENT_STATES',0)
    result=am.monitor(dep)['design_conformance']
    assert result['runs_compared']==0 and result['runs_exact_structural_match']==0
    assert result['mean_minimum_activity_edits_among_compared_runs'] is None
    assert result['not_compared_by_reason']=={'no_completed_steps':0,'distinct_path_limit':1,'alignment_state_limit':2}


@pytest.mark.parametrize('mutation',['hash','model','contract','permission','tools','revision','reference'])
def test_modified_or_authorizing_runtime_specs_are_refused_without_attaching(mutation):
    s=spec();bad=deepcopy(s)
    if mutation=='hash':bad['process_model_sha256']='f'*64
    if mutation=='model':bad['process_model']['entry_activity_id']='A2'
    if mutation=='contract':bad.pop('control_flow_contract')
    if mutation=='permission':bad['execution_authorization']=True
    if mutation=='tools':bad['live_business_tools']=['execute']
    if mutation=='revision':bad['revision']=True
    if mutation=='reference':bad['workflow_id']='private/reference'
    dep=am.import_report(_report([run(1,[])]))['deployment']['deployment_id']
    with pytest.raises(am.MonitoringError):am.attach_design(dep,bad)
    assert not am.list_deployments()['deployments'][0]['design_attached']


def test_runtime_spec_pin_applies_in_both_orders_and_new_migration_is_idempotent():
    s=spec();dep=attach(s,[run(1,['A1'])]);different=deepcopy(s);different['revision']=3
    with pytest.raises(am.MonitoringError,match='does not match'):am.attach_design(dep,different)
    with pytest.raises(am.MonitoringError,match='different workflow spec'):
        am.import_report(_report([run(2,['A1'])],workflow_spec_sha256=am.spec_digest(different)),dep)
    for _ in range(2):
        conn=am._connect();conn.close()
    assert am.monitor(dep)['design_conformance']['process_reference']['revision']==2


def test_exclusive_branches_and_loops_are_compared_without_inventing_rule_compliance():
    from shared.process_model import ProcessModel
    model=ProcessModel(goal='Synthetic review loop',baseline_source='user_declared',baseline_path=['Receive','Review'],
        activities=[{'id':'receive','label':'Receive'}, {'id':'choose','label':'Choose','kind':'decision'},
                    {'id':'review','label':'Review'}, {'id':'done','label':'Done','kind':'end'}],
        transitions=[{'source':'receive','target':'choose'},
                     {'source':'choose','target':'review','rule_id':'needs_review'},
                     {'source':'review','target':'choose'}, {'source':'choose','target':'done','rule_id':'complete'}],
        entry_activity_id='receive',terminal_activity_ids=['done'],
        business_rules=[{'id':'needs_review','text':'Unconfirmed rule','user_confirmed':False},
                        {'id':'complete','text':'Completion rule','user_confirmed':True}])
    s=build_runtime_spec({'format':'openworkgraph.versioned-process-export.v1', 'workflow_id':'loop', 'revision':1,
        'content_sha256':'b'*64, 'process_model':model.model_dump(), 'execution_authorization':False})
    dep=attach(s,[run(1,['receive']),run(2,['receive','review','review']),run(3,['review','receive'])])
    result=am.monitor(dep)['design_conformance']
    assert result['runs_exact_structural_match']==2 and result['runs_compared']==3
    assert result['business_rules_evaluated'] is False and result['policy_compliance_verified'] is False
