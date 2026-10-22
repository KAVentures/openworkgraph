"""Full-history change analysis; projections never become business truth."""
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
    clock = [datetime(2026, 10, 22, tzinfo=timezone.utc)]
    @contextmanager
    def connect():
        conn = sqlite3.connect(tmp_path / 'logs.db', timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()
    return ProcessLogStore(connect, now=lambda: clock[0]), connect, clock


def raw_log(cases, *, day=20):
    """Each entry is (full path, total elapsed); no real customer records."""
    out = StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(('case_id', 'activity', 'timestamp', 'source'))
    start = datetime(2026, 10, day, tzinfo=timezone.utc)
    for number, (path, elapsed) in enumerate(cases):
        for position, activity in enumerate(path):
            stamp = start + timedelta(seconds=elapsed * position / max(1, len(path) - 1))
            writer.writerow((f'PRIVATE_CASE_{number}', activity, stamp.isoformat(), 'PRIVATE_SOURCE'))
    return out.getvalue().encode()


def save(store, cases, **kwargs):
    return store.save(raw_log(cases, **kwargs), retention_days=7, confirmed=True)['dataset_id']


def model():
    return ProcessModel(goal='Handle selected case', baseline_source='user_declared', baseline_path=['Open', 'Close'],
        activities=[{'id':'open', 'label':'Open'}, {'id':'close', 'label':'Close'}],
        transitions=[{'source':'open', 'target':'close'}], entry_activity_id='open', terminal_activity_ids=['close'])


def test_exact_path_mix_common_path_timing_and_same_revision_comparison(logs):
    store, connect, _ = logs
    reviewed, quick, exception = ('Open','Review','Close'), ('Open','Close'), ('Open','Escalate','Close')
    a = save(store, [(reviewed,120)]*3 + [(quick,60)])
    b = save(store, [(reviewed,240)] + [(quick,60)]*3 + [(exception,300)]*2, day=21)
    with connect() as conn:
        before = '\n'.join(conn.iterdump())
    reference = {'workflow_id':'approved_process', 'revision':2, 'content_sha256':'a'*64}
    graph = model()
    graph_before = graph.model_dump_json()
    result = store.compare(a,b,comparability_confirmed=True,reference_model=graph,reference=reference)
    assert result['summary']['path_mix_total_variation_distance'] == .583333
    assert result['summary']['current_only_path_count'] == 1
    # The reviewed and quick routes still occur; only Escalate is new.
    assert result['summary']['baseline_only_path_count'] == 0
    assert result['summary']['current_cases_on_current_only_paths'] == 2
    review = next(row for row in result['paths'] if row['observed_path']==list(reviewed))
    assert review['baseline_case_count']==3 and review['current_case_count']==1
    assert review['case_share_change_percentage_points']==pytest.approx(-58.3333)
    assert review['baseline_median_elapsed_seconds']==120 and review['current_median_elapsed_seconds']==240
    assert review['median_change_percent']==100
    assert result['summary']['baseline_p90_case_elapsed_seconds']==120
    assert result['summary']['current_p90_case_elapsed_seconds']==300
    assert next(row for row in result['activities'] if row['activity']=='Escalate')['baseline_event_count']==0
    c=result['conformance']
    assert c['reference']==reference
    assert c['baseline_exact_structural_match_cases']==1 and c['current_exact_structural_match_cases']==3
    assert c['exact_match_share_change_percentage_points']==25
    assert c['all_retained_cases_and_full_paths_compared'] and not c['rules_and_conditions_evaluated']
    assert not result['comparability']['event_windows_overlap']
    assert 'PRIVATE_' not in json.dumps(result)
    assert graph.model_dump_json()==graph_before
    with connect() as conn:
        assert '\n'.join(conn.iterdump())==before
    for flag in ('statistical_significance_tested','business_rules_evaluated','business_outcomes_verified',
                 'process_model_updated','comparison_report_retained','external_calls_made','execution_permissions_granted'):
        assert result[flag] is False


def test_literal_paths_survive_frequency_rank_changes_and_long_prefixes(logs):
    store, _, _ = logs
    common = ('Open',)*25
    first, second = common+('Review','Close'), common+('Escalate','Close')
    a=save(store,[(first,0)]*3+[(second,90)])
    b=save(store,[(second,180)]*3+[(first,60)])
    assert store.report(a)['variants'][0]['observed_path']==store.report(b)['variants'][0]['observed_path']
    result=store.compare(a,b,comparability_confirmed=True)
    assert result['summary']['path_mix_total_variation_distance']==.5
    rows=result['paths']
    assert len(rows)==2 and rows[0]['observed_path']==rows[1]['observed_path']
    assert rows[0]['path_sha256']!=rows[1]['path_sha256']
    assert all(row['path_truncated'] and row['path_event_count']==27 for row in rows)
    assert all(row['presence']=='both' for row in rows)
    assert 'timestamp_ties_have_unverified_order' in result['warnings']
    assert 'observed_event_windows_overlap' in result['warnings']
    zero=next(row for row in rows if row['baseline_median_elapsed_seconds']==0)
    assert zero['median_change_percent'] is None and zero['median_change_seconds']==60
    loop=next(row for row in result['transitions'] if row['from']==row['to']=='Open')
    assert loop['baseline_transition_count']==loop['current_transition_count']==96
    assert loop['baseline_case_count']==loop['current_case_count']==4


def test_full_unsampled_paths_and_all_cases_used_despite_display_caps(logs):
    store, _, _ = logs
    # 60 routes, with 60 different intermediate activities and 120 edges.
    cases=[(('Open',f'Route {n}','Close'),60+n) for n in range(60)]
    a=save(store,cases)
    b=save(store,cases+[(('Open','New','Close'),300)])
    assert store.report(a)['variants_truncated'] and store.report(a)['cases_truncated']
    result=store.compare(a,b,comparability_confirmed=True,reference_model=model(),reference={'revision':1})
    assert result['coverage']=={
        'all_retained_cases_and_events_used':True,'path_rows_total':61,'path_rows_returned':50,
        'transition_rows_total':122,'transition_rows_returned':100,'activity_rows_total':63,
        'display_path_step_limit':24,'path_rows_truncated':True,'transition_rows_truncated':True}
    assert result['current']['case_count']==61
    assert result['summary']['current_cases_on_current_only_paths']==1
    assert result['summary']['path_mix_total_variation_distance']==round(1/61,6)
    assert result['conformance']['baseline_structurally_deviating_cases']==60
    assert result['conformance']['current_structurally_deviating_cases']==61
    assert result['paths'][0]['observed_path']==['Open','New','Close']
    assert len(result['baseline']['events_sha256'])==64
    assert 'PRIVATE_' not in json.dumps(result)
    assert 'conformance' not in store.report(a)


def test_same_mix_different_volume_and_reversal_properties(logs):
    store, _, _=logs
    cases=[(('Open','Close'),60),(('Open','Review','Close'),120)]
    a=save(store,cases);b=save(store,cases*2,day=21)
    result=store.compare(a,b,comparability_confirmed=True)
    assert result['summary']['path_mix_total_variation_distance']==0
    assert result['summary']['median_change_seconds']==0
    assert all(row['case_share_change_percentage_points']==0 for row in result['paths'])
    c=save(store,[(('Open','Escalate','Close'),300)],day=21)
    forward=store.compare(a,c,comparability_confirmed=True)
    reverse=store.compare(c,a,comparability_confirmed=True)
    assert forward['summary']['path_mix_total_variation_distance']==reverse['summary']['path_mix_total_variation_distance']==1
    assert forward['summary']['median_change_seconds']==-reverse['summary']['median_change_seconds']
    assert forward['baseline']['events_sha256']==reverse['current']['events_sha256']
    assert forward['summary']['current_only_path_count']==reverse['summary']['baseline_only_path_count']==1
    forward_rows={r['path_sha256']:r for r in forward['paths']}
    for row in reverse['paths']:
        assert row['case_share_change_percentage_points']==-forward_rows[row['path_sha256']]['case_share_change_percentage_points']


def test_requires_explicit_comparability_distinct_existing_unexpired_snapshots(logs):
    store, _, clock=logs
    a=save(store,[(('Open','Close'),60)])
    b=save(store,[(('Open','Close'),60)])
    for value in (False,1,'true',None):
        with pytest.raises(ProcessLogError):store.compare(a,b,comparability_confirmed=value)
    with pytest.raises(ProcessLogError):store.compare(a,a,comparability_confirmed=True)
    with pytest.raises(LookupError):store.compare(a,'log_'+'a'*32,comparability_confirmed=True)
    store.forget(b,confirmed=True)
    with pytest.raises(LookupError):store.compare(a,b,comparability_confirmed=True)
    b=save(store,[(('Open','Close'),60)])
    clock[0]+=timedelta(days=7)
    with pytest.raises(LookupError):store.compare(a,b,comparability_confirmed=True)
    store.prune()
    assert not store.inventory()['datasets']


def test_deletion_during_comparison_never_creates_partial_case_histories(logs):
    store, connect, _=logs
    a=save(store,[(('Open','Close'),60)]*35)
    b=save(store,[(('Open','Review','Close'),120)]*40)
    with connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
    deleted=False
    class InterleavedConnection:
        def __init__(self,conn):self.conn=conn
        @property
        def in_transaction(self):return self.conn.in_transaction
        def execute(self,sql,*args):
            nonlocal deleted
            if 'FROM process_log_events' in sql and not deleted:
                deleted=True
                # A separate writer commits after the reader pinned existence.
                ProcessLogStore(connect,now=store.now).forget(b,confirmed=True)
            return self.conn.execute(sql,*args)
    @contextmanager
    def interleaved():
        with connect() as conn:yield InterleavedConnection(conn)
    store.connect=interleaved
    result=store.compare(a,b,comparability_confirmed=True)
    assert deleted and result['baseline']['case_count']==35 and result['current']['case_count']==40
    assert result['current']['event_count']==120
    assert result['paths'][0]['baseline_case_count']+result['paths'][1]['baseline_case_count']==35
    store.connect=connect
    with pytest.raises(LookupError):store.report(b)


def test_dashboard_comparison_auth_validation_no_store_and_exact_reference(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.workflow_evidence_routes import _process_logs
from shared.process_model import ProcessModel
raw=b'case_id,activity,timestamp\nPRIVATE_CASE,Open,2026-10-20T10:00:00Z\nPRIVATE_CASE,Close,2026-10-20T10:01:00Z\n'
a=_process_logs.save(raw,retention_days=7,confirmed=True)['dataset_id']
b=_process_logs.save(raw,retention_days=7,confirmed=True)['dataset_id']
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
query={'baseline_id':a,'current_id':b,'comparability_confirmed':'true'}
base='/v1/process-mining/datasets/compare'
with TestClient(app) as client:
    assert client.get(base,params=query).status_code==401
    assert client.get(base,params={**query,'comparability_confirmed':'false'},headers=headers).status_code==422
    assert client.get(base,params={**query,'current_id':a},headers=headers).status_code==422
    assert client.get(base,params={**query,'baseline_id':"' OR 1=1"},headers=headers).status_code==422
    assert client.get(base,params={**query,'current_id':'log_'+'a'*32},headers=headers).status_code==404
    assert client.get(base,params={**query,'workflow_id':'missing'},headers=headers).status_code==422
    assert client.get(base,params={**query,'workflow_id':'missing','revision':1},headers=headers).status_code==404
    response=client.get(base,params=query,headers=headers)
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    assert response.json()['summary']['path_mix_total_variation_distance']==0
    assert 'PRIVATE_' not in response.text
    # Pin both logs to the requested immutable revision, even after a newer edit.
    graph={'goal':'Handle selected case','baseline_source':'user_declared','baseline_path':['Open','Close'],
        'activities':[{'id':'open','label':'Open'},{'id':'close','label':'Close'}],
        'transitions':[{'source':'open','target':'close'}],'entry_activity_id':'open','terminal_activity_ids':['close']}
    saved=client.post('/v1/process-catalog',headers=headers,json={'workflow_id':'review','title':'Reviewed process',
        'process_model':graph,'user_confirmed':True})
    assert saved.status_code==200,saved.text
    first=saved.json()
    edited=client.post('/v1/process-catalog',headers=headers,json={'workflow_id':'review','title':'Newer process',
        'process_model':{**graph,'goal':'Handle a newer process'},'user_confirmed':True,'expected_revision':1})
    assert edited.status_code==200,edited.text
    comparison=client.get(base,params={**query,'workflow_id':'review','revision':1},headers=headers)
    assert comparison.status_code==200,comparison.text
    assert comparison.json()['conformance']['reference']=={k:first[k] for k in ('workflow_id','revision','content_sha256')}
    assert comparison.json()['conformance']['current_exact_structural_match_cases']==1
    assert client.get('/v1/process-catalog/revision',params={'workflow_id':'review'},headers=headers).json()['revision']==2
    # Ordinary AI bearer credentials cannot read retained snapshots.
    token=local_auth.ensure_api_token()
    denied=client.get(base,params=query,headers={'Authorization':'Bearer '+token})
    assert denied.status_code==403,denied.text
'''
    env=os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],
                          capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
