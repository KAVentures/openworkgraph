"""Nested paired blocks against an independently enumerated finite language."""
from itertools import combinations, permutations
from io import BytesIO
import json
import os
import subprocess
import sys
from zipfile import ZipFile

import pytest
from pydantic import ValidationError
from shared.process_model import ProcessModel
from shared.process_parallel import parallel_regions, replay_node_ids
from shared.process_conformance import compare_path
from shared.process_agent_project import build_runtime_project, synthetic_routes


def nested_model():
    nodes=[{'id':node,'label':node} for node in ('A1','A2','B1','B2','C1','D1','close')]
    nodes += [{'id':node,'label':node,'kind':kind} for node,kind in
              [('fork','parallel_split'),('join','parallel_join'),('inner','parallel_split'),('innerJoin','parallel_join')]]
    pairs=[('fork','A1'),('fork','B1'),('A1','inner'),('inner','C1'),('inner','D1'),
           ('C1','innerJoin'),('D1','innerJoin'),('innerJoin','A2'),('A2','join'),
           ('B1','B2'),('B2','join'),('join','close')]
    return ProcessModel(goal='Nested independent checks',baseline_source='user_declared',baseline_path=['Declared checks'],
        activities=nodes,transitions=[{'source':a,'target':b} for a,b in pairs],
        entry_activity_id='fork',terminal_activity_ids=['close'])


def language():
    result=set()
    for middle in permutations(('C1','D1')):
        left=('A1',*middle,'A2')
        for positions in combinations(range(6),4):
            a=iter(left);b=iter(('B1','B2'))
            result.add(tuple(next(a) if i in positions else next(b) for i in range(6))+('close',))
    return result


def firing_plan(path):
    result=['fork'];inner_seen=set()
    for node in path[:-1]:
        result.append(node)
        if node=='A1':result.append('inner')
        if node in {'C1','D1'}:
            inner_seen.add(node)
            if len(inner_seen)==2:result.append('innerJoin')
    return result+['join','close']


def test_every_nested_interleaving_and_firing_plan_consumes_all_inner_and_outer_tokens():
    model=nested_model();words=language();assert len(words)==30
    assert parallel_regions(model)==[
        {'split':'fork','join':'join','branches':[['A1','inner','C1','D1','innerJoin','A2'],['B1','B2']]},
        {'split':'inner','join':'innerJoin','branches':[['C1'],['D1']]}]
    for word in words:
        result=compare_path(word,model)
        assert result['comparison_complete'] and result['exact_structural_match']
        checked=replay_node_ids(firing_plan(word),model)
        assert checked['all_produced_branch_tokens_consumed']
        assert checked['transition_indices']==list(range(12))


@pytest.mark.parametrize('path',[('A1','C1','A2','B1','B2','close'),
    ('A1','C1','D1','A2','close'), ('C1','A1','D1','A2','B1','B2','close'),
    ('A1','C1','D1','A2','B2','B1','close')])
def test_nested_alignment_edit_count_matches_independent_language_oracle(path):
    def distance(left,right):
        row=list(range(len(right)+1))
        for a in left:
            current=[row[0]+1]
            for i,b in enumerate(right):
                current.append(min(row[i+1]+1,current[-1]+1,row[i]+(0 if a==b else 2)))
            row=current
        return row[-1]
    result=compare_path(path,nested_model())
    assert result['comparison_complete'] and not result['exact_structural_match']
    assert result['minimum_activity_edit_count']==min(distance(path,word) for word in language())


def test_nested_gateway_firings_cannot_skip_inner_branch_or_join_early():
    model=nested_model()
    good=['fork','A1','inner','C1','D1','innerJoin','A2','B1','B2','join','close']
    for bad in ([x for x in good if x!='D1'], [x for x in good if x!='innerJoin'],
                ['fork','A1','inner','C1','innerJoin','D1','A2','B1','B2','join','close']):
        with pytest.raises(ValueError):replay_node_ids(bad,model)


def test_nested_empty_branch_and_model_node_order_do_not_change_pairing():
    raw=nested_model().model_dump()
    raw['activities']=[node for node in raw['activities'] if node['id']!='C1'][::-1]
    raw['transitions']=[edge for edge in raw['transitions'] if edge['source']!='C1' and edge['target']!='C1']
    raw['transitions'].append({'source':'inner','target':'innerJoin'})
    model=ProcessModel.model_validate(raw)
    plan=synthetic_routes(model)['cases'][0]['activity_ids']
    assert len(plan)==len(model.activities) and len(set(plan))==len(plan)
    assert replay_node_ids(plan,model)['transition_indices']==list(range(len(model.transitions)))
    assert compare_path(('A1','D1','A2','B1','B2','close'),model)['exact_structural_match']


def insert_block(raw, before, prefix):
    split,join=prefix+'Fork',prefix+'Join'
    incoming=next(edge for edge in raw['transitions'] if edge['target']==before)
    incoming['target']=split
    raw['activities'] += [{'id':split,'label':split,'kind':'parallel_split'},
                          {'id':join,'label':join,'kind':'parallel_join'},
                          {'id':prefix+'X','label':prefix+'X'}, {'id':prefix+'Y','label':prefix+'Y'}]
    raw['transitions'] += [{'source':a,'target':b} for a,b in
        [(split,prefix+'X'),(split,prefix+'Y'),(prefix+'X',join),(prefix+'Y',join),(join,before)]]


def test_deeper_and_sibling_blocks_preserve_unique_pairing_under_node_permutation():
    raw=nested_model().model_dump()
    insert_block(raw,'C1','deep');insert_block(raw,'B1','sibling')
    for activities in (raw['activities'],raw['activities'][::-1]):
        model=ProcessModel.model_validate({**raw,'activities':activities})
        regions=parallel_regions(model)
        assert {(r['split'],r['join']) for r in regions}=={
            ('fork','join'),('inner','innerJoin'),('deepFork','deepJoin'),('siblingFork','siblingJoin')}
        examples=synthetic_routes(model)
        assert examples['all_transitions_covered']
        for example in examples['cases']:
            plan=example['activity_ids'];assert len(plan)==len(set(plan))==len(model.activities)
            assert replay_node_ids(plan,model)['all_produced_branch_tokens_consumed']
            path=tuple(node for node in plan if next(a for a in model.activities if a.id==node).kind=='activity')
            assert compare_path(path,model)['exact_structural_match']


def test_nested_alignment_budget_unknown_has_no_partial_match(monkeypatch):
    monkeypatch.setattr('shared.process_parallel.MAX_ALIGNMENT_STATES',2)
    value=compare_path(next(iter(language())),nested_model())
    assert not value['comparison_complete'] and value['exact_structural_match'] is None
    assert value['minimum_activity_edit_count'] is None


def test_nested_shortest_route_prefix_precision_and_classic_replay_use_every_branch():
    from shared.process_metrics import minimum_route_activities, visible_prefix_precision
    from shared.process_token_replay import replay_path
    from test_process_metrics import language_precision
    model=nested_model();words=sorted(language())
    assert minimum_route_activities(model)==7
    for path in words:
        replay=replay_path(path,model)
        # Twelve physical edges plus the initial and final places.
        assert (replay['produced'],replay['consumed'],replay['missing'],replay['remaining'])==(14,14,0,0)
        assert replay['fits'] and replay['fitness']==1
    for chosen in (words[:1],words[::3],words):
        observations=[(word,i+1) for i,word in enumerate(chosen)]
        precision=visible_prefix_precision(model,observations)
        assert precision['calculation_complete']
        assert precision['score']==pytest.approx(language_precision(set(words),observations))
    assert visible_prefix_precision(model,[(word,1) for word in words])['score']==1


def test_nested_export_retains_all_gateway_firings_and_bpmn_round_trip():
    from shared.process_catalog import process_bpmn,process_document
    from shared.process_bpmn_import import import_bpmn
    model=nested_model();packet={'format':'openworkgraph.versioned-process-export.v1','workflow_id':'nested',
        'revision':1,'content_sha256':'a'*64,'execution_authorization':False,'title':'Nested checks','process_model':model.model_dump()}
    routes=synthetic_routes(model)
    assert routes['all_transitions_covered'] and not routes['customer_cases_used']
    for case in routes['cases']:
        assert len(case['activity_ids'])==len(model.activities)
        replay_node_ids(case['activity_ids'],model)
    with ZipFile(BytesIO(build_runtime_project(packet))) as z:
        spec=json.loads(z.read('workflow.spec.json'))
        assert spec['process_model']==model.model_dump() and spec['execution_authorization'] is False
    exported=process_bpmn(packet)
    imported=import_bpmn(exported.encode() if isinstance(exported,str) else exported)
    assert sum(a['kind']=='parallel_split' for a in imported['process_model']['activities'])==2
    assert sum(a['kind']=='parallel_join' for a in imported['process_model']['activities'])==2
    assert 'innerJoin' in process_document(packet)


def test_portable_nested_sdk_project_runs_its_suite_and_refuses_a_missing_inner_branch(tmp_path):
    model=nested_model();packet={'format':'openworkgraph.versioned-process-export.v1','workflow_id':'nested',
        'revision':1,'content_sha256':'a'*64,'execution_authorization':False,'process_model':model.model_dump()}
    with ZipFile(BytesIO(build_runtime_project(packet))) as z:z.extractall(tmp_path)
    sdk=os.environ.get('OWG_AGENT_SDK_PYTHON',sys.executable)
    result=subprocess.run([sdk,'-m','unittest','discover','-v'],cwd=tmp_path,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    code='''
import json
from runtime import load_spec,check_plan
s=load_spec();ids=json.load(open('synthetic-cases.json'))['cases'][0]['activity_ids']
p={'revision':1,'content_sha256':'a'*64,'activity_ids':ids}
r=check_plan(p,s)
assert r['all_produced_branch_tokens_consumed'] and not r['execution_permitted']
assert set(r['requires_tool_mapping_ids'])=={'A1','A2','B1','B2','C1','D1','close'}
try:check_plan({**p,'activity_ids':[n for n in ids if n!='D1']},s)
except ValueError:pass
else:raise AssertionError('Incomplete nested branch accepted')
'''
    result=subprocess.run([sdk,'-c',code],cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('change',['crossover','shared_join','escape','conditional','cycle'])
def test_nested_invalid_region_shapes_are_rejected(change):
    raw=nested_model().model_dump()
    if change=='crossover':raw['transitions'].append({'source':'B1','target':'C1'})
    if change=='shared_join':
        raw['transitions']=[e for e in raw['transitions'] if e['source']!='D1']
        raw['transitions'].append({'source':'D1','target':'join'})
    if change=='escape':
        raw['transitions']=[e for e in raw['transitions'] if e['source']!='innerJoin']
        raw['transitions'].append({'source':'innerJoin','target':'close'})
    if change=='conditional':
        raw['business_rules']=[{'id':'condition','text':'Unconfirmed','user_confirmed':False}]
        next(e for e in raw['transitions'] if e['source']=='C1')['rule_id']='condition'
    if change=='cycle':
        raw['transitions']=[e for e in raw['transitions'] if e['source']!='innerJoin']
        raw['transitions'].append({'source':'innerJoin','target':'inner'})
    with pytest.raises(ValidationError):ProcessModel.model_validate(raw)
