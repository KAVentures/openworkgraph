"""Exact selected-ticket source reads with bounded minimized status projection."""
import asyncio
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from server.zendesk_process_preview import minimize_ticket_audits,project_ticket_audits,zendesk_selected_cases_process
from server.zendesk_process_source import read_selected_zendesk_process

ENV={'OWG_ZENDESK_PROCESS_LOGS_ENABLED':'true','OWG_ZENDESK_SUBDOMAIN':'owned-example',
     'OWG_ZENDESK_OAUTH_TOKEN':'SYNTHETIC_PRIVATE_TOKEN'}


def audit(identifier=101,*,ticket=17,value='open',kind='Change',stamp='2026-10-20T10:00:00Z'):
    return {'id':identifier,'ticket_id':ticket,'created_at':stamp,'author_id':777,'metadata':{'ip':'PRIVATE_IP'},
        'events':[{'id':909,'type':kind,'field_name':'status','value':value,'previous_value':'PRIVATE_PREVIOUS'},
                  {'type':'Comment','body':'PRIVATE_COMMENT','author_id':777},
                  {'type':'Change','field_name':'PRIVATE_CUSTOM_FIELD','value':'PRIVATE_CUSTOM_VALUE'}]}


def read(ids,handler,env=None,confirmed=True):
    calls=[]
    async def dispatch(request):
        calls.append(request)
        return handler(request)
    def factory(**options):
        assert options=={'timeout':10,'follow_redirects':False}
        return httpx.AsyncClient(transport=httpx.MockTransport(dispatch),**options)
    result=asyncio.run(read_selected_zendesk_process(ids,confirmed=confirmed,environ=ENV if env is None else env,client_factory=factory))
    return result,calls


def test_fixed_host_selected_path_oauth_filtered_reads_and_actual_membership_projection():
    def handler(request):
        assert request.method=='GET' and request.url.host=='owned-example.zendesk.com'
        assert request.headers['Authorization']=='Bearer SYNTHETIC_PRIVATE_TOKEN'
        assert request.url.params.get_list('filter_events[]')==['Create','Change']
        assert request.url.params['sort_order']=='asc'
        ticket=int(request.url.path.split('/')[-2])
        assert ticket in {17,19}
        return httpx.Response(200,json={'audits':[audit(ticket*10,ticket=ticket,kind='Create',value='new'),
            audit(ticket*10+1,ticket=ticket,value='solved',stamp='2026-10-20T10:05:00Z')],'next_page':None})
    result,calls=read(['17','19'],handler)
    assert len(calls)==2
    assert result['process']['case_count']==2 and result['process']['input_rows']==4
    assert result['process']['variants'][0]['observed_path']==['Created New','Status Solved']
    assert result['process']['metrics']['median_case_elapsed_seconds']==300
    assert all(case['provider_pagination_complete'] for case in result['selections'])
    assert result['process']['source_case_identity_basis']=='selected_ticket_id_verified_on_every_audit'
    assert result['process']['complete_business_case_history'] is False
    assert result['result_persisted'] is False and result['business_outcomes_verified'] is False
    serialized=json.dumps(result)
    assert 'PRIVATE_' not in serialized and 'owned-example' not in serialized
    assert '777' not in serialized and '909' not in serialized


def test_pagination_is_reconstructed_bounded_and_never_follows_a_link_target():
    def handler(request):
        page=int(request.url.params['page'])
        return httpx.Response(200,json={'audits':[audit(page)],
          'next_page':f'https://owned-example.zendesk.com/api/v2/tickets/17/audits?page={page+1}'})
    result,calls=read(['17'],handler)
    assert len(calls)==3
    assert result['selections'][0]['provider_pagination_complete'] is False
    assert result['process']['input_rows']==3
    for target in ['https://invalid.example/steal?page=2','http://owned-example.zendesk.com/api/v2/tickets/17/audits?page=2',
                   'https://user:secret@owned-example.zendesk.com/api/v2/tickets/17/audits?page=2',
                   'https://owned-example.zendesk.com/api/v2/tickets/99/audits?page=2',
                   'https://owned-example.zendesk.com/api/v2/tickets/17/audits?page=3',
                   'https://owned-example.zendesk.com/api/v2/tickets/17/audits?page=2#fragment']:
        result,calls=read(['17'],lambda _request:httpx.Response(200,json={'audits':[audit()],'next_page':target}))
        assert len(calls)==1 and result['process'] is None
        assert result['selections'][0]['availability']=='invalid_pagination_metadata'
    result,calls=read(['17'],lambda _request:httpx.Response(302,headers={'location':'https://invalid.example/steal'}))
    assert len(calls)==1 and result['process'] is None


def test_wrong_or_missing_native_ticket_identity_discards_the_entire_case():
    for ticket in (18,'17',True,None):
        result,calls=read(['17'],lambda _request:httpx.Response(200,json={'audits':[audit(),audit(102,ticket=ticket)],'next_page':None}))
        assert len(calls)==1 and result['process'] is None
        assert result['selections'][0]['availability']=='ticket_identity_mismatch'
    assert minimize_ticket_audits([audit(ticket=18)],17) is None
    def late_mismatch(request):
        page=int(request.url.params['page'])
        return httpx.Response(200,json={'audits':[audit(page,ticket=17 if page==1 else 19)],
            'next_page':'https://owned-example.zendesk.com/api/v2/tickets/17/audits?page=2' if page==1 else None})
    result,calls=read(['17'],late_mismatch)
    assert len(calls)==2 and result['process'] is None


def test_duplicates_conflicts_ambiguous_statuses_and_unknown_values_never_invent_events():
    first=audit();rows=[first,deepcopy(first),audit(102)]
    conflict=audit(103,value='solved');rows+=[conflict,audit(103,value='closed')]
    ambiguous=audit(104);ambiguous['events'].append({'type':'Change','field_name':'status','value':'pending'});rows.append(ambiguous)
    rows+=[audit(105,value='PRIVATE_UNKNOWN_STATUS'),audit(106,stamp='PRIVATE_INVALID_STAMP'),audit(True),
           audit(107,kind={'malformed':'type'})]
    projected=project_ticket_audits(rows,17)
    assert len(projected['events'])==2
    assert projected['duplicate_audit_count']==2 and projected['conflicting_audit_count']==1
    assert projected['ambiguous_status_audit_count']==1
    assert projected['invalid_audit_count']==3
    minimal=minimize_ticket_audits(rows,17)
    assert 'PRIVATE_' not in json.dumps(minimal)
    result=zendesk_selected_cases_process([{'availability':'source_audit','projection':projected}])
    assert result['process']['input_rows']==2
    assert result['process']['data_quality']['cases_with_tied_timestamps']==1
    assert result['process']['metrics']['repeated_activity_events']==1
    assert 'PRIVATE_' not in json.dumps(result)


def test_config_confirmation_validation_and_source_errors_make_no_unexpected_requests():
    for env in ({},{**ENV,'OWG_ZENDESK_PROCESS_LOGS_ENABLED':'false'},
                {**ENV,'OWG_ZENDESK_SUBDOMAIN':'https://evil.example'},
                {**ENV,'OWG_ZENDESK_OAUTH_TOKEN':'bad\r\nheader'},
                {**ENV,'OWG_ZENDESK_OAUTH_TOKEN':''}):
        result,calls=read(['17'],lambda _:pytest.fail('No request allowed'),env=env)
        assert not calls and result['process'] is None
    for ids in ([],['17','17'],['0'],['01'],['1/../../evil'],[17],[[]],['1']*9):
        with pytest.raises(ValueError):read(ids,lambda _:pytest.fail('Invalid selection must not read'))
    with pytest.raises(ValueError):read(['17'],lambda _:pytest.fail('No confirmation'),confirmed=False)
    for status,reason in [(401,'source_access_denied'),(403,'source_access_denied'),(429,'source_rate_limited')]:
        result,calls=read(['17','19'],lambda _:httpx.Response(status,json={'PRIVATE_ERROR':'PRIVATE_PROVIDER_TEXT'}))
        assert len(calls)==1 and result['availability']==reason and 'PRIVATE_' not in json.dumps(result)
    for response in (httpx.Response(200,content=b'x'*(512*1024+1)),
                     httpx.Response(200,content=b'{"audits":[],"audits":[]}'),
                     httpx.Response(200,content=b'{"audits":NaN}'),
                     httpx.Response(200,json={'audits':[audit()]*101}),httpx.Response(500,content=b'PRIVATE_ERROR')):
        result,calls=read(['17'],lambda _:response)
        assert len(calls)==1 and result['process'] is None and 'PRIVATE_' not in json.dumps(result)
    def connection_failure(request):raise httpx.ConnectError('PRIVATE_TOKEN and provider text',request=request)
    result,calls=read(['17'],connection_failure)
    assert len(calls)==1 and result['availability']=='source_request_failed'
    assert 'PRIVATE_' not in json.dumps(result)


def test_missing_pagination_and_unavailable_cases_remain_explicit_with_case_selection_mapping():
    def handler(request):
        ticket=int(request.url.path.split('/')[-2])
        if ticket==19:return httpx.Response(404,json={'error':'PRIVATE_ERROR'})
        return httpx.Response(200,json={'audits':[audit(ticket=ticket)]})
    result,calls=read(['19','17'],handler)
    assert len(calls)==2 and result['projected_ticket_count']==1
    assert result['selections'][0]['case_label'] is None
    assert result['selections'][1]['case_label']=='Case 1'
    assert result['selections'][1]['provider_pagination_complete'] is False


def test_source_transport_logging_does_not_disclose_selected_urls_and_other_logs_are_preserved(caplog):
    with caplog.at_level(logging.DEBUG):
        result,calls=read(['17'],lambda _:httpx.Response(200,json={'audits':[audit()],'next_page':None}))
        logging.getLogger('httpx').info('unrelated log preserved')
    assert result['process'] and len(calls)==1
    assert 'owned-example.zendesk.com' not in caplog.text and 'SYNTHETIC_PRIVATE_TOKEN' not in caplog.text
    assert 'unrelated log preserved' in caplog.text


def test_dashboard_endpoint_validates_before_source_reads_and_denies_ai_bearers(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth,zendesk_process_source as source
reads=[]
async def fake(ids,*,confirmed):
    reads.append((ids,confirmed))
    return {'format':'openworkgraph.zendesk-selected-cases-process.v1','availability':'connector_disabled','process':None}
source.read_selected_zendesk_process=fake
path='/v1/process-mining/zendesk-selected-cases'
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
body={'ticket_ids':['17'],'confirm_source_reads':True}
with TestClient(app) as client:
    assert client.post(path,json=body).status_code==401
    assert client.post(path,json=body,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code==403
    for payload in ({**body,'confirm_source_reads':False},{**body,'confirm_source_reads':'true'},{**body,'extra':'no'}):
        assert client.post(path,json=payload,headers=headers).status_code==403
    for ids in ([],['17','17'],['0'],['01'],[17],[[]],['17']*9):
        assert client.post(path,json={**body,'ticket_ids':ids},headers=headers).status_code==422
    assert client.post(path,content=b'x'*2049,headers={**headers,'Content-Type':'application/json'}).status_code==413
    assert client.post(path,content=b'{"ticket_ids":["17"],"ticket_ids":["19"],"confirm_source_reads":true}',headers={**headers,'Content-Type':'application/json'}).status_code==422
    assert client.post(path,content=b'{"ticket_ids":NaN,"confirm_source_reads":true}',headers={**headers,'Content-Type':'application/json'}).status_code==422
    assert not reads
    result=client.post(path,json=body,headers=headers)
    assert result.status_code==200,result.text
    assert result.headers['cache-control']=='no-store'
    assert reads==[(['17'],True)]
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
