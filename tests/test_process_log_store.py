"""Retained logs keep full structural histories without storing source keys."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from server.process_log_store import ProcessLogStore, ProcessLogError
from server.process_mining_csv import mine_csv_events, ProcessMiningCSVError


def csv_log(cases=35, steps=130):
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    return ('case_id,activity,timestamp,source\n'+''.join(
        f'PRIVATE_CASE_{case},Step {position % 3},{(start+timedelta(seconds=position)).isoformat()},PRIVATE_SOURCE\n'
        for case in range(cases) for position in range(steps))).encode()


@pytest.fixture
def local_store(tmp_path):
    clock=[datetime(2026,10,21,tzinfo=timezone.utc)]
    @contextmanager
    def connect():
        conn=sqlite3.connect(tmp_path/'logs.db',timeout=10)
        try:
            with conn:yield conn
        finally:conn.close()
    return ProcessLogStore(connect,now=lambda:clock[0]),connect,clock


def test_full_cases_and_events_are_queryable_after_reopen_and_preview_stays_ephemeral(local_store):
    store,connect,clock=local_store
    raw=csv_log()
    preview=mine_csv_events(raw)
    assert len(preview['cases'])==30 and len(preview['cases'][0]['events'])==24
    assert store.inventory()['datasets']==[]
    saved=store.save(raw,retention_days=7,confirmed=True);identifier=saved['dataset_id']
    assert saved['case_count']==35 and saved['event_count']==4550
    store=ProcessLogStore(connect,now=lambda:clock[0])
    first=store.cases(identifier,limit=30)
    assert len(first['cases'])==30 and first['has_more']
    rest=store.cases(identifier,after=first['next_after'])
    assert [c['case_number'] for c in rest['cases']]==list(range(31,36))
    page=store.events(identifier,35,limit=100)
    assert len(page['events'])==100 and page['has_more']
    last=store.events(identifier,35,after=page['next_after'])
    assert [event['position'] for event in last['events']]==list(range(101,131))
    assert not last['has_more'] and last['events'][-1]['activity']=='Step 0'
    report=store.report(identifier)
    assert report['import_retained'] and report['case_count']==preview['case_count']
    assert report['variants']==preview['variants']
    with connect() as conn:
        serialized='\n'.join(conn.iterdump())
        assert 'PRIVATE_CASE' not in serialized and 'PRIVATE_SOURCE' not in serialized
        assert conn.execute('SELECT COUNT(*) FROM process_log_events').fetchone()[0]==4550
    assert saved['native_case_ids_retained'] is False and saved['ai_access_enabled'] is False
    assert saved['synchronized'] is False


def test_ordering_ties_repeats_filters_and_preview_variant_identity_match(local_store):
    store,_,_=local_store
    raw=b'''case_id,activity,timestamp
z,Close,2026-10-20T10:02:00Z
z,Open,2026-10-20T10:00:00Z
z,Review,2026-10-20T10:01:00Z
z,Review,2026-10-20T10:01:00Z
a,Open,2026-10-20T10:00:00Z
a,Close,2026-10-20T10:02:00Z
'''
    saved=store.save(raw,retention_days=1,confirmed=True);identifier=saved['dataset_id']
    cases=store.cases(identifier)['cases']
    assert cases[0]['event_count']==2 and cases[1]['event_count']==4
    assert cases[1]['tied_timestamp_adjacent_pairs']==1
    events=store.events(identifier,2)['events']
    assert [e['activity'] for e in events]==['Open','Review','Review','Close']
    assert [e['elapsed_since_previous_seconds'] for e in events]==[None,60,0,60]
    assert store.cases(identifier,activity='Review')['cases']==[cases[1]]
    assert store.cases(identifier,variant_id=cases[1]['variant_id'])['cases']==[cases[1]]
    assert not store.cases(identifier,activity="' OR 1=1 --")['cases']
    assert [c['variant_id'] for c in cases]==[c['variant_id'] for c in store.report(identifier)['cases']]


def test_retained_log_comparison_covers_full_cases_and_preserves_snapshot_identity(local_store):
    from shared.process_model import ProcessModel
    store,_,_=local_store
    raw=csv_log(35,2)+b'PRIVATE_CASE_34,Step 2,2026-10-20T00:00:02Z,PRIVATE_SOURCE\n'
    identifier=store.save(raw,retention_days=7,confirmed=True)['dataset_id']
    graph=ProcessModel(goal='Handle selected cases',baseline_source='user_declared',baseline_path=['Step 0','Step 1'],
        activities=[{'id':'a','label':'Step 0'},{'id':'b','label':'Step 1'}],
        transitions=[{'source':'a','target':'b'}],entry_activity_id='a',terminal_activity_ids=['b'])
    reference={'workflow_id':'selected','revision':2,'content_sha256':'a'*64}
    report=store.report(identifier,reference_model=graph,reference=reference)
    assert report['conformance']['reference']==reference
    assert report['conformance']['exact_structural_match_cases']==34
    assert report['conformance']['structurally_deviating_cases']==1
    assert report['conformance']['comparison_covers_all_cases_and_variants'] is True
    original=store.report(identifier)
    for before,after in zip(original['cases'],report['cases']):
        assert (before['case'],before['path'],before['variant_id'])==(after['case'],after['path'],after['variant_id'])
    assert original['data_quality']==report['data_quality']
    assert 'conformance' not in original  # comparisons never mutate the snapshot


def test_expiry_delete_and_confirmation_do_not_touch_other_snapshots(local_store):
    store,connect,clock=local_store
    with pytest.raises(ProcessLogError):store.save(csv_log(1,2),retention_days=1,confirmed=False)
    for days in (True,0,31,'7'):
        with pytest.raises(ProcessLogError):store.save(csv_log(1,2),retention_days=days,confirmed=True)
    with pytest.raises(ProcessMiningCSVError):store.save(b'invalid',retention_days=1,confirmed=True)
    assert store.inventory()['datasets']==[]
    first=store.save(csv_log(1,2),retention_days=1,confirmed=True)['dataset_id']
    second=store.save(csv_log(1,3),retention_days=7,confirmed=True)['dataset_id']
    with pytest.raises(ProcessLogError):store.forget(second,confirmed=False)
    clock[0]+=timedelta(days=1)
    assert store.prune()==1
    for operation in (store.report,store.cases,lambda id:store.events(id,1)):
        with pytest.raises(LookupError):operation(first)
    assert store.inventory()['datasets'][0]['dataset_id']==second
    with pytest.raises(LookupError):store.events(second,100)
    assert store.forget(second,confirmed=True)['deleted'] is True
    with connect() as conn:
        for table in ('process_log_datasets','process_log_cases','process_log_events'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0


def test_failed_event_insert_rolls_back_entire_snapshot(local_store):
    store,connect,_=local_store
    class BrokenConnection:
        def __init__(self,conn):self.conn=conn
        @property
        def in_transaction(self):return self.conn.in_transaction
        def execute(self,*args):return self.conn.execute(*args)
        def executemany(self,*args):raise RuntimeError('synthetic disk write failure')
    @contextmanager
    def broken():
        with connect() as conn:yield BrokenConnection(conn)
    store.connect=broken
    with pytest.raises(RuntimeError):store.save(csv_log(1,2),retention_days=1,confirmed=True)
    with connect() as conn:
        for table in ('process_log_datasets','process_log_cases','process_log_events'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0


def test_concurrent_snapshot_quota_has_one_winner(local_store):
    store,_,_=local_store
    for _ in range(19):store.save(csv_log(1,1),retention_days=1,confirmed=True)
    def save(_):
        try:return store.save(csv_log(1,1),retention_days=1,confirmed=True)['dataset_id']
        except ProcessLogError:return 'quota'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,range(2)))
    assert results.count('quota')==1 and len(store.inventory()['datasets'])==20


def test_authenticated_dashboard_retention_lifecycle_and_body_limits(tmp_path):
    # Isolated app/middleware, sessions, SQLite and config, as with preview tests.
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
raw=b'case_id,activity,timestamp\nPRIVATE_CASE,Open,2026-10-20T10:00:00Z\nPRIVATE_CASE,Close,2026-10-20T10:01:00Z\n'
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session(),'Content-Type':'text/csv'}
base='/v1/process-mining/datasets'
with TestClient(app) as client:
    assert client.get(base).status_code==401
    assert client.post(base+'?retention_days=7',headers=headers,content=raw).status_code==403
    assert client.get(base,headers=headers).json()['datasets']==[]
    retained=client.post(base+'?retention_days=7',headers={**headers,'X-OWG-Retention-Confirmed':'true'},content=raw)
    assert retained.status_code==200,retained.text
    identifier=retained.json()['dataset_id'];query={'dataset_id':identifier}
    assert retained.headers['cache-control']=='no-store'
    assert client.get(base,headers=headers).json()['datasets'][0]['dataset_id']==identifier
    for suffix in ('/report','/cases','/events'):
        params=query|({'case_number':1} if suffix=='/events' else {})
        response=client.get(base+suffix,params=params,headers=headers)
        assert response.status_code==200,response.text
        assert 'PRIVATE_CASE' not in response.text
        assert response.headers['cache-control']=='no-store'
    assert client.get(base+'/events',headers=headers,params=query|{'case_number':2}).status_code==404
    assert client.get(base+'/cases',headers=headers,params=query|{'limit':101}).status_code==422
    approved={**headers,'X-OWG-Retention-Confirmed':'true'}
    assert client.post(base+'?retention_days=7',headers=approved,content=b'x'*(1024*1024+1)).status_code==413
    assert client.post(base+'?retention_days=7',headers={**approved,'Content-Type':'application/json'},content=b'{}').status_code==415
    assert client.post(base+'/forget',headers=headers,params=query).status_code==403
    assert client.post(base+'/forget',headers={**headers,'X-OWG-Deletion-Confirmed':'true'},params=query).json()['deleted']
    assert client.get(base+'/report',params=query,headers=headers).status_code==404
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],
                          capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
