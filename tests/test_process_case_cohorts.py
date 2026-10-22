"""Retained log cohorts select complete cases, with stable snapshot identity."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
import csv
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from server.process_log_store import ProcessLogError, ProcessLogStore
from shared.process_model import ProcessModel


@pytest.fixture
def logs(tmp_path):
    @contextmanager
    def connect():
        conn=sqlite3.connect(tmp_path/'logs.db',timeout=10)
        conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()
    return ProcessLogStore(connect),connect


def retain(store,cases):
    stream=StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(('case_id','activity','timestamp','source'))
    for n,(start,path,step_seconds) in enumerate(cases):
        for p,activity in enumerate(path):
            writer.writerow((f'PRIVATE_CASE_{n:04d}',activity,(start+timedelta(seconds=p*step_seconds)).isoformat(),'PRIVATE_SOURCE'))
    return store.save(stream.getvalue().encode(),retention_days=7,confirmed=True)['dataset_id']


def test_whole_case_dates_utc_boundaries_and_independent_activity_filters(logs):
    store,connect=logs
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    identifier=retain(store,[
        (start-timedelta(seconds=1),('Open','Review','Close'),7200),
        (start,('Open','Review','Close'),7200),
        (start+timedelta(hours=1),('Open','Close'),7200),
        (start+timedelta(hours=2),('Open','Review','Close'),7200)])
    with connect() as conn:before='\n'.join(conn.iterdump())
    result=store.cohort(identifier,started_since='2026-10-20T02:00:00+02:00',
        started_before='2026-10-20T02:00:00Z',contains_activity='Review')
    assert result['selection']['selected_case_count']==1
    assert result['selection']['selected_event_count']==3
    assert result['selection']['filters']['started_since']=='2026-10-20T00:00:00+00:00'
    assert result['selection']['all_events_of_selected_cases_included']
    assert not result['selection']['events_clipped_to_time_window']
    case=result['report']['cases'][0]
    assert case['case']=='Case 2' and case['duration_seconds']==14400
    assert case['events'][-1]['observed_at']=='2026-10-20T04:00:00+00:00'
    # Drill-down activity is an additional AND, preserving the original cohort.
    query=result['selection']['filters']
    page=store.cases(identifier,**query,activity='Close')
    assert [row['case_number'] for row in page['cases']]==[2]
    assert not store.cases(identifier,**query,activity='Absent')['cases']
    assert 'PRIVATE_' not in json.dumps(result)
    assert result['selection_retained'] is False
    with connect() as conn:assert '\n'.join(conn.iterdump())==before


def test_filtered_rank_changes_preserve_case_and_variant_identity_beyond_samples(logs):
    store,_=logs
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    first=('Open','Review','Close');second=('Open','Escalate','Close')
    # Overall variant_1=Review (35 cases), variant_2=Escalate (5).
    # Later cohort reverses frequency and includes original cases after #30.
    identifier=retain(store,[(start+timedelta(minutes=i),first,1) for i in range(35)]+
        [(start+timedelta(minutes=i),second,2) for i in range(35,40)])
    result=store.cohort(identifier,started_since=(start+timedelta(minutes=34)).isoformat())
    report=result['report']
    assert result['selection']['selected_case_count']==6
    assert report['variants'][0]['variant_id']=='variant_2'
    assert report['variants'][1]['variant_id']=='variant_1'
    assert [row['case'] for row in report['cases']]==[f'Case {n}' for n in range(35,41)]
    assert report['cases'][0]['variant_id']=='variant_1'
    assert all(row['variant_id']=='variant_2' for row in report['variants'][0]['case_examples'])
    assert report['variants'][1]['case_examples'][0]['case']=='Case 35'
    assert store.cases(identifier,**result['selection']['filters'],limit=2)['next_after']==36
    assert [row['case_number'] for row in store.cases(identifier,**result['selection']['filters'],after=36)['cases']]==[37,38,39,40]
    assert store.events(identifier,40)['events'][1]['activity']=='Escalate'
    assert store.cohort(identifier,variant_id='variant_2')['selection']['selected_case_count']==5
    assert store.report(identifier)['variants'][0]['variant_id']=='variant_1'


def test_filters_preserve_complete_long_paths_all_events_and_structural_comparison(logs):
    store,_=logs
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    prefix=('Open',)*30
    cases=[(start+timedelta(minutes=i),prefix+('Review','Close'),1) for i in range(35)]
    identifier=retain(store,cases)
    graph=ProcessModel(goal='Resolve selected case',baseline_source='user_declared',baseline_path=['Open','Review','Close'],
        activities=[{'id':'o','label':'Open'},{'id':'d','label':'Continue','kind':'decision'},
                    {'id':'r','label':'Review'},{'id':'c','label':'Close'}],
        business_rules=[{'id':'repeat','text':'Continue repeated step','user_confirmed':True},
                        {'id':'done','text':'Continue to review','user_confirmed':True}],
        transitions=[{'source':'o','target':'d'},{'source':'d','target':'o','rule_id':'repeat'},
                     {'source':'d','target':'r','rule_id':'done'},{'source':'r','target':'c'}],
        entry_activity_id='o',terminal_activity_ids=['c'])
    reference={'workflow_id':'review','revision':2,'content_sha256':'a'*64}
    result=store.cohort(identifier,contains_activity='Review',reference_model=graph,reference=reference)
    report=result['report']
    assert report['case_count']==35 and report['input_rows']==1120 and report['cases_truncated']
    assert report['variants'][0]['path_truncated']
    assert report['conformance']['exact_structural_match_cases']==35
    assert report['conformance']['reference']==reference
    assert report['cohort']['selected_case_count']==35
    assert report['metrics']['repeated_activity_events']==35*29
    assert report['data_quality']['cases_sorted_from_nonchronological_input'] is None
    assert report['original_snapshot_data_quality']==store.report(identifier)['data_quality']


def test_valid_empty_cohort_is_explicit_without_invented_metrics(logs):
    store,_=logs
    identifier=retain(store,[(datetime(2026,10,20,tzinfo=timezone.utc),('Open','Close'),60)])
    for filters in ({'contains_activity':'Absent'}, {'started_since':'2026-10-21T00:00:00Z'}, {'variant_id':'variant_999'}):
        result=store.cohort(identifier,**filters)
        assert result['selection']['selected_case_count']==result['selection']['selected_event_count']==0
        assert result['report'] is None
        assert not store.cases(identifier,**filters)['cases']
    for filters in ({'started_since':'2026-10-20'}, {'started_before':'not a timestamp'},
                    {'started_since':'2026-10-21T00:00:00Z','started_before':'2026-10-20T00:00:00Z'},
                    {'started_since':'2026-10-20T00:00:00Z','started_before':'2026-10-20T02:00:00+02:00'},
                    {'contains_activity':''},{'started_since':True}):
        for operation in (store.cohort,store.cases):
            with pytest.raises(ProcessLogError):operation(identifier,**filters)
    store.forget(identifier,confirmed=True)
    with pytest.raises(LookupError):store.cohort(identifier)


def test_cohort_read_is_atomic_during_deletion_and_expired_snapshots_are_unavailable(logs):
    store,connect=logs
    identifier=retain(store,[(datetime(2026,10,20,tzinfo=timezone.utc),('Open','Review','Close'),60)]*35)
    with connect() as conn:conn.execute('PRAGMA journal_mode=WAL')
    deleted=False
    class Interleaved:
        def __init__(self,conn):self.conn=conn
        @property
        def in_transaction(self):return self.conn.in_transaction
        def execute(self,sql,*args):
            nonlocal deleted
            if 'JOIN process_log_cases' in sql and not deleted:
                deleted=True
                ProcessLogStore(connect).forget(identifier,confirmed=True)
            return self.conn.execute(sql,*args)
    @contextmanager
    def interleaved():
        with connect() as conn:yield Interleaved(conn)
    store.connect=interleaved
    result=store.cohort(identifier,contains_activity='Review')
    assert deleted and result['selection']['selected_case_count']==35
    assert result['selection']['selected_event_count']==result['report']['input_rows']==105
    store.connect=connect
    with pytest.raises(LookupError):store.cohort(identifier)
    identifier=retain(store,[(datetime(2026,10,20,tzinfo=timezone.utc),('Open','Close'),60)])
    store.now=lambda:datetime.now(timezone.utc)+timedelta(days=8)
    with pytest.raises(LookupError):store.cohort(identifier)


def test_dashboard_cohort_api_is_local_authenticated_readonly_and_matches_case_browse(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.workflow_evidence_routes import _process_logs
raw=b'case_id,activity,timestamp\nPRIVATE_A,Open,2026-10-20T10:00:00Z\nPRIVATE_A,Close,2026-10-20T12:00:00Z\nPRIVATE_B,Open,2026-10-21T10:00:00Z\nPRIVATE_B,Review,2026-10-21T12:00:00Z\nPRIVATE_B,Close,2026-10-22T10:00:00Z\n'
identifier=_process_logs.save(raw,retention_days=7,confirmed=True)['dataset_id']
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
query={'dataset_id':identifier,'started_since':'2026-10-21T00:00:00Z','started_before':'2026-10-22T00:00:00Z','contains_activity':'Review'}
base='/v1/process-mining/datasets'
with TestClient(app) as client:
    assert client.get(base+'/cohort',params=query).status_code==401
    assert client.get(base+'/cohort',params=query,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code==403
    response=client.get(base+'/cohort',params=query,headers=headers)
    assert response.status_code==200,response.text
    result=response.json()
    assert response.headers['cache-control']=='no-store'
    assert result['selection']['selected_case_count']==1 and result['selection']['selected_event_count']==3
    assert result['report']['cases'][0]['case']=='Case 2'
    assert result['report']['cases'][0]['events'][-1]['observed_at']=='2026-10-22T10:00:00+00:00'
    assert 'PRIVATE_' not in response.text
    cases=client.get(base+'/cases',params={**query,'activity':'Close'},headers=headers)
    assert [case['case_number'] for case in cases.json()['cases']]==[2]
    assert client.get(base+'/cohort',params={**query,'variant_id':'bad'},headers=headers).status_code==422
    for endpoint in ('/cohort','/cases'):
        assert client.get(base+endpoint,params={**query,'started_since':'2026-10-21'},headers=headers).status_code==422
    assert client.get(base+'/cohort',params={**query,'workflow_id':'review'},headers=headers).status_code==422
    empty=client.get(base+'/cohort',params={**query,'contains_activity':'Absent'},headers=headers)
    assert empty.status_code==200 and empty.json()['report'] is None
    assert client.get(base+'/cohort',params={**query,'dataset_id':'log_'+'a'*32},headers=headers).status_code==404
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
