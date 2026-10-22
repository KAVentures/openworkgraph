"""Explicit invoice lifecycle projection reuses existing account/record receipts."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from server.stripe_source_evidence import stripe_record_receipt
from server.stripe_process_preview import stripe_selected_invoices_process
from server.record_graph import record_graph
from test_issue226_stripe_source_evidence import REF,ID,ACCOUNT,INFO,BASE

CREATED=1792483200


def receipt(payload=None,*,include=True,wrong_account=False,wrong_record=False):
    calls=[]
    value={'object':'invoice','id':ID,'status':'paid','paid':True,'created':CREATED,
           'status_transitions':{'finalized_at':CREATED+60,'paid_at':CREATED+300,
                                 'marked_uncollectible_at':None,'voided_at':None},
           'customer_email':'PRIVATE_CUSTOMER','total':99999999}
    if payload:value.update(payload)
    if wrong_record:value['id']='in_DIFFERENT12345'
    def reader(url,key):
        calls.append(url)
        assert key==BASE['OWG_STRIPE_RESTRICTED_READ_KEY']
        if url.endswith('/v1/account'):return {'object':'account','id':'acct_OTHER123456' if wrong_account else ACCOUNT}
        assert url=='https://api.stripe.com/v1/invoices/'+ID
        return value
    result=stripe_record_receipt(REF,environment=BASE,resolver=lambda _:INFO,reader=reader,include_lifecycle=include)
    return result,calls


def test_lifecycle_requires_separate_option_and_uses_only_existing_two_source_reads():
    ordinary,calls=receipt(include=False)
    assert len(calls)==2 and 'process_audit_events' not in ordinary and 'object_type' not in ordinary
    result,calls=receipt()
    assert len(calls)==2
    assert [event['action'] for event in result['process_audit_events']]==['invoice_created','invoice_finalized','invoice_paid']
    assert all(event['source_event_ref'].startswith('source_event:') for event in result['process_audit_events'])
    assert len({event['source_event_ref'] for event in result['process_audit_events']})==3
    assert result['source_audit_history_available'] is False
    report=stripe_selected_invoices_process([result])
    assert report['process']['case_count']==1 and report['process']['input_rows']==3
    assert report['process']['variants'][0]['observed_path']==['Invoice created','Invoice finalized','Invoice paid']
    assert report['process']['metrics']['median_case_elapsed_seconds']==300
    for value in (result,report):assert 'PRIVATE_' not in json.dumps(value) and ID not in json.dumps(value)
    assert report['business_outcomes_verified'] is False and report['customer_bank_settlement_verified'] is False
    assert report['agent_causality_verified'] is False and report['result_persisted'] is False


def test_chronology_conflicts_do_not_fabricate_history_or_change_existing_current_state_receipts():
    for fields in ({'finalized_at':CREATED-1,'paid_at':CREATED+300},
                   {'finalized_at':CREATED+400,'paid_at':CREATED+300},
                   {'finalized_at':CREATED+60,'paid_at':CREATED+300,'voided_at':CREATED+500},
                   {'marked_uncollectible_at':CREATED+400,'paid_at':CREATED+300}):
        result,_=receipt({'status_transitions':fields})
        assert result['lifecycle_projection_diagnostics']['chronology_conflict'] is True
        assert not result['process_audit_events']
        assert result['source_state']=='paid' and result['verified_operational_outcome']['verified'] is True
        assert stripe_selected_invoices_process([result])['process'] is None
    result,_=receipt({'status_transitions':{'finalized_at':CREATED+60,'marked_uncollectible_at':CREATED+120,'paid_at':CREATED+300}})
    assert len(result['process_audit_events'])==4


def test_missing_invalid_or_tied_timestamps_remain_explicit_and_are_not_replaced_by_check_time():
    for created in (False,-1,'PRIVATE_INVALID_TIME',10**30):
        result,_=receipt({'created':created,'status_transitions':{}})
        assert not result['process_audit_events']
        assert result['lifecycle_projection_diagnostics']['invalid_timestamp_field_count']==1
    result,_=receipt({'created':None,'status_transitions':{'paid_at':None}})
    assert not result['process_audit_events'] and result['source_state']=='paid'
    result,_=receipt({'status_transitions':{'finalized_at':CREATED,'paid_at':CREATED}})
    report=stripe_selected_invoices_process([result])
    assert report['process']['input_rows']==3
    assert report['process']['data_quality']['cases_with_tied_timestamps']==1
    assert report['process']['metrics']['median_case_elapsed_seconds']==0
    assert report['process']['cases'][0]['tied_timestamp_order_verified'] is False
    selected=stripe_selected_invoices_process([None,result])
    assert selected['selections']==[{'selection_number':1,'case_label':None,'projected_event_count':0},
        {'selection_number':2,'case_label':'Case 1','projected_event_count':3}]


def test_account_and_record_identity_are_checked_before_exposing_any_lifecycle():
    result,calls=receipt(wrong_account=True)
    assert len(calls)==1 and result['availability']=='unknown' and 'process_audit_events' not in result
    result,calls=receipt(wrong_record=True)
    assert len(calls)==2 and result['availability']=='unknown' and 'process_audit_events' not in result
    for kind,host,provider in [('refund','dashboard.stripe.com','stripe'),('invoice','github.com','stripe'),('issue','owned.atlassian.net','jira')]:
        raw=[{'event_id':'owned','observed_at':'2026-10-20T10:00:00Z','metadata':{'resource_reference':{
            'resource_ref':REF,'provider':provider,'resource_kind':kind,'host':host}}}]
        out=record_graph(REF,_raw_events=raw,include_source_status=True,include_source_lifecycle=True,
            required_source_type=('stripe','invoice','dashboard.stripe.com'),source_reader=lambda *args,**kwargs:
                (_ for _ in ()).throw(AssertionError('Wrong provider/kind must not read')))
        assert out['source_evidence']['availability']=='unknown'


def test_record_graph_default_does_not_request_source_history_but_explicit_invoice_projection_does():
    raw=[{'event_id':'owned','observed_at':'2026-10-20T10:00:00Z','metadata':{'resource_reference':{
        'resource_ref':REF,'provider':'stripe','resource_kind':'invoice','host':'dashboard.stripe.com'}}}]
    calls=[]
    def source(ref,**kwargs):calls.append((ref,kwargs));return {'availability':'source_reported'}
    record_graph(REF,_raw_events=raw,include_source_status=True,source_reader=source)
    assert calls==[(REF,{})]
    record_graph(REF,_raw_events=raw,include_source_status=True,include_source_lifecycle=True,source_reader=source)
    assert calls[-1]==(REF,{'include_lifecycle':True})
    calls.clear()
    record_graph(REF,_raw_events=[],include_source_status=True,include_source_lifecycle=True,source_reader=source)
    assert not calls


def test_invoice_projection_api_auth_strict_body_and_type_scope_before_reads(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
import server.record_graph as graph
calls=[]
def fake(ref,**kwargs):
    calls.append((ref,kwargs));return {'source_evidence':None}
graph.record_graph=fake
path='/v1/process-mining/stripe-selected-invoices'
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
body={'resource_refs':['owg:r:'+'a'*32],'confirm_source_reads':True}
with TestClient(app) as client:
    assert client.post(path,json=body).status_code==401
    assert client.post(path,json=body,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code==403
    assert client.post(path,json={**body,'confirm_source_reads':False},headers=headers).status_code==403
    for refs in ([],['invoice:in_PRIVATE'],['owg:r:'+'a'*32]*2,[{}]):
        assert client.post(path,json={**body,'resource_refs':refs},headers=headers).status_code==422
    assert client.post(path,content=b'x'*2049,headers={**headers,'Content-Type':'application/json'}).status_code==413
    assert client.post(path,content=b'{"resource_refs":[],"resource_refs":[],"confirm_source_reads":true}',headers={**headers,'Content-Type':'application/json'}).status_code==422
    assert not calls
    result=client.post(path,json=body,headers=headers)
    assert result.status_code==200,result.text
    assert result.headers['cache-control']=='no-store'
    assert calls==[(body['resource_refs'][0],{'include_source_status':True,'include_source_lifecycle':True,
        'required_source_type':('stripe','invoice','dashboard.stripe.com')})]
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
