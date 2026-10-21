"""Actual generated reports reconnect to the exact local saved process safely."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from shared.process_agent_project import build_runtime_project, build_runtime_spec
from shared.process_shadow_review import ShadowEvaluation, inspect_shadow_report

ROOT = Path(__file__).resolve().parents[1]


def model():
    return {'goal': 'Review selected support case', 'baseline_path': ['Review', 'Close'],
        'baseline_source': 'user_declared', 'activities': [{'id': 'a', 'label': 'Review'}, {'id': 'b', 'label': 'Close'}],
        'transitions': [{'source': 'a', 'target': 'b'}], 'entry_activity_id': 'a', 'terminal_activity_ids': ['b']}


def generated_report(packet, directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    with ZipFile(BytesIO(build_runtime_project(packet))) as archive: archive.extractall(directory)
    code = '''import json
from evaluate import compare
from execute import digest,reference
from runtime import load_spec
spec=load_spec()
suite={'format':'openworkgraph.reviewed-shadow-cases.v1','process_reference':reference(spec),
 'spec_sha256':digest(spec),'reviewed_by_customer':True,'case_inputs_authorized':True,
 'cases':[{'id':'case_1','category':'normal','input':{'text':'PRIVATE_CASE_VALUE'},
 'expected':{'status':'proposal','accepted_routes':[['a','b']],'review_confirmed':True}}]}
results={'format':'openworkgraph.shadow-plan-results.v1','process_reference':reference(spec),
 'spec_sha256':digest(spec),'suite_sha256':digest(suite),'results':[{'case_id':'case_1','plan':{
 'revision':spec['revision'],'content_sha256':spec['content_sha256'],'status':'needs_clarification',
 'activity_ids':[],'missing_business_inputs':['PRIVATE_MODEL_QUESTION']}}]}
with open('report.json','w',encoding='utf-8') as stream:json.dump(compare(spec,suite,results),stream)
'''
    run = subprocess.run([sys.executable, '-c', code], cwd=directory, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads((directory / 'report.json').read_text(encoding='utf-8'))


@pytest.fixture
def sample(tmp_path):
    packet = {'format': 'openworkgraph.versioned-process-export.v1', 'workflow_id': 'reviewed_process',
        'revision': 1, 'content_sha256': 'a'*64, 'execution_authorization': False, 'process_model': model()}
    return packet, generated_report(packet, tmp_path / 'project')


def test_generated_report_is_bound_to_exact_specification_and_keeps_mismatches_unverified(sample):
    packet, report = sample
    validated = ShadowEvaluation.model_validate(report)
    result = inspect_shadow_report(packet, validated, latest_revision=2)
    assert result['exact_specification_match'] is True
    assert result['references_latest_revision_at_inspection'] is False
    assert result['report']['cases'][0]['reason'] == 'unexpected_clarification'
    assert result['report']['matching_case_count'] == 0
    for field in ('report_authenticity_verified', 'customer_expectations_independently_verified',
                  'source_outcomes_verified', 'execution_authorized', 'result_persisted', 'process_model_updated'):
        assert result[field] is False
    assert 'PRIVATE_' not in json.dumps(result)
    with ZipFile(BytesIO(build_runtime_project(packet))) as archive:
        assert json.loads(archive.read('workflow.spec.json')) == build_runtime_spec(packet)
    for changed in ({'content_sha256': 'c'*64}, {'revision': 2}, {'workflow_id': 'other_process'},
                    {'process_model': {**packet['process_model'], 'goal': 'A changed goal'}}):
        with pytest.raises(ValueError): inspect_shadow_report(packet | changed, validated, latest_revision=2)


def test_reports_reject_raw_fields_inconsistent_counts_grants_and_non_strict_values(sample):
    _, report = sample
    mutations = [{'case_inputs_and_model_text_included': True}, {'business_tools_called': True},
        {'execution_authorized': True}, {'source_outcomes_verified': True}, {'process_model_updated': True},
        {'independent_business_ground_truth_verified': True}, {'case_count': True}, {'execution_authorized': 0},
        {'raw_case': 'PRIVATE_CASE_VALUE'}, {'reason_counts': {'unexpected_clarification': 2}},
        {'matching_case_count': 1}, {'evaluated_at': '2026-10-21T12:00:00'}, {'review_required': False}]
    for changed in mutations:
        with pytest.raises(ValidationError): ShadowEvaluation.model_validate(report | changed)
    for changed in ({'case_id': 'PRIVATE_CUSTOMER'}, {'reason': 'PRIVATE_MODEL_QUESTION'},
                    {'matches_reviewed_expectation': True}, {'raw_case': 'PRIVATE_CASE_VALUE'}):
        value = deepcopy(report); value['cases'][0].update(changed)
        with pytest.raises(ValidationError): ShadowEvaluation.model_validate(value)


def test_dashboard_report_inspection_has_no_retention_and_keeps_old_revision_identity(tmp_path):
    code = r'''
import json,sys,sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_process_shadow_review import generated_report,model
from server.secure_app import app
from server import local_auth
from server import db
with TestClient(app) as client:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 saved=client.post('/v1/process-catalog',headers=h,json={'title':'Support review','process_model':model(),'user_confirmed':True}).json()
 params={'workflow_id':saved['workflow_id'],'revision':1}
 packet=client.get('/v1/process-catalog/export',headers=h,params=params).json()
 report=generated_report(packet,Path(sys.argv[1])/'project')
 body={**params,'report':report}
 assert client.post('/v1/process-catalog/shadow-review',json=body).status_code==401
 bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
 assert client.post('/v1/process-catalog/shadow-review',headers=bearer,json=body).status_code==403
 result=client.post('/v1/process-catalog/shadow-review',headers=h,json=body)
 assert result.status_code==200,result.text
 data=result.json();assert data['exact_specification_match'] and data['references_latest_revision_at_inspection']
 assert data['report']['review_required'] and data['result_persisted'] is False
 assert result.headers['cache-control']=='no-store'
 assert 'PRIVATE_' not in result.text
 for changes in ({'report':report|{'raw_case':'PRIVATE_CASE_VALUE'}},{'report':report|{'spec_sha256':'c'*64}},
                 {'revision':True},{'report':report|{'execution_authorized':True}}):
  rejected=client.post('/v1/process-catalog/shadow-review',headers=h,json=body|changes)
  assert rejected.status_code==422 and 'PRIVATE_' not in rejected.text
 updated=client.post('/v1/process-catalog',headers=h,json={'workflow_id':saved['workflow_id'],'title':'New support review',
  'expected_revision':1,'user_confirmed':True,'process_model':model()})
 assert updated.status_code==200,updated.text
 old=client.post('/v1/process-catalog/shadow-review',headers=h,json=body).json()
 assert old['process_reference']['revision']==1 and old['latest_revision_at_inspection']==2
 assert old['references_latest_revision_at_inspection'] is False
 assert client.post('/v1/process-catalog/shadow-review',headers=h,json=body|{'revision':2}).status_code==422
 history=client.get('/v1/process-catalog/history',headers=h,params={'workflow_id':saved['workflow_id']}).json()
 assert len(history['workflows'])==2
 with db.connect() as conn:
  assert conn.execute('SELECT COUNT(*) FROM workflow_knowledge').fetchone()[0]==2
  assert conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
  assert 'PRIVATE_' not in '\n'.join(conn.iterdump())
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path/'data'),
           'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path/'auth'), 'WORKFLOW_OBSERVER_CONFIG': str(tmp_path/'config.json')}
    run = subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
