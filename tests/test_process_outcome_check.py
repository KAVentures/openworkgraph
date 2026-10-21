"""Process-specific source checks reuse receipts and cannot invent causal success."""
import os
from pathlib import Path
import subprocess
import sys


def test_dashboard_exact_revision_expectation_and_source_read_boundary(tmp_path):
    code=r'''
from datetime import datetime,timezone
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server import workflow_evidence_routes as routes
from server.open_work_outcomes import source_operation_receipt

ref='owg:r:'+'a'*32
model={'goal':'Resolve selected case','baseline_path':['Open','Close'],'baseline_source':'user_declared',
 'activities':[{'id':'a','label':'Open'},{'id':'b','label':'Close'}],
 'transitions':[{'source':'a','target':'b'}],'entry_activity_id':'a','terminal_activity_ids':['b'],
 'outcome_expectations':[{'terminal_activity_id':'b','operation':'github_pr_merged','user_confirmed':True}]}
calls=[]
def receipt(resource,operation):
 calls.append((resource,operation))
 graph={'record_observed':True,'source_event_limit_reached':False,
   'record_identity_complete_within_retained_window':True,'record_type':{'provider':'github','kind':'pull_request'},
   'source_evidence':{'availability':'source_reported','source_state_reported':True,
   'verified_at':datetime.now(timezone.utc).isoformat(),'source_state':'merged'}}
 return source_operation_receipt(resource,operation,record_reader=lambda *a,**k:graph)
routes.source_operation_receipt=receipt
with TestClient(app) as client:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 saved=client.post('/v1/process-catalog',headers=h,json={'title':'PR lifecycle','process_model':model,'user_confirmed':True}).json()
 body={'workflow_id':saved['workflow_id'],'revision':1,'terminal_activity_id':'b',
       'expected_operation':'github_pr_merged','resource_ref':ref,'source_reads_confirmed':True}
 assert client.post('/v1/process-catalog/check-outcome',json=body).status_code==401
 bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
 assert client.post('/v1/process-catalog/check-outcome',headers=bearer,json=body).status_code==403
 for changes,expected in [({'source_reads_confirmed':False},403),({'expected_operation':'stripe_invoice_paid'},422),
  ({'terminal_activity_id':'a'},422),({'revision':99},404),({'resource_ref':'https://untrusted.example'},422),
  ({'source_reads_confirmed':'true'},422),({'extra':'untrusted'},422)]:
  result=client.post('/v1/process-catalog/check-outcome',headers=h,json={**body,**changes})
  assert result.status_code==expected,(changes,result.text)
 assert calls==[]
 result=client.post('/v1/process-catalog/check-outcome',headers=h,json=body)
 assert result.status_code==200,result.text
 data=result.json()
 assert data['receipt']['operation_verified'] is True
 assert data['reference']['content_sha256']==saved['content_sha256']
 assert data['association_basis']=='user_selected_process_expectation_and_exact_observed_record'
 assert not data['business_objective_verified'] and not data['agent_caused_operation_verified']
 assert not data['execution_authorization'] and not data['result_persisted']
 assert calls==[(ref,'github_pr_merged')]
 history=client.get('/v1/process-catalog/history',headers=h,params={'workflow_id':saved['workflow_id']}).json()
 assert len(history['workflows'])==1
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),
         'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,
                          text=True,capture_output=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
