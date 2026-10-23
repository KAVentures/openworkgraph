"""Reviewed exact shared-key aliases preserve source provenance without inferred joins."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import csv
import json
import os
import sqlite3
import subprocess
import sys

import pytest
from server.process_mining_csv import mine_csv_events, parse_csv_event_records, ProcessMiningCSVError
from server.process_log_store import ProcessLogStore
from shared.process_model import ProcessModel

RAW=b'''case_id,activity,timestamp,origin,source
case_key_7,Open,2026-10-20T00:00:00Z,source_1,PRIVATE_SOURCE
case_key_1,Open,2026-10-20T00:03:00Z,source_1,PRIVATE_SOURCE
case_key_7,Close,2026-10-20T00:02:00Z,source_1,PRIVATE_SOURCE
case_key_3,Waiting,2026-10-20T00:00:30Z,source_2,PRIVATE_SOURCE
case_key_7,Review,2026-10-20T00:01:00Z,source_2,PRIVATE_SOURCE
'''


@pytest.fixture
def logs(tmp_path):
    clock=[datetime(2026,10,23,tzinfo=timezone.utc)]
    @contextmanager
    def connect():
        conn=sqlite3.connect(tmp_path/'logs.db',timeout=10);conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()
    return ProcessLogStore(connect,now=lambda:clock[0]),connect,clock


def model():
    return ProcessModel(goal='Handle reviewed case',baseline_source='user_declared',baseline_path=['Open','Review','Close'],
        activities=[{'id':'open','label':'Open'},{'id':'review','label':'Review'},{'id':'close','label':'Close'}],
        transitions=[{'source':'open','target':'review'},{'source':'review','target':'close'}],
        entry_activity_id='open',terminal_activity_ids=['close'])


def test_exact_supplied_keys_preserve_interleaved_histories_source_support_and_unknown_authority():
    result=mine_csv_events(RAW,case_links_authorized=True)
    assert result['case_count']==3 and result['input_rows']==5
    assert result['case_identity_basis']=='caller_reviewed_literal_shared_case_key'
    assert not result['authoritative'] and not result['cross_app_identity_inferred']
    c=result['case_correlation']
    assert c['multi_source_case_count']==1 and c['source_count']==2
    assert c['authoritative_case_namespace_confirmed_by_caller'] is True
    assert not c['case_identity_independently_verified'] and not c['source_origins_independently_verified']
    assert not c['anonymization_verified'] and c['alias_source']=='client_declared_import_scoped_aliases'
    assert c['sources']==[{'origin':'source_1','event_count':3,'case_count':2},
                          {'origin':'source_2','event_count':2,'case_count':2}]
    first=result['cases'][0]
    assert first['case']=='Case 1' and first['case_key_alias']=='case_key_7'
    assert first['path']==['Open','Review','Close']
    assert [event['origin'] for event in first['events']]==['source_1','source_2','source_1']
    assert first['origins']==['source_1','source_2']
    assert 'PRIVATE_SOURCE' not in json.dumps(result)
    # Similar times and common activities do not join the other declared case keys.
    assert [case['case_key_alias'] for case in result['cases']]==['case_key_7','case_key_3','case_key_1']
    assert result['business_outcomes_verified'] is False and result['agent_permissions_granted'] is False


def test_review_is_literal_and_native_keys_or_origin_values_are_rejected_without_echo():
    for reviewed in (False,1,'true',None):
        with pytest.raises(ProcessMiningCSVError):mine_csv_events(RAW,case_links_authorized=reviewed)
    for raw in (RAW.replace(b'case_key_7',b'PRIVATE_CUSTOMER_ORDER'),
                RAW.replace(b'source_1',b'PRIVATE_PROVIDER_ACCOUNT'),
                RAW.replace(b'source_2',b'source_5'),RAW.replace(b'case_key_7',b'case_key_1001'),
                b'case_id,activity,timestamp\ncase_key_1,Open,2026-10-20T00:00:00Z\n'):
        with pytest.raises(ProcessMiningCSVError) as exc:mine_csv_events(raw,case_links_authorized=True)
        assert 'PRIVATE_' not in str(exc.value)
    # Structural callers retain the original six-field parser contract and output.
    simple=b'case_id,activity,timestamp,source\nPRIVATE_CASE,Open,2026-10-20T00:00:00Z,PRIVATE_SOURCE\n'
    rows,dims=parse_csv_event_records(simple)
    assert len(rows[0])==6 and not dims
    result=mine_csv_events(simple)
    assert 'case_correlation' not in result and 'case_key_alias' not in result['cases'][0]
    assert 'origin' not in result['cases'][0]['events'][0]


def test_retention_reconstruction_cohorts_and_complete_event_queries_preserve_alias_identity(logs):
    store,connect,clock=logs
    saved=store.save(RAW,retention_days=7,confirmed=True,case_links_authorized=True);identifier=saved['dataset_id']
    assert saved['case_key_aliases_retained'] and saved['source_origin_aliases_retained']
    assert saved['native_case_ids_retained'] is False and saved['source_values_retained'] is False
    cases=store.cases(identifier)['cases']
    assert [case['case_key_alias'] for case in cases]==['case_key_7','case_key_3','case_key_1']
    assert [event['origin'] for event in store.events(identifier,1)['events']]==['source_1','source_2','source_1']
    before=store.report(identifier)
    reference={'workflow_id':'reviewed','revision':3,'content_sha256':'a'*64}
    rebuilt=store.report(identifier,reference_model=model(),reference=reference)
    assert rebuilt['case_correlation']==before['case_correlation']
    assert rebuilt['cases'][0]['case_key_alias']=='case_key_7'
    assert rebuilt['conformance']['exact_structural_match_cases']==1
    cohort=store.cohort(identifier,contains_activity='Review',reference_model=model(),reference=reference)['report']
    assert cohort['case_count']==1 and cohort['input_rows']==3
    assert cohort['cases'][0]['case']=='Case 1' and cohort['cases'][0]['case_key_alias']=='case_key_7'
    assert cohort['case_correlation']['multi_source_case_count']==1
    assert [event['origin'] for event in cohort['cases'][0]['events']]==['source_1','source_2','source_1']
    only=store.cohort(identifier,contains_activity='Waiting')['report']
    assert only['cases'][0]['case']=='Case 2' and only['cases'][0]['case_key_alias']=='case_key_3'
    assert only['case_correlation']['source_count']==1 and only['case_correlation']['multi_source_case_count']==0
    assert len(store.cases(identifier,transition_from='Open',transition_to='Review')['cases'])==1
    comparison=store.compare_cohorts(identifier,baseline_filters={},current_filters={'contains_activity':'Review'},comparability_confirmed=True)
    assert comparison['current']['event_count']==3 and comparison['cohort_overlap']['shared_case_count']==1
    with connect() as conn:
        assert 'PRIVATE_' not in '\n'.join(conn.iterdump())
    clock[0]+=timedelta(days=7)
    with pytest.raises(LookupError):store.events(identifier,1)
    store.prune()  # Existing history-retention scheduler owns committed expiry deletion.
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM process_log_events').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM process_log_cases').fetchone()[0]==0


def test_reopened_aliases_beyond_preview_caps_and_legacy_upgrade(logs):
    store,connect,_=logs
    out=StringIO(newline='');writer=csv.writer(out);writer.writerow(('case_id','activity','timestamp','origin'))
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    for number in range(1,41):
        for offset,activity,origin in ((0,'Open','source_1'),(1,'Close','source_2')):
            writer.writerow(('case_key_'+str(number),activity,(start+timedelta(minutes=number,seconds=offset)).isoformat(),origin))
    identifier=store.save(out.getvalue().encode(),retention_days=7,confirmed=True,case_links_authorized=True)['dataset_id']
    page=store.cases(identifier,after=35)
    assert [row['case_key_alias'] for row in page['cases']]==['case_key_'+str(n) for n in range(36,41)]
    cohort=store.cohort(identifier,started_since=(start+timedelta(minutes=36)).isoformat())['report']
    assert [row['case_key_alias'] for row in cohort['cases']]==['case_key_'+str(n) for n in range(36,41)]
    assert cohort['cases'][0]['case']=='Case 36' and cohort['case_correlation']['multi_source_case_count']==5
    store.forget(identifier,confirmed=True)
    simple=b'case_id,activity,timestamp\nPRIVATE_CASE,Open,2026-10-20T00:00:00Z\n'
    identifier=store.save(simple,retention_days=7,confirmed=True)['dataset_id'];original=store.report(identifier)
    with connect() as conn:
        conn.execute('ALTER TABLE process_log_events DROP COLUMN origin')
        conn.execute('ALTER TABLE process_log_cases DROP COLUMN case_key_alias')
    store.inventory();store.inventory()
    assert store.report(identifier)==original
    assert 'case_key_alias' not in store.cases(identifier)['cases'][0]
    assert 'origin' not in store.events(identifier,1)['events'][0]


def test_dashboard_review_header_before_private_model_read_and_independent_retention(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
import server.workflow_evidence_routes as routes
raw=b'case_id,activity,timestamp,origin\ncase_key_7,Open,2026-10-20T00:00:00Z,source_1\ncase_key_7,Close,2026-10-20T00:01:00Z,source_2\n'
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session(),'Content-Type':'text/csv'}
reviewed={**headers,'X-OWG-Shared-Case-Keys-Reviewed':'true'}
token=local_auth.ensure_api_token();ai={'Authorization':'Bearer '+token,'X-OWG-Shared-Case-Keys-Reviewed':'true','Content-Type':'text/csv'}
with TestClient(app) as client:
 old=routes._catalog_record
 def forbidden(*a,**k):raise AssertionError('Unauthorized rich request read a private model')
 routes._catalog_record=forbidden
 try:
  assert client.post('/v1/process-mining/preview?workflow_id=private&revision=1',headers=ai,content=raw).status_code==403
 finally:routes._catalog_record=old
 assert client.post('/v1/process-mining/preview',headers=headers,content=raw).status_code==422
 for value in ('True','1','false'):
  assert client.post('/v1/process-mining/preview',headers={**headers,'X-OWG-Shared-Case-Keys-Reviewed':value},content=raw).status_code==422
 response=client.post('/v1/process-mining/preview',headers=reviewed,content=raw)
 assert response.status_code==200,response.text
 assert response.headers['cache-control']=='no-store'
 assert response.json()['case_count']==1 and response.json()['case_correlation']['multi_source_case_count']==1
 assert not routes._process_logs.inventory()['datasets']
 assert client.post('/v1/process-mining/datasets?retention_days=7',headers=reviewed,content=raw).status_code==403
 assert client.post('/v1/process-mining/datasets?retention_days=7',headers={**headers,'X-OWG-Retention-Confirmed':'true'},content=raw).status_code==422
 saved=client.post('/v1/process-mining/datasets?retention_days=7',headers={**reviewed,'X-OWG-Retention-Confirmed':'true'},content=raw)
 assert saved.status_code==200,saved.text
 id=saved.json()['dataset_id']
 cases=client.get('/v1/process-mining/datasets/cases',params={'dataset_id':id},headers=headers)
 assert cases.status_code==200 and cases.json()['cases'][0]['case_key_alias']=='case_key_7'
 assert cases.headers['cache-control']=='no-store'
 events=client.get('/v1/process-mining/datasets/events',params={'dataset_id':id,'case_number':1},headers=headers)
 assert [row['origin'] for row in events.json()['events']]==['source_1','source_2']
 assert client.get('/v1/process-mining/datasets/cases',params={'dataset_id':id},headers=ai).status_code==403
 assert client.post('/v1/process-mining/datasets/forget',params={'dataset_id':id},headers={**headers,'X-OWG-Deletion-Confirmed':'true'}).status_code==200
 assert not routes._process_logs.inventory()['datasets']
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


def test_source_timestamp_ties_and_duplicate_events_do_not_establish_order_or_event_equivalence():
    raw=b'case_id,activity,timestamp,origin\ncase_key_1,Open,2026-10-20T00:00:00Z,source_1\ncase_key_1,Open,2026-10-20T00:00:00Z,source_2\ncase_key_1,Close,2026-10-20T00:01:00Z,source_2\n'
    result=mine_csv_events(raw,case_links_authorized=True)
    assert result['input_rows']==3 and result['cases'][0]['event_count']==3
    assert result['cases'][0]['path']==['Open','Open','Close']
    assert result['cases'][0]['tied_timestamp_adjacent_pairs']==1
    assert not result['cases'][0]['tied_timestamp_order_verified']
    assert result['data_quality']['same_case_activity_timestamp_repeats']==1
    assert [row['origin'] for row in result['cases'][0]['events']]==['source_1','source_2','source_2']
