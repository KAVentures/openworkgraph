"""Complete population comparisons never clip paths or claim independent samples."""
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
from server.process_log_store import ProcessLogError, ProcessLogStore
from shared.process_model import ProcessModel


@pytest.fixture
def logs(tmp_path):
    clock = [datetime(2026, 10, 23, tzinfo=timezone.utc)]
    @contextmanager
    def connect():
        conn = sqlite3.connect(tmp_path / 'logs.db', timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn: yield conn
        finally: conn.close()
    return ProcessLogStore(connect, now=lambda: clock[0]), connect, clock


def save(store, count=40):
    out = StringIO(newline=''); writer = csv.writer(out)
    writer.writerow(('case_id','activity','timestamp','participant','team'))
    for n in range(count):
        start = datetime(2026,10,20,tzinfo=timezone.utc)+timedelta(days=n)
        path = ['Open','Close'] if n < 35 else ['Open']*25+['Review','Close']
        for i, activity in enumerate(path):
            writer.writerow((f'PRIVATE_{n}',activity,(start+timedelta(minutes=i)).isoformat(),
                'participant_1' if i==0 else 'participant_2', 'team_1' if n<35 else 'team_2'))
    return store.save(out.getvalue().encode(),retention_days=7,confirmed=True,
                      participant_labels_authorized=True)['dataset_id']


def compare(store, identifier, baseline=None, current=None, **kwargs):
    return store.compare_cohorts(identifier, baseline_filters=baseline or {},current_filters=current or {},
                                comparability_confirmed=True,**kwargs)


def test_unsampled_whole_case_populations_known_overlap_and_no_mutation(logs):
    store, connect, _ = logs; identifier = save(store)
    with connect() as conn: before='\n'.join(conn.iterdump())
    result=compare(store,identifier,{}, {'contains_team':'team_2','contains_participant':'participant_2'})
    assert result['format']=='openworkgraph.process-cohort-comparison.v1'
    assert result['baseline']['case_count']==40 and result['current']['case_count']==5
    assert result['current']['event_count']==135
    assert result['cohort_overlap']=={'case_identity_basis':'same_snapshot_case_ordinal',
        'shared_case_count':5,'baseline_only_case_count':35,'current_only_case_count':0,
        'union_case_count':40,'cohorts_are_disjoint':False}
    assert result['summary']['path_mix_total_variation_distance']==.875
    assert result['summary']['baseline_median_elapsed_seconds']==60
    assert result['summary']['current_median_elapsed_seconds']==1560
    path=next(row for row in result['paths'] if row['path_event_count']==27)
    assert path['path_truncated'] and path['current_case_count']==5
    assert next(row for row in result['activities'] if row['activity']=='Open')['current_event_count']==125
    assert result['coverage']['all_events_of_selected_cases_included']
    assert not result['coverage']['events_clipped_to_filter_window']
    assert 'PRIVATE_' not in json.dumps(result)
    assert 'case_identity_not_joined' not in result['warnings']
    assert 'cohorts_share_retained_cases' in result['warnings']
    for key in ('business_rules_evaluated','business_outcomes_verified','comparison_report_retained',
                'process_model_updated','external_calls_made','execution_permissions_granted','statistical_significance_tested'):
        assert result[key] is False
    assert result['comparability']['statistical_independence_verified'] is False
    with connect() as conn: assert '\n'.join(conn.iterdump())==before


def test_date_windows_select_complete_cases_and_reversal_and_exact_model(logs):
    store, _, _=logs; identifier=save(store)
    a={'started_before':'2026-11-24T00:00:00Z'}
    b={'started_since':'2026-11-23T19:00:00-05:00','started_before':'2026-11-29T00:00:01Z'}
    model=ProcessModel(goal='Handle selected case', baseline_source='user_declared', baseline_path=['Open','Close'],
        activities=[{'id':'open','label':'Open'},{'id':'close','label':'Close'}],
        transitions=[{'source':'open','target':'close'}],entry_activity_id='open',terminal_activity_ids=['close'])
    reference={'workflow_id':'approved','revision':3,'content_sha256':'a'*64}
    result=compare(store,identifier,a,b,reference_model=model,reference=reference)
    assert result['baseline']['case_count']==35 and result['current']['case_count']==5
    assert result['cohort_overlap']['cohorts_are_disjoint']
    assert result['cohort_overlap']['shared_case_count']==0
    assert result['conformance']['reference']==reference
    assert result['conformance']['baseline_exact_structural_match_cases']==35
    assert result['conformance']['current_exact_structural_match_cases']==0
    assert result['conformance']['all_selected_cases_and_full_paths_compared']
    reverse=compare(store,identifier,b,a)
    assert reverse['summary']['median_change_seconds']==-result['summary']['median_change_seconds']
    assert reverse['summary']['path_mix_total_variation_distance']==result['summary']['path_mix_total_variation_distance']==1
    # A tiny selection window still includes all following events of that case.
    one=compare(store,identifier,{}, {'started_since':'2026-11-24T00:00:00Z','started_before':'2026-11-24T00:00:01Z'})
    assert one['current']['case_count']==1 and one['current']['event_count']==27
    assert one['current']['event_window']['last_event_at']=='2026-11-24T00:26:00+00:00'


def test_identical_populations_zero_elapsed_missing_ties_and_invalid_selections(logs):
    store, connect, clock=logs; identifier=save(store,1)
    equal=compare(store,identifier)
    assert equal['cohort_overlap']['shared_case_count']==1
    assert equal['summary']['path_mix_total_variation_distance']==0
    with connect() as conn:
        conn.execute('UPDATE process_log_events SET observed_at = ?, elapsed_since_previous_seconds = 0',
                     ('2026-10-20T00:00:00+00:00',))
    equal=compare(store,identifier)
    assert equal['summary']['baseline_median_elapsed_seconds']==0
    assert equal['summary']['median_change_percent'] is None
    assert equal['current']['data_quality']['cases_with_tied_timestamps']==1
    assert 'timestamp_ties_have_unverified_order' in equal['warnings']
    for filters in ({'contains_team':'team_999'},{'started_since':'2026-10-20'},
                    {'contains_participant':"' OR 1=1"},{'unknown':True},[],
                    {'started_since':'2026-11-01T00:00:00Z','started_before':'2026-10-01T00:00:00Z'}):
        with pytest.raises(ProcessLogError):
            store.compare_cohorts(identifier,baseline_filters={},current_filters=filters,comparability_confirmed=True)
    for confirmed in (False,1,'true',None):
        with pytest.raises(ProcessLogError):
            store.compare_cohorts(identifier,baseline_filters={},current_filters={},comparability_confirmed=confirmed)
    clock[0]+=timedelta(days=7)
    with pytest.raises(LookupError):compare(store,identifier)


def test_concurrent_deletion_cannot_clip_either_cohort(logs):
    store, connect, _=logs; identifier=save(store)
    with connect() as conn: conn.execute('PRAGMA journal_mode=WAL')
    deleted=False
    class Interleaved:
        def __init__(self,conn):self.conn=conn
        @property
        def in_transaction(self):return self.conn.in_transaction
        def execute(self,sql,*args):
            nonlocal deleted
            if 'FROM process_log_events e' in sql and not deleted:
                deleted=True;ProcessLogStore(connect,now=store.now).forget(identifier,confirmed=True)
            return self.conn.execute(sql,*args)
    @contextmanager
    def interleaved():
        with connect() as conn:yield Interleaved(conn)
    store.connect=interleaved
    result=compare(store,identifier,{}, {'contains_team':'team_2'})
    assert deleted and result['baseline']['case_count']==40 and result['current']['event_count']==135
    store.connect=connect
    with pytest.raises(LookupError):compare(store,identifier)


def test_api_dashboard_only_bounded_reviewed_body_and_no_private_error_echo(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.workflow_evidence_routes import _process_logs
raw=b'case_id,activity,timestamp\nPRIVATE_CASE,Open,2026-10-20T10:00:00Z\nPRIVATE_CASE,Close,2026-10-20T10:01:00Z\n'
id=_process_logs.save(raw,retention_days=7,confirmed=True)['dataset_id']
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
body={'dataset_id':id,'baseline_filters':{},'current_filters':{},'comparability_confirmed':True}
base='/v1/process-mining/datasets/cohorts/compare'
with TestClient(app) as client:
 assert client.post(base,json=body).status_code==401
 token=local_auth.ensure_api_token()
 assert client.post(base,json=body,headers={'Authorization':'Bearer '+token}).status_code==403
 for patch in ({'comparability_confirmed':False},{'comparability_confirmed':'true'},
               {'current_filters':{'contains_participant':'PRIVATE_ID'}},{'current_filters':{'unknown':'PRIVATE_ID'}},
               {'workflow_id':'missing'},{'dataset_id':"' OR 1=1"}):
  response=client.post(base,json={**body,**patch},headers=headers)
  assert response.status_code in (403,422),response.text
  assert 'PRIVATE_ID' not in response.text
 assert client.post(base,json={**body,'current_filters':{'contains_activity':'Absent'}},headers=headers).status_code==422
 assert client.post(base,json={**body,'dataset_id':'log_'+'a'*32},headers=headers).status_code==404
 assert client.post(base,json={**body,'workflow_id':'missing','revision':1},headers=headers).status_code==404
 assert client.post(base,content=b'{' + b' '*4096,headers={**headers,'Content-Type':'application/json'}).status_code==413
 assert client.post(base,content=b'{}',headers={**headers,'Content-Type':'text/plain'}).status_code==415
 assert client.post(base,content=b'{"dataset_id":"a","dataset_id":"b"}',headers={**headers,'Content-Type':'application/json'}).status_code==422
 response=client.post(base,json=body,headers=headers)
 assert response.status_code==200,response.text
 assert response.headers['cache-control']=='no-store'
 assert response.json()['cohort_overlap']['shared_case_count']==1
 assert 'PRIVATE_' not in response.text
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],
                          capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
