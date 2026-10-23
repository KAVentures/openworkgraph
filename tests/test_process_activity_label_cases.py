"""Exact event attribution must not be inferred from labels elsewhere in a case."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from io import StringIO
from pathlib import Path
import csv
import json
import os
import sqlite3
import subprocess
import sys

import pytest
from server.process_log_store import ProcessLogStore,ProcessLogError


@pytest.fixture
def logs(tmp_path):
    @contextmanager
    def connect():
        conn=sqlite3.connect(tmp_path/'cases.db');conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()
    store=ProcessLogStore(connect)
    plans=[ [('Open',1,1,1),('Review',2,1,2),('Close',1,1,2)],
            [('Open',2,1,1),('Review',1,1,2),('Close',2,1,2)],
            [('Open',2,2,1),('Review',None,2,2),('Close',1,2,2)],
            [('Open',2,1,1),('Review',2,1,2),('Review',2,1,2),('Close',1,1,2)],
            [('Open',1,1,2),('Review',2,1,2),('Close',1,1,2)]]
    out=StringIO(newline='');writer=csv.writer(out);writer.writerow(('case_id','activity','timestamp','participant','team','origin'))
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    for number,events in enumerate(plans,1):
        for position,(activity,participant,team,origin) in enumerate(events):
            writer.writerow(('case_key_'+str(number),activity,(start+timedelta(minutes=number,seconds=position)).isoformat(),
                'participant_'+str(participant) if participant else '', 'team_'+str(team),'source_'+str(origin)))
    identifier=store.save(out.getvalue().encode(),retention_days=7,confirmed=True,participant_labels_authorized=True,case_links_authorized=True)['dataset_id']
    return store,identifier,connect


def test_label_and_activity_must_share_one_event_with_whole_histories_and_unique_cases(logs):
    store,identifier,connect=logs
    with connect() as conn:before='\n'.join(conn.iterdump())
    query={'activity':'Review','label_dimension':'participant','label_value':'participant_2'}
    first=store.cases(identifier,limit=1,**query)
    assert [row['case_number'] for row in first['cases']]==[1] and first['has_more']
    rest=store.cases(identifier,after=first['next_after'],**query)
    assert [row['case_number'] for row in rest['cases']]==[4,5]  # Review by P1 or unknown is excluded.
    assert rest['cases'][0]['event_count']==4  # Two P2/Review events never duplicate the case.
    assert first['activity_label_filter']=={'activity':'Review','dimension':'participant','label':'participant_2','missing_label':False}
    assert first['same_event_label_match_required'] and not first['label_is_verified_identity']
    opened=store.cases(identifier,activity='Open',label_dimension='participant',label_value='participant_2')
    assert [row['case_number'] for row in opened['cases']]==[2,3,4]  # P2 elsewhere in case1 is insufficient.
    source=store.cases(identifier,activity='Open',label_dimension='origin',label_value='source_2')
    assert [row['case_number'] for row in source['cases']]==[5]
    unknown=store.cases(identifier,activity='Review',label_dimension='participant',label_missing=True)
    assert [row['case_number'] for row in unknown['cases']]==[3]
    assert unknown['activity_label_filter']['label'] is None and unknown['activity_label_filter']['missing_label']
    events=store.events(identifier,3)['events']
    assert len(events)==3 and events[0]['participant']=='participant_2' and events[-1]['participant']=='participant_1'
    assert 'participant' not in events[1]
    with connect() as conn:assert '\n'.join(conn.iterdump())==before


def test_label_case_selection_preserves_applied_cohorts_and_exact_stored_variant(logs):
    store,identifier,_=logs
    query={'activity':'Review','label_dimension':'participant','label_value':'participant_2'}
    assert not store.cases(identifier,contains_team='team_2',**query)['cases']
    result=store.cases(identifier,started_since='2026-10-20T00:04:00Z',started_before='2026-10-20T00:04:01Z',**query)
    assert [row['case_number'] for row in result['cases']]==[4] and result['cases'][0]['event_count']==4
    assert len(store.events(identifier,4)['events'])==4  # Events after the case-date window stay present.
    variant=next(row['variant_id'] for row in store.cases(identifier)['cases'] if row['case_number']==4)
    selected=store.cases(identifier,variant_id=variant,**query)
    assert [row['case_number'] for row in selected['cases']]==[4]
    assert selected['cohort_filters']['variant_id']==variant


def test_missing_uncollected_dimensions_invalid_aliases_and_partial_constraints_are_not_guessed(logs):
    store,identifier,_=logs
    for values in ({'label_dimension':'participant','label_value':'participant_2'},
        {'activity':'Open','label_value':'participant_2'}, {'activity':'Open','label_dimension':'participant'},
        {'activity':'Open','label_dimension':'participant','label_value':'participant_2','label_missing':True},
        {'activity':'Open','label_dimension':'participant','label_missing':1},
        {'activity':'Open','label_dimension':'participant','label_value':'PRIVATE_NATIVE_PERSON'},
        {'activity':'Open','label_dimension':'origin','label_value':'source_5'},
        {'activity':'Open','label_dimension':"participant) OR 1=1",'label_value':'participant_2'}):
        with pytest.raises(ProcessLogError) as exc:store.cases(identifier,**values)
        assert 'PRIVATE_NATIVE_PERSON' not in str(exc.value)
    simple=store.save(b'case_id,activity,timestamp\na,Open,2026-10-20T00:00:00Z\n',retention_days=7,confirmed=True)['dataset_id']
    for dimension in ('participant','team','origin'):
        with pytest.raises(ProcessLogError,match='not retained'):
            store.cases(simple,activity='Open',label_dimension=dimension,label_missing=True)
    assert 'activity_label_filter' not in store.cases(simple,activity='Open')


def test_api_filters_are_dashboard_only_parameterized_bounded_and_no_store(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.workflow_evidence_routes import _process_logs
raw=b'case_id,activity,timestamp,participant\na,Open,2026-10-20T00:00:00Z,participant_1\na,Review,2026-10-20T00:01:00Z,participant_2\nb,Open,2026-10-20T00:02:00Z,participant_2\nb,Review,2026-10-20T00:03:00Z,participant_1\n'
id=_process_logs.save(raw,retention_days=7,confirmed=True,participant_labels_authorized=True)['dataset_id']
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
query={'dataset_id':id,'activity':'Open','label_dimension':'participant','label_value':'participant_2'}
base='/v1/process-mining/datasets/cases'
with TestClient(app) as client:
 assert client.get(base,params=query).status_code==401
 assert client.get(base,params=query,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code==403
 response=client.get(base,params=query,headers=headers)
 assert response.status_code==200,response.text
 assert response.headers['cache-control']=='no-store'
 assert [row['case_number'] for row in response.json()['cases']]==[2]
 assert response.json()['same_event_label_match_required'] is True
 invalid=client.get(base,params={**query,'label_value':'PRIVATE_NATIVE_PERSON'},headers=headers)
 assert invalid.status_code==422 and 'PRIVATE_NATIVE_PERSON' not in invalid.text
 assert client.get(base,params={**query,'label_missing':True},headers=headers).status_code==422
 assert client.get(base,params={**query,'label_dimension':'team','label_value':'team_1'},headers=headers).status_code==422
 assert client.get(base,params={'dataset_id':id,'activity':'Open','label_dimension':'participant','label_missing':True},headers=headers).json()['cases']==[]
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('mode',['cases','events'])
def test_deletion_during_labeled_case_or_event_read_cannot_create_partial_histories(logs,mode):
    store,identifier,connect=logs
    with connect() as conn:conn.execute('PRAGMA journal_mode=WAL')
    deleted=False
    class Interleaved:
        def __init__(self,conn):self.conn=conn
        @property
        def in_transaction(self):return self.conn.in_transaction
        def execute(self,sql,*args):
            nonlocal deleted
            needle='SELECT report_json FROM process_log_datasets' if mode=='cases' else 'SELECT position, activity'
            if needle in sql and not deleted:
                deleted=True;ProcessLogStore(connect).forget(identifier,confirmed=True)
            return self.conn.execute(sql,*args)
    @contextmanager
    def interleaved():
        with connect() as conn:yield Interleaved(conn)
    store.connect=interleaved
    if mode=='cases':
        result=store.cases(identifier,activity='Review',label_dimension='participant',label_value='participant_2')
        assert [row['case_number'] for row in result['cases']]==[1,4,5]
        assert result['dataset']['case_count']==5
    else:
        result=store.events(identifier,1)
        assert [event['activity'] for event in result['events']]==['Open','Review','Close']
        assert [event['participant'] for event in result['events']]==['participant_1','participant_2','participant_1']
    assert deleted
    store.connect=connect
    with pytest.raises(LookupError):store.events(identifier,1)
