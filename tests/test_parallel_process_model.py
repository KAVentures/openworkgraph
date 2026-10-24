"""Fork/join conformance must preserve every branch, not choose one path."""
from itertools import combinations

import pytest
from pydantic import ValidationError
from shared.process_model import ProcessModel
from shared.process_conformance import compare_path
from shared.process_parallel import parallel_regions


def parallel_model():
    return ProcessModel(goal='Review two independent checks',baseline_source='user_declared',
        baseline_coverage='user_declared_design',baseline_path=['User declared parallel design'],
        activities=[{'id':'fork','label':'Run checks','kind':'parallel_split'},
            *[{'id':identifier,'label':identifier} for identifier in ('A1','A2','B1','B2')],
            {'id':'join','label':'All checks','kind':'parallel_join'}, {'id':'close','label':'Close'}],
        transitions=[{'source':a,'target':b} for a,b in [('fork','A1'),('fork','B1'),('A1','A2'),
            ('A2','join'),('B1','B2'),('B2','join'),('join','close')]],
        entry_activity_id='fork',terminal_activity_ids=['close'])


def test_every_valid_interleaving_preserves_branch_order_and_consumes_all_join_arrivals():
    model=parallel_model()
    assert model.linear_path() is None
    assert parallel_regions(model)==[{'split':'fork','join':'join','branches':[['A1','A2'],['B1','B2']]}]
    for positions in combinations(range(4),2):
        a=iter(('A1','A2'));b=iter(('B1','B2'))
        path=tuple(next(a) if index in positions else next(b) for index in range(4))+('Close',)
        result=compare_path(path,model)
        assert result['comparison_complete'] and result['exact_structural_match'],path
        assert result['minimum_activity_edit_count']==0 and not result['business_rules_evaluated']
        assert [row['activity'] for row in result['alignment']]==list(path)
        assert {row['model_activity_id'] for row in result['alignment']}=={'A1','A2','B1','B2','close'}


@pytest.mark.parametrize(('path','edits'),[
    (('A1','A2','Close'),2), (('A1','B2','A2','B1','Close'),2),
    (('A1','A2','Close','B1','B2'),2), (('A1','B1','A2','B2','Extra','Close'),1),
    (('A1',),4), ((),5)])
def test_missing_out_of_order_extra_or_incomplete_events_are_not_silent_exclusive_routes(path,edits):
    result=compare_path(path,parallel_model())
    assert result['comparison_complete'] and result['exact_structural_match'] is False
    assert result['minimum_activity_edit_count']==edits
    if path in ((),('A1',)):assert result['could_be_incomplete_log_prefix']


def test_repeated_labels_remain_separate_branch_tasks_and_empty_branch_is_not_a_lost_join():
    data=parallel_model().model_dump()
    data['activities']=[node for node in data['activities'] if node['id'] not in {'A2','B2'}]
    for node in data['activities']:
        if node['id'] in {'A1','B1'}:node['label']='Check'
    data['transitions']=[{'source':a,'target':b} for a,b in [('fork','A1'),('fork','B1'),('A1','join'),('B1','join'),('join','close')]]
    model=ProcessModel.model_validate(data)
    assert compare_path(('Check','Check','Close'),model)['exact_structural_match']
    assert compare_path(('Check','Close'),model)['minimum_activity_edit_count']==1
    assert compare_path(('Check','Check','Check','Close'),model)['minimum_activity_edit_count']==1
    data['activities']=[node for node in data['activities'] if node['id']!='B1']
    data['transitions']=[edge for edge in data['transitions'] if 'B1' not in edge.values()]+[{'source':'fork','target':'join'}]
    assert compare_path(('Check','Close'),ProcessModel.model_validate(data))['exact_structural_match']


def test_invalid_or_unsupported_parallel_topology_is_rejected_instead_of_flattened():
    changes=[]
    shared=parallel_model().model_dump();shared['transitions'].append({'source':'B2','target':'A2'})
    changes.append(shared)
    escape=parallel_model().model_dump();escape['transitions']=[edge for edge in escape['transitions'] if edge['source']!='B2']
    escape['transitions'].append({'source':'B2','target':'close'});changes.append(escape)
    conditional=parallel_model().model_dump();conditional['business_rules']=[{'id':'condition','text':'Choose one','user_confirmed':True}]
    conditional['transitions'][0]['rule_id']='condition';changes.append(conditional)
    conditional_branch=parallel_model().model_dump();conditional_branch['business_rules']=conditional['business_rules']
    conditional_branch['transitions'][2]['rule_id']='condition';changes.append(conditional_branch)
    orphan=parallel_model().model_dump();orphan['activities'][0]['kind']='decision'
    orphan['business_rules']=[{'id':'condition','text':'Choose one','user_confirmed':True}]
    for edge in orphan['transitions']:
        if edge['source']=='fork':edge['rule_id']='condition'
    changes.append(orphan)
    nested=parallel_model().model_dump();nested['activities'][1]['kind']='parallel_split';changes.append(nested)
    for data in changes:
        with pytest.raises(ValidationError):ProcessModel.model_validate(data)


def test_budget_exhaustion_is_unknown_and_never_a_match_or_deviation(monkeypatch):
    monkeypatch.setattr('shared.process_parallel.MAX_ALIGNMENT_STATES',2)
    result=compare_path(('A1','B1','A2','B2','Close'),parallel_model())
    assert result['comparison_complete'] is False
    assert result['exact_structural_match'] is None and result['minimum_activity_edit_count'] is None
    assert result['could_be_incomplete_log_prefix'] is None and result['alignment']==[]
    assert result['comparison_reason']=='alignment_state_budget_reached'


def test_bpmn_round_trip_preserves_parallel_gateways_geometry_and_unconditional_edges():
    from xml.etree import ElementTree as ET
    from shared.process_catalog import process_bpmn,process_document
    from shared.process_bpmn_import import import_bpmn,BPMN,BPMNImportError
    from shared.process_agent_project import build_runtime_project,synthetic_routes
    model=parallel_model()
    packet={'format':'openworkgraph.versioned-process-export.v1','workflow_id':'parallel','revision':1,
        'content_sha256':'a'*64,'title':'Parallel checks','execution_authorization':False,'process_model':model.model_dump()}
    xml=process_bpmn(packet);root=ET.fromstring(xml)
    gateways=root.findall('.//{'+BPMN+'}parallelGateway')
    assert [node.get('gatewayDirection') for node in gateways]==['Diverging','Converging']
    assert len(root.findall('.//{http://www.omg.org/spec/BPMN/20100524/DI}BPMNShape'))==7
    draft=import_bpmn(xml.encode());restored=ProcessModel.model_validate(draft['process_model'])
    assert [node.kind for node in restored.activities].count('parallel_split')==1
    assert [node.kind for node in restored.activities].count('parallel_join')==1
    assert restored.business_rules==[]
    assert compare_path(('B1','A1','B2','A2','Close'),restored)['exact_structural_match']
    assert 'parallel_split' in process_document(packet)
    from io import BytesIO
    import json
    from zipfile import ZipFile
    with ZipFile(BytesIO(build_runtime_project(packet))) as project:
        spec=json.loads(project.read('workflow.spec.json'))
    assert spec['control_flow_contract']=='openworkgraph.parallel-node-firing-plan.v1'
    assert not spec['execution_authorization'] and synthetic_routes(model)['all_transitions_covered']
    invalid=xml.replace('gatewayDirection="Diverging"','gatewayDirection="Converging"')
    with pytest.raises(BPMNImportError,match='direction'):import_bpmn(invalid.encode())
    invalid=xml.replace('sourceRef="Activity_fork" targetRef="Activity_A1" />',
        'sourceRef="Activity_fork" targetRef="Activity_A1"><bpmn:conditionExpression>Choose</bpmn:conditionExpression></bpmn:sequenceFlow>')
    assert invalid!=xml
    with pytest.raises(BPMNImportError,match='conditional'):import_bpmn(invalid.encode())
    internal=xml.replace('sourceRef="Activity_A1" targetRef="Activity_A2" />',
        'sourceRef="Activity_A1" targetRef="Activity_A2"><bpmn:conditionExpression>Choose</bpmn:conditionExpression></bpmn:sequenceFlow>')
    with pytest.raises(BPMNImportError,match='conditional'):import_bpmn(internal.encode())
    annotated=xml.replace('sourceRef="Activity_A1" targetRef="Activity_A2" />',
        'name="Review branch notes" sourceRef="Activity_A1" targetRef="Activity_A2" />')
    inspected=import_bpmn(annotated.encode())
    assert inspected['process_model']['business_rules']==[]
    assert inspected['process_model']['manual_review_conditions']


def test_unknown_case_counts_and_aggregate_budget_never_turn_unfinished_search_into_drift(monkeypatch):
    from datetime import datetime,timedelta,timezone
    from server.process_mining_csv import mine_csv_events
    from shared.process_conformance import compare_paths
    from shared.process_drift import compare_snapshots
    model=parallel_model()
    paths=[('A1','B1','A2','B2','Close'),('A1','A2','Close')]
    def csv_log():
        start=datetime(2026,10,20,tzinfo=timezone.utc)
        return ('case_id,activity,timestamp\n'+''.join(f'{number},{activity},{(start+timedelta(seconds=index)).isoformat()}\n'
            for number,path in enumerate(paths) for index,activity in enumerate(path))).encode()
    report=mine_csv_events(csv_log(),reference_model=model)
    assert report['conformance']['exact_structural_match_cases']==1
    assert report['conformance']['structurally_deviating_cases']==1
    assert report['conformance']['structural_comparison_unknown_cases']==0
    monkeypatch.setattr('shared.process_parallel.MAX_ALIGNMENT_STATES',3)
    monkeypatch.setattr('shared.process_parallel.MAX_BATCH_ALIGNMENT_STATES',4)
    comparisons=compare_paths(paths,model)
    assert sum(row['alignment_state_count'] for row in comparisons.values())<=4
    assert all(row['exact_structural_match'] is None for row in comparisons.values())
    report=mine_csv_events(csv_log(),reference_model=model)
    assert report['conformance']['exact_structural_match_cases']==0
    assert report['conformance']['structurally_deviating_cases']==0
    assert report['conformance']['structural_comparison_unknown_cases']==2
    assert not report['conformance']['comparison_covers_all_cases_and_variants']
    assert report['conformance']['all_cases_attempted']
    snapshot={'reference':{},'cases':[{'path':path,'duration_seconds':len(path)-1,'gaps':[1]*(len(path)-1)} for path in paths],
        'data_quality':{'cases_with_tied_timestamps':0},'event_window':{'first_event_at':'2026-10-20T00:00:00Z','last_event_at':'2026-10-20T00:01:00Z'}}
    drift=compare_snapshots(snapshot,snapshot,reference_model=model,reference={'workflow_id':'p','revision':1})['conformance']
    assert drift['baseline_structurally_deviating_cases']==drift['current_structurally_deviating_cases']==0
    assert drift['baseline_structural_comparison_unknown_cases']==drift['current_structural_comparison_unknown_cases']==2
    assert drift['exact_match_share_change_percentage_points'] is None
    assert not drift['all_retained_cases_and_full_paths_compared']


def test_parallel_regions_can_repeat_through_explicit_exclusive_outer_routing():
    data=parallel_model().model_dump()
    data['activities'] += [{'id':'start','label':'Start','kind':'start'},
        {'id':'merge','label':'Enter region','kind':'routing'}, {'id':'repeat','label':'Repeat','kind':'decision'}]
    data['entry_activity_id']='start'
    data['transitions']=[edge for edge in data['transitions'] if edge['source']!='join']+[
        {'source':'start','target':'merge'}, {'source':'merge','target':'fork'},
        {'source':'join','target':'repeat'}, {'source':'repeat','target':'merge','rule_id':'again'},
        {'source':'repeat','target':'close','rule_id':'finished'}]
    data['business_rules']=[{'id':'again','text':'Review whether another cycle is needed'},
        {'id':'finished','text':'Review whether checks are complete'}]
    result=compare_path(('A1','B1','A2','B2','B1','B2','A1','A2','Close'),ProcessModel.model_validate(data))
    assert result['exact_structural_match'] and not result['business_rules_evaluated']
    assert len(result['alignment'])==9


def test_exported_linear_runtime_rejects_a_rehashed_parallel_spec():
    from pathlib import Path
    import hashlib,json
    scope={}
    template=Path(__file__).resolve().parents[1]/'shared/process_agent_templates/runtime.py.txt'
    exec(compile(template.read_text(),str(template),'exec'),scope)
    model=parallel_model().model_dump()
    forged={'process_model':model,'process_model_sha256':hashlib.sha256(json.dumps(model,sort_keys=True).encode()).hexdigest(),
        'execution_authorization':False,'live_business_tools':[]}
    with pytest.raises(ValueError,match='cannot plan or execute parallel'):scope['validate_spec'](forged)


def test_dashboard_save_export_analysis_and_runtime_contract_preserve_one_reviewed_model(tmp_path):
    import os,json,subprocess,sys
    from pathlib import Path
    path=tmp_path/'model.json';path.write_text(json.dumps(parallel_model().model_dump()))
    code=r'''
import json,sys
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
model=json.load(open(sys.argv[1]))
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
with TestClient(app) as client:
 body={'title':'Parallel reviews','process_model':model,'user_confirmed':True}
 assert client.post('/v1/process-catalog',json=body).status_code==401
 saved=client.post('/v1/process-catalog',json=body,headers=headers)
 assert saved.status_code==200,saved.text
 record=saved.json();query={'workflow_id':record['workflow_id'],'revision':1}
 for fmt in ('json','bpmn','pdd'):
  response=client.get('/v1/process-catalog/export',params={**query,'format':fmt},headers=headers)
  assert response.status_code==200,response.text
  assert response.headers['cache-control']=='no-store'
 project=client.get('/v1/process-catalog/agent-project',params=query,headers=headers)
 assert project.status_code==200,project.text
 from io import BytesIO
 from zipfile import ZipFile
 spec=json.loads(ZipFile(BytesIO(project.content)).read('workflow.spec.json'))
 assert spec['control_flow_contract']=='openworkgraph.parallel-node-firing-plan.v1'
 assert spec['process_model']==record['process_model'] and not spec['execution_authorization']
 raw=b'case_id,activity,timestamp\nprivate_case,A1,2026-10-20T00:00:00Z\nprivate_case,B1,2026-10-20T00:01:00Z\nprivate_case,B2,2026-10-20T00:02:00Z\nprivate_case,A2,2026-10-20T00:03:00Z\nprivate_case,Close,2026-10-20T00:04:00Z\n'
 response=client.post('/v1/process-mining/preview',params=query,content=raw,headers={**headers,'Content-Type':'text/csv'})
 assert response.status_code==200,response.text
 value=response.json();assert value['conformance']['exact_structural_match_cases']==1
 assert value['conformance']['structural_comparison_unknown_cases']==0
 assert 'private_case' not in response.text
 assert value['conformance']['reference']['content_sha256']==record['content_sha256']
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code,str(path)],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


def test_parallel_minimum_edit_cost_matches_an_independent_finite_language_oracle():
    import random
    legal=[]
    for positions in combinations(range(4),2):
        a=iter(('A1','A2'));b=iter(('B1','B2'))
        legal.append(tuple(next(a) if index in positions else next(b) for index in range(4))+('Close',))
    def distance(left,right):
        previous=list(range(len(right)+1))
        for index,value in enumerate(left,1):
            current=[index]
            for column,other in enumerate(right,1):
                current.append(min(previous[column]+1,current[-1]+1,
                    previous[column-1] if value==other else previous[column-1]+2))
            previous=current
        return previous[-1]
    randomizer=random.Random(730);model=parallel_model()
    for _ in range(60):
        path=tuple(randomizer.choice(('A1','A2','B1','B2','Close','Extra')) for _ in range(randomizer.randrange(11)))
        result=compare_path(path,model);expected=min(distance(path,valid) for valid in legal)
        assert result['minimum_activity_edit_count']==expected,path
        assert result['exact_structural_match']==(path in legal)
        assert sum(row['type']!='matched' for row in result['alignment'])==expected


def test_parallel_join_cannot_be_the_initial_marking_even_with_a_structurally_reachable_loop():
    data=parallel_model().model_dump();data['entry_activity_id']='join'
    data['activities'].append({'id':'choice','label':'Continue','kind':'decision'})
    data['transitions']=[edge for edge in data['transitions'] if edge['source']!='join']+[
        {'source':'join','target':'choice'},{'source':'choice','target':'fork','rule_id':'repeat'},
        {'source':'choice','target':'close','rule_id':'stop'}]
    data['business_rules']=[{'id':'repeat','text':'Review another cycle'},{'id':'stop','text':'Review completion'}]
    with pytest.raises(ValidationError,match='Parallel joins require'):ProcessModel.model_validate(data)


def test_serial_parallel_regions_preserve_their_separate_completion_barriers():
    data=parallel_model().model_dump()
    next(node for node in data['activities'] if node['id']=='close')['kind']='parallel_split'
    data['activities'] += [{'id':'c','label':'C'},{'id':'d','label':'D'},
        {'id':'join2','label':'Second barrier','kind':'parallel_join'},{'id':'finish','label':'Finish'}]
    data['terminal_activity_ids']=['finish']
    data['transitions'] += [{'source':a,'target':b} for a,b in [('close','c'),('close','d'),
        ('c','join2'),('d','join2'),('join2','finish')]]
    model=ProcessModel.model_validate(data)
    assert compare_path(('B1','A1','A2','B2','D','C','Finish'),model)['exact_structural_match']
    assert not compare_path(('B1','A1','A2','D','B2','C','Finish'),model)['exact_structural_match']
