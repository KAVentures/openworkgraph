"""Portable fork/join plans retain all branches and customer execution boundaries."""
from copy import deepcopy
from io import BytesIO
from itertools import combinations
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest
from shared.process_agent_project import build_runtime_project, build_runtime_spec, synthetic_routes
from shared.process_model import ProcessModel
from shared.process_parallel import replay_node_ids
from test_parallel_process_model import parallel_model

ROOT=Path(__file__).resolve().parents[1]


def packet(model):
    return {'format':'openworkgraph.versioned-process-export.v1','workflow_id':'parallel','revision':1,
            'content_sha256':'a'*64,'execution_authorization':False,'process_model':model.model_dump()}


def test_every_branch_interleaving_has_all_transitions_and_consumes_all_tokens():
    graph=parallel_model()
    for positions in combinations(range(4),2):
        a=iter(('A1','A2'));b=iter(('B1','B2'))
        middle=[next(a) if n in positions else next(b) for n in range(4)]
        result=replay_node_ids(['fork',*middle,'join','close'],graph)
        assert result['transition_indices']==list(range(7)) and result['rule_ids']==[]
        assert result['all_produced_branch_tokens_consumed']


@pytest.mark.parametrize('ids',[
    ['fork','A1','A2','join','close'], ['fork','A2','A1','B1','B2','join','close'],
    ['fork','A1','B1','join','A2','B2','close'], ['fork','A1','A2','B1','B2','close'],
    ['A1','A2','B1','B2','join','close'], ['fork','fork','A1','A2','B1','B2','join','close'],
    ['fork','A1','A2','B1','B2','join','close','close'],
])
def test_invalid_or_incomplete_firings_never_become_a_valid_schedule(ids):
    with pytest.raises(ValueError):replay_node_ids(ids,parallel_model())


def test_firing_budget_has_no_partial_success_and_repeated_labels_keep_distinct_ids():
    raw=parallel_model().model_dump()
    for node in raw['activities']:
        if node['kind']=='activity':node['label']='Check'
    graph=ProcessModel.model_validate(raw)
    ids=['fork','A1','B1','A2','B2','join','close']
    result=replay_node_ids(ids,graph)
    assert replay_node_ids(ids,graph,max_work=result['work_count'])==result
    with pytest.raises(ValueError,match='work limit'):replay_node_ids(ids,graph,max_work=result['work_count']-1)
    with pytest.raises(ValueError):replay_node_ids(['fork','Check','join','close'],graph)


def test_empty_branch_and_outer_decision_rules_preserve_exact_firing_edges():
    raw=parallel_model().model_dump()
    raw['activities']=[n for n in raw['activities'] if n['id'] not in {'B1','B2'}]
    raw['activities'] += [{'id':'choice','label':'Choose','kind':'decision'}, {'id':'other','label':'Other','kind':'end'}]
    raw['transitions']=[e for e in raw['transitions'] if not ({e['source'],e['target']}&{'B1','B2'})]
    raw['transitions'] += [{'source':'fork','target':'join'}, {'source':'choice','target':'fork','rule_id':'checks'},
                          {'source':'choice','target':'other','rule_id':'other_rule'}]
    raw['entry_activity_id']='choice';raw['terminal_activity_ids']=['close','other']
    raw['business_rules']=[{'id':'checks','text':'Run both branches','user_confirmed':True},
                           {'id':'other_rule','text':'Other route needs review','user_confirmed':False}]
    graph=ProcessModel.model_validate(raw)
    checked=replay_node_ids(['choice','fork','A1','A2','join','close'],graph)
    assert checked['rule_ids']==['checks']
    assert replay_node_ids(['choice','other'],graph)['rule_ids']==['other_rule']
    routes=synthetic_routes(graph)
    assert routes['cases'] and routes['all_transitions_covered']
    for route in routes['cases']:replay_node_ids(route['activity_ids'],graph)


def test_exported_parallel_project_is_portable_and_uses_the_same_graph_validator(tmp_path):
    graph=parallel_model();payload=packet(graph);spec=build_runtime_spec(payload)
    assert spec['control_flow_contract']=='openworkgraph.parallel-node-firing-plan.v1'
    assert spec['execution_schedule']=='serial_token_valid_node_order'
    assert not spec['execution_authorization'] and spec['live_business_tools']==[]
    project=tmp_path/'project';project.mkdir()
    with ZipFile(BytesIO(build_runtime_project(payload))) as z:
        for name in ('process_model.py','process_parallel.py','process_rule_conditions.py','process_opportunities.py'):
            assert z.read('owg_model/'+name).decode('utf-8')==(ROOT/'shared'/name).read_text(encoding='utf-8')
        z.extractall(project)
    sdk=os.environ.get('OWG_AGENT_SDK_PYTHON',sys.executable)
    result=subprocess.run([sdk,'-m','unittest','discover','-v'],cwd=project,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    check=r'''
from copy import deepcopy
import hashlib,json
from runtime import load_spec,check_plan,validate_spec
spec=load_spec();plan={'revision':1,'content_sha256':'a'*64,'activity_ids':['fork','A1','B1','A2','B2','join','close']}
checks=check_plan(plan,spec)
assert checks['all_produced_branch_tokens_consumed'] and not checks['execution_permitted']
assert set(checks['requires_tool_mapping_ids'])=={'A1','A2','B1','B2','close'}
missing={**plan,'activity_ids':['fork','A1','A2','join','close']}
try:check_plan(missing,spec);raise AssertionError('missing branch accepted')
except ValueError:pass
bad=deepcopy(spec);bad['process_model']['entry_activity_id']='A1'
bad['process_model_sha256']=hashlib.sha256(json.dumps(bad['process_model'],sort_keys=True).encode()).hexdigest()
try:validate_spec(bad);raise AssertionError('invalid rehashed graph accepted')
except ValueError:pass
old=deepcopy(spec);old.pop('control_flow_contract')
try:validate_spec(old);raise AssertionError('legacy spec acquired implicit fork semantics')
except ValueError:pass
from datetime import datetime,timedelta,timezone
from test_execution import fixture
from execute import digest,prepare,review_template,validate_intent,ImplementationError
s,p,case,bindings,_=fixture();m=s['process_model']
m['activities']=[{'id':'fork','label':'Both','kind':'parallel_split','implementation':'unmapped'},*m['activities'][:-1],
                 {'id':'join','label':'Joined','kind':'parallel_join','implementation':'unmapped'},m['activities'][-1]]
m['transitions']=[{'source':a,'target':b} for a,b in [('fork','resolve'),('fork','readback'),('resolve','join'),('readback','join'),('join','done')]]
m['entry_activity_id']='fork'
m['manual_review_conditions']=[{'activity_id':'readback','kind':'exception_condition','condition_label':'Missing evidence','response':'pause_for_review'}]
s.update(control_flow_contract='openworkgraph.parallel-node-firing-plan.v1',execution_schedule='serial_token_valid_node_order',process_model_sha256=digest(m))
p['activity_ids']=['fork','resolve','readback','join','done']
ready=prepare(s,p,case,bindings);assert not ready['requirements']
intent=review_template(s,ready,case,bindings)
intent.update(customer_authorization_confirmed=True,source_reads_confirmed=True,expires_at=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat())
try:validate_intent(s,ready,case,bindings,intent);raise AssertionError('branch manual guard skipped')
except ImplementationError as e:assert 'manual conditions' in str(e)
intent['manual_condition_confirmations']=ready['manual_condition_ids']
validate_intent(s,ready,case,bindings,intent)
alternative={**p,'activity_ids':['fork','readback','resolve','join','done']}
other=prepare(s,alternative,case,bindings)
try:validate_intent(s,other,case,bindings,intent);raise AssertionError('reviewed schedule silently changed')
except ImplementationError as e:assert 'exact specification, plan' in str(e)
incomplete=deepcopy(bindings);incomplete['bindings']=[row for row in incomplete['bindings'] if row['activity_id']!='readback']
assert 'missing_authorized_tool_binding:readback' in prepare(s,p,case,incomplete)['requirements']
'''
    result=subprocess.run([sdk,'-c',check],cwd=project,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
