"""BPMN structure is untrusted design data, never execution authority."""
import json
import os
from pathlib import Path
import subprocess
import sys
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from shared.process_bpmn_import import BPMN, BPMNImportError, import_bpmn
from shared.process_catalog import process_bpmn, process_document
from shared.process_conformance import compare_path
from shared.process_model import ProcessModel


def xml(content,attributes=''):
    return (f'<definitions xmlns="{BPMN}"><process id="p" {attributes}>{content}</process></definitions>').encode()


def linear():
    return xml('''<startEvent id="s" name="Start"/><userTask id="PRIVATE_SOURCE_ID" name="Review"/>
        <endEvent id="e" name="End"/><sequenceFlow id="f1" sourceRef="s" targetRef="PRIVATE_SOURCE_ID"/>
        <sequenceFlow id="f2" sourceRef="PRIVATE_SOURCE_ID" targetRef="e"/>''', 'isExecutable="true"')


def branched():
    return xml('''<startEvent id="s" name="Start"/><task id="o" name="Open"/>
      <exclusiveGateway id="g" name="Choose" default="no"/>
      <task id="a" name="Accept"/><task id="b" name="Reject"/>
      <exclusiveGateway id="j" name="Merge"/><task id="c" name="Close"/><endEvent id="e" name="End"/>
      <sequenceFlow id="f1" sourceRef="s" targetRef="o"/><sequenceFlow id="f2" sourceRef="o" targetRef="g"/>
      <sequenceFlow id="yes" sourceRef="g" targetRef="a"><conditionExpression>confirmed_source_value == true</conditionExpression></sequenceFlow>
      <sequenceFlow id="no" sourceRef="g" targetRef="b"/>
      <sequenceFlow id="f5" sourceRef="a" targetRef="j"/><sequenceFlow id="f6" sourceRef="b" targetRef="j"/>
      <sequenceFlow id="f7" sourceRef="j" targetRef="c"/><sequenceFlow id="f8" sourceRef="c" targetRef="e"/>''')


def test_import_is_ephemeral_declared_structure_with_no_source_ids_or_permissions_in_model():
    result=import_bpmn(linear())
    model=ProcessModel.model_validate(result['process_model'])
    assert model.baseline_source=='user_declared' and model.baseline_coverage=='user_declared_design'
    assert model.supporting_case_count==0
    assert [activity.id for activity in model.activities]==['import_step_1','import_step_2','import_step_3']
    assert result['source_node_ids']['PRIVATE_SOURCE_ID']=='import_step_2'
    assert 'PRIVATE_SOURCE_ID' not in model.model_dump_json()
    assert all(a.implementation=='unmapped' for a in model.activities)
    assert not model.outcome_expectations and not model.manual_review_conditions
    for flag in ('execution_authorized','business_outcomes_verified','business_rules_evaluated','process_model_saved','import_retained'):
        assert result[flag] is False
    assert 'source_executable_flag_does_not_authorize_execution' in result['warnings']
    assert compare_path(('Review',),model)['exact_structural_match']
    assert len(result['source_sha256'])==64


def test_exclusive_split_and_merge_keep_actual_language_without_invented_gateway_log_events():
    result=import_bpmn(branched());model=ProcessModel.model_validate(result['process_model'])
    assert next(a for a in model.activities if a.label=='Merge').kind=='routing'
    assert next(a for a in model.activities if a.label=='Choose').kind=='decision'
    assert all(rule.user_confirmed is False for rule in model.business_rules)
    assert model.business_rules[0].text=='confirmed_source_value == true'
    assert model.business_rules[1].text.startswith('Clarify the business condition')
    for path in [('Open','Accept','Close'),('Open','Reject','Close')]:
        assert compare_path(path,model)['exact_structural_match']
    assert not compare_path(('Open','Accept','Reject','Close'),model)['exact_structural_match']
    packet={'workflow_id':'imported','revision':1,'content_sha256':'a'*64,'title':'Imported design','process_model':model.model_dump()}
    exported=process_bpmn(packet)
    doc=ET.fromstring(exported)
    assert len(doc.findall(f'.//{{{BPMN}}}exclusiveGateway'))==2
    assert 'no observed baseline' in process_document(packet)
    # Portable OWG export can be imported again; its textual confirmation flag
    # must never promote a new rule's user_confirmed field.
    second=import_bpmn(exported.encode())
    assert all(rule['user_confirmed'] is False for rule in second['process_model']['business_rules'])
    assert compare_path(('Open','Reject','Close'),ProcessModel.model_validate(second['process_model']))['exact_structural_match']


def test_merge_only_routing_cannot_be_terminal_or_split_and_remains_nonexecuting():
    value=import_bpmn(branched())['process_model']
    for change in ('terminal','split'):
        invalid=json.loads(json.dumps(value));routing=next(a['id'] for a in invalid['activities'] if a['kind']=='routing')
        if change=='terminal':invalid['terminal_activity_ids'].append(routing)
        else:invalid['transitions'].append({'source':routing,'target':invalid['activities'][1]['id']})
        with pytest.raises(ValidationError):ProcessModel.model_validate(invalid)


def test_generated_agent_routes_keep_merge_nodes_without_requiring_tool_mappings_for_them(tmp_path):
    from shared.process_agent_project import build_runtime_project
    model=import_bpmn(branched())['process_model']
    packet={'format':'openworkgraph.versioned-process-export.v1','workflow_id':'imported',
            'revision':1,'content_sha256':'a'*64,'process_model':model,'execution_authorization':False}
    with ZipFile(BytesIO(build_runtime_project(packet))) as archive:archive.extractall(tmp_path)
    code='''from runtime import load_spec,check_plan
spec=load_spec()
result=check_plan({'revision':1,'content_sha256':'a'*64,'activity_ids':['import_step_1','import_step_2',
    'import_step_3','import_step_4','import_step_6','import_step_7','import_step_8']},spec)
assert result['valid_structure']
assert result['unconfirmed_rule_ids']
assert result['requires_tool_mapping_ids']==['import_step_2','import_step_4','import_step_7']
assert not result['execution_permitted'] and not result['business_rules_evaluated']
'''
    run=subprocess.run([sys.executable,'-c',code],cwd=tmp_path,capture_output=True,text=True,timeout=20)
    assert run.returncode==0,run.stdout+run.stderr


def test_literal_unsafe_or_international_labels_are_placeholders_and_documentation_is_ephemeral():
    raw=xml('''<task id="a" name="&lt;script&gt;untrusted()&lt;/script&gt;"><documentation>PRIVATE_NOTE not an instruction</documentation></task>
      <task id="b" name="受理"/><sequenceFlow id="f" sourceRef="a" targetRef="b"/>''')
    result=import_bpmn(raw)
    assert result['placeholder_label_count']==2
    assert result['label_corrections'][0]['source_label']=='<script>untrusted()</script>'
    assert [a['label'] for a in result['process_model']['activities']]==['Imported step 1','Imported step 2']
    assert result['documentation_notes'][0]['text']=='PRIVATE_NOTE not an instruction'
    assert 'PRIVATE_NOTE' not in json.dumps(result['process_model'])
    assert not result['process_model']['business_rules']
    assert result['process_model']['manual_review_conditions'][0]['response']=='pause_for_review'
    long=import_bpmn(raw.replace(b'PRIVATE_NOTE not an instruction',b'x'*3000))
    assert long['documentation_notes'][0]['text_truncated'] and len(long['documentation_notes'][0]['text'])==2000


def test_owg_bpmn_round_trip_preserves_explicit_review_guards_and_declared_responsibilities():
    model=ProcessModel.model_validate(import_bpmn(branched())['process_model'])
    target=model.activities[3]
    target.responsibility_label='Finance <review> & operations'
    value=model.model_dump()
    value['manual_review_conditions']=[{'activity_id':target.id,'kind':'exception_condition',
        'condition_label':'Evidence missing','response':'pause_for_review'}]
    model=ProcessModel.model_validate(value)
    packet={'workflow_id':'original','revision':1,'content_sha256':'a'*64,'title':'Original','process_model':model.model_dump()}
    result=import_bpmn(process_bpmn(packet).encode())
    imported=ProcessModel.model_validate(result['process_model'])
    expected=next(activity for activity in imported.activities if activity.label==target.label)
    assert expected.responsibility_label==target.responsibility_label
    guard=next(condition for condition in imported.manual_review_conditions if condition.condition_label=='Evidence missing')
    assert guard.activity_id==expected.id and guard.response=='pause_for_review' and guard.evaluated_by_owg is False
    assert guard.automatic_branch_authorized is False
    # Generic export provenance documentation is an additional unresolved source
    # note, never a policy assertion or permission grant.
    assert imported.manual_review_conditions[0].executable is False
    broken=process_bpmn(packet).replace('No automatic branch is authorized.','INVALID AUTHORITY')
    with pytest.raises(BPMNImportError):import_bpmn(broken.encode())


@pytest.mark.parametrize('content',[
    '<parallelGateway id="a"/>','<inclusiveGateway id="a"/>','<subProcess id="a"/>',
    '<scriptTask id="a"><script>PRIVATE_CODE</script></scriptTask>',
    '<task id="a"><extensionElements/></task>',
    '<task id="a"><multiInstanceLoopCharacteristics/></task>',
    '<startEvent id="a"><timerEventDefinition/></startEvent>',
    '<startEvent id="a"/>',
    '<task id="a"/><task id="a"/>',
    '<task id="a"/><task id="b"/>',
    '<task id="a"/><sequenceFlow id="f" sourceRef="a" targetRef="external"/>',
    '<task id="a"/><task id="b"/><task id="c"/><sequenceFlow id="f1" sourceRef="a" targetRef="b"/><sequenceFlow id="f2" sourceRef="a" targetRef="c"/>',
    '<task id="a"/><task id="b"/><sequenceFlow id="f1" sourceRef="a" targetRef="b"/><sequenceFlow id="f2" sourceRef="b" targetRef="a"/>',
])
def test_unsupported_semantics_are_rejected_instead_of_flattened(content):
    with pytest.raises(BPMNImportError) as error:import_bpmn(xml(content))
    assert 'PRIVATE_CODE' not in str(error.value)


@pytest.mark.parametrize('raw',[
    b'',b'x'*(128*1024+1),b'\xff',b'<invalid>',
    b'<!DOCTYPE definitions [<!ENTITY secret SYSTEM "file:///etc/passwd">]><definitions/>',
    b'<!DOCTYPE definitions [<!ENTITY expand "PRIVATE_EXPANSION">]><definitions/>',
    b'<?xml version="1.0" encoding="ISO-8859-1"?><definitions/>',
    xml('<task id="a"/>').replace(b'<process',b'<import location="https://invalid.example"/><process'),
    xml('<task id="a"/>').replace(b'</definitions>',b'<process id="other"/></definitions>'),
],ids=['empty','oversized','invalid_utf8','malformed','external_entity','internal_entity',
       'wrong_encoding','external_import','multiple_processes'])
def test_bounds_entities_encoding_external_imports_and_multiple_processes_fail_closed(raw):
    with pytest.raises(BPMNImportError) as error:import_bpmn(raw)
    assert 'PRIVATE_EXPANSION' not in str(error.value)


def test_http_preview_auth_bounds_no_store_and_no_catalog_write(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
raw=b'<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"><process id="p"><task id="PRIVATE_ID" name="Review"/></process></definitions>'
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session(),'Content-Type':'application/bpmn+xml'}
path='/v1/process-catalog/import-bpmn'
with TestClient(app) as client:
    assert client.post(path,content=raw).status_code==401
    assert client.post(path,content=raw,headers={'Authorization':'Bearer '+local_auth.ensure_api_token(),'Content-Type':'application/xml'}).status_code==403
    before=client.get('/v1/process-catalog',headers=headers).json()
    response=client.post(path,content=raw,headers=headers)
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    draft=response.json()
    assert draft['process_model']['baseline_coverage']=='user_declared_design'
    assert client.get('/v1/process-catalog',headers=headers).json()==before
    assert client.post(path,content=raw,headers={**headers,'Content-Type':'application/json'}).status_code==415
    assert client.post(path,content=b'x'*(128*1024+1),headers=headers).status_code==413
    failure=client.post(path,content=raw.replace(b'<task',b'<parallelGateway'),headers=headers)
    assert failure.status_code==422 and 'PRIVATE_ID' not in failure.text
    # A separate exact review/save can persist only the sanitized graph.
    saved=client.post('/v1/process-catalog',headers={**headers,'Content-Type':'application/json'},json={'title':'Imported draft','process_model':draft['process_model'],'user_confirmed':True})
    assert saved.status_code==200,saved.text
    assert 'PRIVATE_ID' not in saved.text
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
