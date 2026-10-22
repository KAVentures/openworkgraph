"""Fresh source-state rule comparisons retain no payloads and grant no execution."""
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from server.process_source_rule_checks import SourceRuleCheck,check_current_source_rules
from shared.process_model import ProcessModel
from test_process_rule_conditions import graph,packet,predicate

REF='owg:r:'+'a'*32
NOW=datetime(2026,10,22,12,tzinfo=timezone.utc)


def record(path='/source/state',expected='paid',operator='eq'):
    value=graph(True);value['business_rules'][0]['predicate']=predicate(operator,expected,path=path)
    return packet(ProcessModel.model_validate(value).model_dump())


def selection(source_type='stripe_invoice',**kw):
    return SourceRuleCheck(resource_ref=REF,source_type=source_type,**kw)


def source_graph(provider='stripe',kind='invoice',state='paid',**changes):
    methods={'stripe':'opted_in_restricted_key_stripe_account_and_record_get',
        'jira':'explicit_opt_in_site_verified_oauth_jira_issue_status_read',
        'github':'explicit_opt_in_authenticated_local_github_cli_read'}
    out={'resource_ref':REF,'record_observed':True,'record_type':{'provider':provider,'kind':kind},
        'record_identity_complete_within_retained_window':True,'source_event_limit_reached':False,
        'source_rows_scoped_to_exact_record':True,'timeline':[{'PRIVATE_EVENT_VALUE':'PRIVATE_RECORD'}],
        'source_evidence':{'provider':provider,'availability':'source_reported','source_state_reported':True,
            'source_state':state,'verified_at':NOW.isoformat(),'verification_method':methods[provider],
            'customer_email':'PRIVATE_SOURCE_CUSTOMER','native_id':'PRIVATE_NATIVE_ID'}}
    out.update(changes);return out


@pytest.mark.parametrize('kind,provider,record_kind,state,host',[
    ('stripe_invoice','stripe','invoice','paid','dashboard.stripe.com'),
    ('stripe_refund','stripe','refund','succeeded','dashboard.stripe.com'),
    ('github_pull_request','github','pull_request','merged','github.com'),
    ('jira_issue','jira','issue','done','owned.atlassian.net'),
])
def test_supported_read_is_pinned_to_canonical_type_and_returns_only_minimized_statuses(kind,provider,record_kind,state,host):
    calls=[]
    def reader(ref,**kwargs):calls.append((ref,kwargs));return source_graph(provider,record_kind,state)
    result=check_current_source_rules(record(expected=state),selection(kind),record_reader=reader,
        resolver=lambda ref:{'provider':'jira','resource_kind':'issue','host':host},now=NOW)
    assert calls==[(REF,{'include_source_status':True,'required_source_type':(provider,record_kind,host)})]
    assert result['rule_result_counts']=={'matched':1}
    assert result['facts_source_authoritativeness_verified'] is True
    assert result['source_state_verified_at']==NOW.isoformat()
    assert result['source_status_refresh_requested'] is True
    assert result['selected_record_reference']==REF
    assert state not in json.dumps(result) and 'PRIVATE_' not in json.dumps(result)
    for flag in ('case_association_verified','historical_case_facts_verified','business_policy_compliance_verified',
                 'observed_execution_conformance_verified','independent_outcomes_verified','execution_permissions_granted',
                 'generated_agent_source_fact_guard_resolved','model_provider_calls_made','facts_retained','comparison_report_retained','process_model_updated'):
        assert result[flag] is False


def test_unavailable_stale_conflicting_or_untrusted_source_never_satisfies_absence_predicates():
    variants=[None,source_graph(record_observed=False),source_graph(source_event_limit_reached=True),
        source_graph(record_identity_complete_within_retained_window=False),source_graph(source_rows_scoped_to_exact_record=False),
        source_graph(resource_ref='owg:r:'+'b'*32),source_graph(record_type={'provider':'stripe','kind':'refund'})]
    for changes in ({'availability':'unknown'},{'provider':'jira'},{'verification_method':'caller_claim'},
        {'object_type':'refund'},{'source_state_reported':False},{'source_state':'PRIVATE_CUSTOM_STATE'},
        {'verified_at':(NOW-timedelta(seconds=121)).isoformat()},{'verified_at':(NOW+timedelta(seconds=6)).isoformat()},
        {'verified_at':'2026-10-22T12:00:00'},{'verified_at':'PRIVATE_TIME'}):
        out=source_graph();out['source_evidence'].update(changes);variants.append(out)
    for out in variants:
        result=check_current_source_rules(record(operator='not_exists',expected=None),selection(),
            record_reader=lambda *a,**k:out,now=NOW)
        assert result['rule_result_counts']=={'unknown':1}
        assert result['facts_source_authoritativeness_verified'] is False
        assert 'PRIVATE_' not in json.dumps(result)
    def failure(*a,**kw):raise RuntimeError('PRIVATE_TOKEN_OR_URL')
    result=check_current_source_rules(record(),selection(),record_reader=failure,now=NOW)
    assert result['rule_result_counts']=={'unknown':1} and 'PRIVATE_' not in json.dumps(result)


def test_only_explicit_supported_facts_are_evaluated_with_three_valued_logic():
    value=record();value['process_model']['business_rules'][0]['predicate']={'match':'all','conditions':[
        {'path':'/source/state','operator':'eq','expected':'paid'},
        {'path':'/source/customer_email','operator':'not_exists','expected':None}]}
    result=check_current_source_rules(value,selection(),record_reader=lambda *a,**k:source_graph(),now=NOW)
    assert result['rule_result_counts']=={'unknown':1}
    assert result['cases'][0]['rules'][0]['conditions'][1]=={'status':'unknown','reason':'source_fact_path_not_supported'}
    value['process_model']['business_rules'][0]['predicate']['match']='any'
    assert check_current_source_rules(value,selection(),record_reader=lambda *a,**k:source_graph(),now=NOW)['rule_result_counts']=={'matched':1}
    value['process_model']['business_rules'][0]['predicate']['conditions'][0]['expected']='open'
    assert check_current_source_rules(value,selection(),record_reader=lambda *a,**k:source_graph(),now=NOW)['rule_result_counts']=={'unknown':1}


def test_unsupported_unconfirmed_off_route_and_invalid_route_do_not_trigger_source_reads():
    def forbidden(*a,**kw):pytest.fail('This selection must not read any provider')
    for value,selected in [(record(path='/business/approval',operator='not_exists',expected=None),selection()),
        (record(),selection(activity_ids=['unknown']))]:
        result=check_current_source_rules(value,selected,record_reader=forbidden,now=NOW)
        assert result['source_status_refresh_requested'] is False
    value=record();value['process_model']['business_rules'][0]['user_confirmed']=False
    result=check_current_source_rules(value,selection(),record_reader=forbidden,now=NOW)
    assert result['rule_result_counts']=={'not_evaluated_unconfirmed':1}
    assert not result['source_status_refresh_requested']
    value=record();value['process_model']['business_rules'][0].pop('predicate')
    assert check_current_source_rules(value,selection(),record_reader=forbidden,now=NOW)['rule_result_counts']=={'not_evaluated_unstructured':1}
    value=record(path='/business/approval');value['process_model']['business_rules'].append({
        'id':'off_route','text':'Source rule on another selection','user_confirmed':True,
        'predicate':predicate(expected='paid',path='/source/state')})
    off_route=check_current_source_rules(value,selection(activity_ids=['review','close']),record_reader=forbidden,now=NOW)
    assert off_route['rule_result_counts']=={'unknown':1,'not_on_proposed_route':1}
    result=check_current_source_rules(record(),selection('jira_issue'),resolver=lambda ref:None,record_reader=forbidden,now=NOW)
    assert result['rule_result_counts']=={'unknown':1} and not result['source_status_refresh_requested']
    for info in ({'provider':'jira','resource_kind':'issue','host':'api.atlassian.com'},
                 {'provider':'jira','resource_kind':'issue','host':'owned.atlassian.net.evil.example'}):
        assert not check_current_source_rules(record(),selection('jira_issue'),resolver=lambda ref:info,record_reader=forbidden,now=NOW)['source_status_refresh_requested']


def test_source_selection_accepts_no_facts_secrets_urls_or_ambiguous_refs():
    for value in ({'resource_ref':'PRIVATE_NATIVE_ID','source_type':'stripe_invoice'},
        {'resource_ref':REF,'source_type':'private_url'}, {'resource_ref':REF,'source_type':'stripe_invoice','facts':{}},
        {'resource_ref':REF,'source_type':'stripe_invoice','token':'PRIVATE_TOKEN'},
        {'resource_ref':REF,'source_type':'stripe_invoice','activity_ids':[1]}):
        with pytest.raises(ValidationError):SourceRuleCheck.model_validate(value)


def test_current_source_endpoint_uses_canonical_proof_real_receipt_and_no_persistence(tmp_path):
    code=r'''
from datetime import datetime,timezone
from fastapi.testclient import TestClient
import json,os,sqlite3
from pathlib import Path
from server.secure_app import app
from server import local_auth,db,record_graph,stripe_source_evidence
REF='owg:r:'+'a'*32;UNSEEN='owg:r:'+'c'*32;ID='in_1234567890ABCDEF';ACCOUNT='acct_1234567890ABCDEF'
calls=[]
env={'OWG_STRIPE_SOURCE_READ_ENABLED':'true','OWG_STRIPE_ACCOUNT_ID':ACCOUNT,'OWG_STRIPE_RESTRICTED_READ_KEY':'rk_test_synthetic_sample_only'}
def reader(url,key):
    assert key==env['OWG_STRIPE_RESTRICTED_READ_KEY'];calls.append(url)
    if url=='https://api.stripe.com/v1/account':return {'object':'account','id':ACCOUNT}
    assert url=='https://api.stripe.com/v1/invoices/'+ID
    return {'object':'invoice','id':ID,'status':'paid','paid':True,'customer_email':'PRIVATE_SOURCE_CUSTOMER','amount_due':99999999}
def resolve(ref):return {'provider':'stripe','resource_kind':'invoice','host':'dashboard.stripe.com','resolver_locator':'invoice:'+ID} if ref==REF else None
production=stripe_source_evidence.stripe_record_receipt
record_graph.stripe_record_receipt=lambda ref:production(ref,environment=env,resolver=resolve,reader=reader)
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
model={'goal':'Check selected case','baseline_source':'user_declared','baseline_path':['Review','Close'],
    'activities':[{'id':'review','label':'Review'},{'id':'close','label':'Close'}],
    'transitions':[{'source':'review','target':'close','rule_id':'evidence'}],
    'business_rules':[{'id':'evidence','text':'Source state compared','user_confirmed':True,
        'predicate':{'match':'all','conditions':[{'path':'/source/state','operator':'eq','expected':'paid'}]}}],
    'entry_activity_id':'review','terminal_activity_ids':['close']}
with TestClient(app) as client:
    os.environ['WORKFLOW_OBSERVER_RUN_STARTED_AT']=datetime.now(timezone.utc).isoformat()
    stamp=datetime.now(timezone.utc).isoformat()
    db.insert_events([{'event_id':'owned-source-proof','observed_at':stamp,'device_id':'synthetic',
        'session_id':'synthetic','app':'Google Chrome','window_title':'Owned workflow','event_type':'browser_click',
        'source':'browser_extension','metadata':{'resource_reference':{'provider':'stripe','resource_kind':'invoice',
            'host':'dashboard.stripe.com','resource_ref':REF}}}])
    assert client.post('/v1/process-catalog',headers=headers,json={'workflow_id':'source_checks','title':'Source checks',
        'process_model':model,'user_confirmed':True}).status_code==200
    model['business_rules'][0]['predicate']['conditions'][0]['expected']='void'
    assert client.post('/v1/process-catalog',headers=headers,json={'workflow_id':'source_checks','title':'New source checks',
        'process_model':model,'user_confirmed':True,'expected_revision':1}).status_code==200
    path='/v1/process-catalog/check-current-source-rules?workflow_id=source_checks&revision=1'
    body={'resource_ref':REF,'source_type':'stripe_invoice'}
    assert client.post(path,json=body).status_code==401
    assert client.post(path,json=body,headers={'Authorization':'Bearer '+local_auth.ensure_api_token(),'X-OWG-Source-Rule-Read-Authorized':'true'}).status_code==403
    assert client.post(path,json=body,headers=headers).status_code==403
    assert calls==[]
    auth={**headers,'X-OWG-Source-Rule-Read-Authorized':'true'}
    for invalid in ({**body,'facts':{'state':'paid'}},{**body,'source_type':'https://PRIVATE_TARGET'},
                   {**body,'token':'PRIVATE_SECRET'}):
        response=client.post(path,json=invalid,headers=auth);assert response.status_code==422 and 'PRIVATE_' not in response.text
    assert client.post(path,content=b'x'*4097,headers={**auth,'Content-Type':'application/json'}).status_code==413
    assert client.post(path,content=b'{"resource_ref":1,"resource_ref":2}',headers={**auth,'Content-Type':'application/json'}).status_code==422
    assert client.post(path.replace('revision=1','revision=9'),json=body,headers=auth).status_code==404
    assert calls==[]
    response=client.post(path,json={**body,'activity_ids':['missing']},headers=auth)
    assert response.status_code==200 and response.json()['source_status_refresh_requested'] is False and not calls
    for params in ({**body,'resource_ref':UNSEEN},{**body,'source_type':'stripe_refund'}):
        result=client.post(path,json=params,headers=auth).json()
        assert result['rule_result_counts']=={'unknown':1} and not calls
    database=Path(os.environ['WORKFLOW_OBSERVER_DATA'])/'workflow_observer.db'
    with sqlite3.connect(database) as conn:before='\n'.join(conn.iterdump())
    response=client.post(path,json=body,headers=auth)
    assert response.status_code==200,response.text
    result=response.json();assert response.headers['cache-control']=='no-store'
    assert result['rule_result_counts']=={'matched':1} and result['facts_source_authoritativeness_verified'] is True
    assert result['process_reference']['revision']==1 and result['latest_revision_at_check']==2
    assert result['references_latest_revision_at_check'] is False
    assert result['case_association_verified'] is False and result['generated_agent_source_fact_guard_resolved'] is False
    assert calls==['https://api.stripe.com/v1/account','https://api.stripe.com/v1/invoices/'+ID]
    for value in ('PRIVATE_',ID,ACCOUNT,'99999999','paid'):assert value not in response.text,value
    with sqlite3.connect(database) as conn:assert '\n'.join(conn.iterdump())==before
    state_path='/v1/record-graph/state-receipt'
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    params={**body,'confirm_source_read':'true'}
    count=len(calls)
    assert client.get(state_path,params=params).status_code==401
    assert client.get(state_path,params=body,headers=bearer).status_code==403
    assert client.get(state_path,params={**params,'source_type':'PRIVATE_INVALID'},headers=bearer).status_code==422
    assert len(calls)==count
    response=client.get(state_path,params=params,headers=bearer)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    receipt=response.json()
    assert receipt['format']=='openworkgraph.source-state-receipt.v1'
    assert receipt['source_state_verified'] and receipt['provider_reported_state']=='paid',receipt
    assert receipt['case_association_verified'] is False and receipt['agent_execution_authorized'] is False
    assert len(calls)==count+2
    for value in ('PRIVATE_',ID,ACCOUNT,'99999999'):assert value not in response.text
    assert client.get(state_path,params={**params,'resource_ref':UNSEEN},headers=bearer).json()['source_state_verified'] is False
    assert len(calls)==count+2
    from shared.history_policy import set_ai_history_access
    from datetime import timedelta
    set_ai_history_access(mode='selected_range',since=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),
        until=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
    assert client.get(state_path,params={**params,'since':'2000-01-01T00:00:00Z'},headers=bearer).json()['source_state_verified'] is False
    assert len(calls)==count+2
    set_ai_history_access(mode='all_saved')
    assert client.get(state_path,params=params,headers=bearer).json()['source_state_verified'] is True
    assert len(calls)==count+4
    from server.ai_access import set_ai_access
    set_ai_access(False)
    assert client.get(state_path,params=params,headers=bearer).status_code==403
    assert len(calls)==count+4
    with sqlite3.connect(database) as conn:assert '\n'.join(conn.iterdump())==before
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('source_type',['jira_issue','github_pull_request','stripe_refund'])
def test_existing_production_adapter_receipts_match_the_source_fact_contract(monkeypatch,source_type):
    if source_type=='jira_issue':
        from server.jira_source_evidence import jira_issue_receipt
        from test_jira_source_evidence_issue226 import ENV,INFO,_reader
        reader,calls=_reader()
        receipt=jira_issue_receipt(REF,environment=ENV,resolver=lambda _:INFO,reader=reader)
        assert calls and all(url.startswith('https://api.atlassian.com/') for url,_params,_token in calls)
        provider,kind,state='jira','issue','done';resolver=lambda _:INFO
    elif source_type=='github_pull_request':
        from types import SimpleNamespace
        from server import record_graph as module
        monkeypatch.setattr(module.outcome_tracker,'enabled',lambda:True)
        monkeypatch.setattr(module,'resolve_resource_reference',lambda _:{'provider':'github','resource_kind':'pull_request',
            'host':'github.com','resolver_locator':'owned/repo/pull/12'})
        calls=[]
        def runner(command,**kwargs):
            calls.append(command);return SimpleNamespace(returncode=0,stdout=json.dumps({'state':'MERGED',
                'mergedAt':'2026-10-15T12:00:00Z','closedAt':None,'reviews':[]}))
        receipt=module._gh_pr_snapshot(REF,runner=runner,which=lambda _:'/owned/gh')
        assert calls[0][1:3]==['pr','view']
        provider,kind,state='github','pull_request','merged';resolver=lambda _:None
    else:
        from server.stripe_source_evidence import stripe_record_receipt
        from test_issue226_stripe_source_evidence import BASE,ACCOUNT
        refund='re_1234567890ABCDEF';calls=[]
        def reader(url,key):
            calls.append(url)
            if url.endswith('/account'):return {'object':'account','id':ACCOUNT}
            assert url=='https://api.stripe.com/v1/refunds/'+refund
            return {'object':'refund','id':refund,'status':'succeeded','amount':99999999}
        receipt=stripe_record_receipt(REF,environment=BASE,resolver=lambda _:{'provider':'stripe','resource_kind':'refund',
            'host':'dashboard.stripe.com','resolver_locator':'refund:'+refund},reader=reader)
        assert len(calls)==2
        provider,kind,state='stripe','refund','succeeded';resolver=lambda _:None
    assert receipt['availability']=='source_reported'
    body=source_graph(provider,kind,state);body['source_evidence']=receipt
    result=check_current_source_rules(record(expected=state),selection(source_type),record_reader=lambda *a,**k:body,resolver=resolver)
    assert result['rule_result_counts']=={'matched':1} and result['facts_source_authoritativeness_verified'] is True
    assert 'PRIVATE_' not in json.dumps(result) and state not in json.dumps(result)


def test_current_refresh_suppresses_only_scoped_source_http_logs(caplog):
    import logging
    caplog.set_level(logging.WARNING,logger='httpx')
    def reader(*a,**kw):
        logging.getLogger('httpx').warning('PRIVATE_SELECTED_SOURCE_URL')
        return source_graph()
    check_current_source_rules(record(),selection(),record_reader=reader,now=NOW)
    logging.getLogger('httpx').warning('Unrelated request remains visible')
    assert 'PRIVATE_SELECTED_SOURCE_URL' not in caplog.text
    assert 'Unrelated request remains visible' in caplog.text
