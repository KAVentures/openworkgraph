"""Declared predicate semantics, privacy boundaries and immutable model checks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from shared.process_model import ProcessModel
from shared.process_rule_conditions import RulePredicate,evaluate_predicate,validate_facts
from shared.process_rule_checks import RuleCase,RuleCaseBatch,check_case_rules
from shared.process_agent_project import process_model_digest,build_runtime_spec
from shared.process_catalog import process_bpmn,process_document
from shared.process_bpmn_import import import_bpmn


def predicate(operator='eq',expected='approved',path='/approval/status',match='all'):
    return {'match':match,'conditions':[{'path':path,'operator':operator,'expected':expected}]}


def graph(structured=False):
    model={'goal':'Check selected case','baseline_source':'user_declared','baseline_path':['Review','Close'],
        'activities':[{'id':'review','label':'Review'},{'id':'close','label':'Close'}],
        'transitions':[{'source':'review','target':'close','rule_id':'evidence'}],
        'business_rules':[{'id':'evidence','text':'Evidence confirmed','user_confirmed':True}],
        'entry_activity_id':'review','terminal_activity_ids':['close']}
    if structured:model['business_rules'][0]['predicate']=predicate()
    return model


def packet(model):
    return {'format':'openworkgraph.versioned-process-export.v1','workflow_id':'legacy_rule','revision':1,
            'content_sha256':'a'*64,'execution_authorization':False,'title':'Case rule check','process_model':model}


def test_existing_text_rules_keep_actual_pre_feature_model_and_spec_hashes():
    model=ProcessModel.model_validate(graph())
    assert 'predicate' not in model.model_dump()['business_rules'][0]
    # Captured from the frozen pre-feature 1d4b64b tree, not calculated from a
    # second copy of this implementation.
    assert process_model_digest(model.model_dump())=='b885456de37894d281ea2a1d79a3b5cbaed2abcb448bffcfd074971a0a86e0e5'
    spec=build_runtime_spec(packet(model.model_dump()))
    assert hashlib.sha256(json.dumps(spec,sort_keys=True,allow_nan=False).encode()).hexdigest()=='9e7efd0dff9a6b9e107dd3ce9285d7b81bb0c649ce2fa689c8a2b78d0be8e40d'
    changed=ProcessModel.model_validate(graph(True))
    assert process_model_digest(changed.model_dump())!=spec['process_model_sha256']


@pytest.mark.parametrize('operator,expected,actual,status',[
    ('eq',True,1,'not_matched'),('eq',False,0,'not_matched'),('eq',1,1.0,'matched'),
    ('ne','1',1,'matched'),('eq',None,None,'matched'),('gt',100,101,'matched'),
    ('gte',100,100,'matched'),('lt',100,101,'not_matched'),('lte',.3,.3,'matched'),
    ('gt',100,'101','unknown'),('eq',1,{'not':'scalar'},'unknown'),
    ('in',['approved','waived'],'approved','matched'),('not_in',[1,2],True,'matched'),
    ('eq','PRIVATE_EXPECTED','PRIVATE_FACT','not_matched'),
])
def test_type_sensitive_scalar_membership_and_ordering_without_fact_leaks(operator,expected,actual,status):
    result=evaluate_predicate(predicate(operator,expected,path='/field'),{'field':actual})
    assert result['status']==status
    assert 'PRIVATE_' not in json.dumps(result)
    assert result['facts_authoritativeness_verified'] is False
    assert result['business_policy_compliance_verified'] is False and result['execution_authorized'] is False


def test_missing_null_existence_json_pointer_escaping_and_three_valued_groups():
    assert evaluate_predicate(predicate(expected=None,path='/missing'),{})['status']=='unknown'
    assert evaluate_predicate(predicate('exists',None,path='/field'),{'field':None})['status']=='matched'
    assert evaluate_predicate(predicate('not_exists',None,path='/field'),{'field':None})['status']=='not_matched'
    assert evaluate_predicate(predicate('not_exists',None,path='/missing'),{})['status']=='matched'
    assert evaluate_predicate(predicate(expected=3,path='/a~1b/~0key/0'),{'a/b':{'~key':[3]}})['status']=='matched'
    assert evaluate_predicate(predicate(expected=3,path='/list/01'),{'list':[0,3]})['status']=='unknown'
    group={'match':'all','conditions':[predicate(expected=True,path='/known')['conditions'][0],
                                      predicate(expected=1,path='/missing')['conditions'][0]]}
    assert evaluate_predicate(group,{'known':False})['status']=='not_matched'
    assert evaluate_predicate(group,{'known':True})['status']=='unknown'
    group['match']='any'
    assert evaluate_predicate(group,{'known':True})['status']=='matched'
    assert evaluate_predicate(group,{'known':False})['status']=='unknown'


def test_no_code_interpolation_coercion_or_unbounded_predicate_language():
    assert evaluate_predicate(predicate(expected='do_not_execute',path='/__proto__/constructor'),
        {'__proto__':{'constructor':'do_not_execute'}})['status']=='matched'
    for value in ({'match':'all','conditions':[]}, {'match':'eval','conditions':predicate()['conditions']},
                  predicate('gt','100'),predicate('in',[]),predicate('exists',True),
                  predicate(path='$.approval.status'),predicate(path='/bad~2escape'),
                  predicate(path='/'+'/'.join(['x']*9)),predicate(expected=float('nan')),
                  predicate(expected=10**16),predicate(expected='\ud800'),
                  {**predicate(),'code':'PRIVATE_SCRIPT'}):
        with pytest.raises(ValidationError):RulePredicate.model_validate(value)
    for value in ({'x':float('inf')},{'x':10**16},{'x':'a'*4097},{'x':object()},
                  {'x':list(range(101))},{str(n):n for n in range(101)}):
        with pytest.raises(ValueError):validate_facts(value)
    nested={};root=nested
    for _ in range(9):nested['x']={};nested=nested['x']
    with pytest.raises(ValueError):validate_facts(root)


def test_report_distinguishes_confirmed_unstructured_unconfirmed_and_proposed_route_without_values():
    model=graph(True)
    model['business_rules']+=[{'id':'text_only','text':'PRIVATE_RULE_TEXT','user_confirmed':True},
                             {'id':'pending','text':'Unconfirmed condition','user_confirmed':False,'predicate':predicate()}]
    record=packet(ProcessModel.model_validate(model).model_dump());before=json.dumps(record,sort_keys=True)
    cases=[RuleCase(case_id='case_1',facts={'approval':{'status':'approved'},'PRIVATE_UNREFERENCED':'PRIVATE_FACT'}),
           RuleCase(case_id='case_2',facts={},activity_ids=['review','close']),
           RuleCase(case_id='case_3',facts={},activity_ids=['missing'])]
    report=check_case_rules(record,cases)
    assert report['cases'][0]['counts']=={'matched':1,'not_evaluated_unstructured':1,'not_evaluated_unconfirmed':1}
    assert report['cases'][1]['counts']=={'unknown':1,'not_on_proposed_route':2}
    assert report['cases'][1]['proposed_route_structure_matches'] is True
    assert report['cases'][2]['proposed_route_structure_matches'] is False
    assert report['cases'][2]['counts']=={'not_evaluated_invalid_route':3}
    assert 'PRIVATE_' not in json.dumps(report)
    assert report['process_reference']['revision']==1
    assert json.dumps(record,sort_keys=True)==before
    for flag in ('facts_source_authoritativeness_verified','business_policy_compliance_verified','observed_execution_conformance_verified',
                 'independent_outcomes_verified','facts_retained','comparison_report_retained','process_model_updated',
                 'source_reads_performed','model_provider_calls_made','execution_permissions_granted'):
        assert report[flag] is False


def test_structured_definitions_round_trip_without_importing_confirmation_or_execution():
    model=ProcessModel.model_validate(graph(True));value=packet(model.model_dump())
    exported=process_bpmn(value)
    assert 'OWG declared predicate v1:' in exported
    imported=import_bpmn(exported.encode())['process_model']
    assert imported['business_rules'][0]['predicate']==model.business_rules[0].predicate.model_dump()
    assert imported['business_rules'][0]['user_confirmed'] is False
    assert 'Structured declared predicates' in process_document(value)
    assert build_runtime_spec(value)['process_model']['business_rules'][0]['predicate']==model.business_rules[0].predicate.model_dump()
    assert 'PRIVATE_FACT' not in process_document(value)


def test_case_labels_batch_bounds_and_route_types_are_strict():
    for value in ({'cases':[]},{'cases':[{'case_id':'PRIVATE_ID','facts':{}}]},
                  {'cases':[{'case_id':'case_1','facts':{},'extra':'PRIVATE_FACT'}]},
                  {'cases':[{'case_id':'case_1','facts':{}}]*2},
                  {'cases':[{'case_id':'case_1','facts':{},'activity_ids':[1]}]}):
        with pytest.raises(ValidationError):RuleCaseBatch.model_validate(value)


def test_http_fact_checks_are_dashboard_only_explicit_ephemeral_strict_and_exact_revision(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
import sqlite3,os,json
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
model={'goal':'Check selected case','baseline_source':'user_declared','baseline_path':['Review','Close'],
    'activities':[{'id':'review','label':'Review'},{'id':'close','label':'Close'}],
    'transitions':[{'source':'review','target':'close','rule_id':'evidence'}],
    'business_rules':[{'id':'evidence','text':'Evidence confirmed','user_confirmed':True,
       'predicate':{'match':'all','conditions':[{'path':'/approval/status','operator':'eq','expected':'approved'}]}}],
    'entry_activity_id':'review','terminal_activity_ids':['close']}
with TestClient(app) as client:
    saved=client.post('/v1/process-catalog',headers=headers,json={'workflow_id':'checks','title':'Rule checks','process_model':model,'user_confirmed':True})
    assert saved.status_code==200,saved.text
    newer=client.post('/v1/process-catalog',headers=headers,json={'workflow_id':'checks','title':'Updated checks',
        'process_model':model,'user_confirmed':True,'expected_revision':1})
    assert newer.status_code==200,newer.text
    path='/v1/process-catalog/check-rules?workflow_id=checks&revision=1'
    body={'cases':[{'case_id':'case_1','facts':{'approval':{'status':'approved'},'unused':'PRIVATE_FACT_VALUE'}}]}
    assert client.post(path,json=body).status_code==401
    assert client.post(path,json=body,headers={'Authorization':'Bearer '+local_auth.ensure_api_token(),'X-OWG-Case-Facts-Authorized':'true'}).status_code==403
    assert client.post(path,json=body,headers=headers).status_code==403
    confirmed={**headers,'X-OWG-Case-Facts-Authorized':'true'}
    before=client.get('/v1/process-catalog/history?workflow_id=checks',headers=headers).json()
    response=client.post(path,json=body,headers=confirmed)
    assert response.status_code==200,response.text
    result=response.json();assert response.headers['cache-control']=='no-store'
    assert result['process_reference']['revision']==1 and result['latest_revision_at_check']==2
    assert result['references_latest_revision_at_check'] is False
    assert result['rule_result_counts']=={'matched':1}
    assert 'PRIVATE_' not in response.text and 'approved' not in response.text
    assert client.get('/v1/process-catalog/history?workflow_id=checks',headers=headers).json()==before
    with sqlite3.connect(os.path.join(os.environ['WORKFLOW_OBSERVER_DATA'],'workflow_observer.db')) as conn:
        assert 'PRIVATE_FACT_VALUE' not in '\n'.join(conn.iterdump())
    assert client.post(path,content=b'x'*(128*1024+1),headers={**confirmed,'Content-Type':'application/json'}).status_code==413
    assert client.post(path,content=b'{"cases":[],"cases":[]}',headers={**confirmed,'Content-Type':'application/json'}).status_code==422
    assert client.post(path,json={'cases':[{'case_id':'case_1','facts':{'value':1e100}}]},headers=confirmed).status_code==422
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('metadata',[
    '{"match":"all","match":"any","conditions":[]}',
    '{"match":"all","conditions":[{"path":"/x","operator":"eval","expected":true}]}',
    '{"match":"all","conditions":[{"path":"/x","operator":"eq","expected":NaN}]}',
],ids=['duplicate-key','unsupported-operator','nonstandard-number'])
def test_malformed_recognized_bpmn_predicates_reject_without_echoing_values(metadata):
    import xml.etree.ElementTree as ET
    from shared.process_bpmn_import import BPMNImportError
    root=ET.fromstring(process_bpmn(packet(ProcessModel.model_validate(graph(True)).model_dump())))
    docs=[node for node in root.iter() if node.tag.endswith('documentation') and
          (node.text or '').startswith('OWG declared predicate v1:')]
    assert len(docs)==1
    docs[0].text='OWG declared predicate v1: '+metadata
    with pytest.raises(BPMNImportError,match='complete bounded supported format') as error:
        import_bpmn(ET.tostring(root))
    assert metadata not in str(error.value)
