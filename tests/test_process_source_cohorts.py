"""Source selection uses supplied event aliases, retaining every case event."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
import csv
import json
import sqlite3

import pytest
from server.process_log_store import ProcessLogStore, ProcessLogError


@pytest.fixture
def logs(tmp_path):
    @contextmanager
    def connect():
        conn=sqlite3.connect(tmp_path/'source.db');conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()
    store=ProcessLogStore(connect)
    stream=StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(('case_id','activity','timestamp','participant','team','origin'))
    # P2/team2/source2 co-occur only in case 1, although all three are
    # separately present in case 2. Case 3 is entirely from source 1.
    plans=[[(1,1,1),(2,2,2),(1,1,2)],[(2,2,1),(1,1,2),(1,1,1)],[(1,1,1),(1,1,1),(1,1,1)]]
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    for number,events in enumerate(plans,1):
        for position,(participant,team,origin) in enumerate(events):
            writer.writerow((f'case_key_{number}',('Open','Review','Close')[position],
                (start+timedelta(minutes=number,seconds=position)).isoformat(),
                f'participant_{participant}',f'team_{team}',f'source_{origin}'))
    identifier=store.save(stream.getvalue().encode(),retention_days=7,confirmed=True,
        participant_labels_authorized=True,case_links_authorized=True)['dataset_id']
    return store,identifier,connect


def test_source_selection_keeps_full_histories_aliases_and_one_case_per_match(logs):
    store,identifier,connect=logs
    with connect() as conn:before='\n'.join(conn.iterdump())
    first=store.cases(identifier,contains_origin='source_2',limit=1)
    assert [row['case_number'] for row in first['cases']]==[1] and first['has_more']
    following=store.cases(identifier,contains_origin='source_2',after=first['next_after'])
    assert [row['case_number'] for row in following['cases']]==[2]
    result=store.cohort(identifier,contains_origin='source_2')
    assert result['selection']['filters']=={'contains_origin':'source_2'}
    assert result['selection']['selected_case_count']==2 and result['selection']['selected_event_count']==6
    assert result['selection']['all_events_of_selected_cases_included']
    assert [event['origin'] for event in result['report']['cases'][0]['events']]==['source_1','source_2','source_2']
    assert result['report']['cases'][1]['case_key_alias']=='case_key_2'
    assert len(store.events(identifier,1)['events'])==3
    with connect() as conn:assert '\n'.join(conn.iterdump())==before


def test_all_selected_metadata_labels_share_an_event_but_activity_is_whole_case(logs):
    store,identifier,_=logs
    filters={'contains_origin':'source_2','contains_participant':'participant_2','contains_team':'team_2'}
    result=store.cohort(identifier,contains_activity='Open',**filters)
    assert [case['case'] for case in result['report']['cases']]==['Case 1']
    assert [case['case_number'] for case in store.cases(identifier,**filters)['cases']]==[1]
    # Activity-specific drill-down remains a separate exact event constraint:
    # case 1 has P2/source2 on Review, not Open.
    assert not store.cases(identifier,activity='Open',label_dimension='origin',label_value='source_2',**filters)['cases']
    assert store.cohort(identifier,contains_origin='source_2',started_since='2026-10-20T00:02:00Z')['report']['cases'][0]['case']=='Case 2'


def test_source_comparison_uses_whole_cases_and_reports_overlap(logs):
    store,identifier,_=logs
    result=store.compare_cohorts(identifier,baseline_filters={'contains_origin':'source_1'},
        current_filters={'contains_origin':'source_2'},comparability_confirmed=True)
    assert result['baseline']['case_count']==3 and result['baseline']['event_count']==9
    assert result['current']['case_count']==2 and result['current']['event_count']==6
    assert result['current']['cohort_filters']=={'contains_origin':'source_2'}
    assert result['cohort_overlap']['shared_case_count']==2
    assert store.cohort(identifier,contains_origin='source_4')['report'] is None


def test_source_alias_bounds_uncollected_dimensions_and_review_are_explicit(logs):
    store,identifier,_=logs
    simple=store.save(b'case_id,activity,timestamp\na,Open,2026-10-20T00:00:00Z\n',confirmed=True,retention_days=7)['dataset_id']
    for value in ('PRIVATE_ACCOUNT','source_0','source_5','source_01',True):
        for operation in (store.cohort,store.cases):
            with pytest.raises(ProcessLogError) as exc:operation(identifier,contains_origin=value)
            assert 'PRIVATE_ACCOUNT' not in str(exc.value)
    for operation in (store.cohort,store.cases):
        with pytest.raises(ProcessLogError,match='not retained'):operation(simple,contains_origin='source_1')
    with pytest.raises(ProcessLogError,match='not retained'):
        store.compare_cohorts(simple,baseline_filters={},current_filters={'contains_origin':'source_1'},comparability_confirmed=True)
    with pytest.raises(ProcessLogError):
        store.compare_cohorts(identifier,baseline_filters={},current_filters={'contains_origin':'source_2'},comparability_confirmed=False)


def test_source_filters_reach_all_production_endpoints_with_private_errors_sanitized(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.workflow_evidence_routes import _process_logs
raw=b'case_id,activity,timestamp,origin\ncase_key_1,Open,2026-10-20T00:00:00Z,source_1\ncase_key_1,Close,2026-10-20T00:01:00Z,source_2\ncase_key_2,Open,2026-10-20T00:02:00Z,source_1\n'
id=_process_logs.save(raw,retention_days=7,confirmed=True,case_links_authorized=True)['dataset_id']
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
base='/v1/process-mining/datasets'
with TestClient(app) as client:
 for route in ('/cases','/cohort'):
  response=client.get(base+route,params={'dataset_id':id,'contains_origin':'source_2'},headers=headers)
  assert response.status_code==200,response.text
  assert response.headers['cache-control']=='no-store'
  value=response.json()
  if route=='/cases':assert [row['case_number'] for row in value['cases']]==[1]
  else:assert value['selection']['selected_event_count']==2
  invalid=client.get(base+route,params={'dataset_id':id,'contains_origin':'PRIVATE_ACCOUNT'},headers=headers)
  assert invalid.status_code==422 and 'PRIVATE_ACCOUNT' not in invalid.text
 body={'dataset_id':id,'baseline_filters':{'contains_origin':'source_1'},'current_filters':{'contains_origin':'source_2'},'comparability_confirmed':True}
 assert client.post(base+'/cohorts/compare',json=body).status_code==401
 assert client.post(base+'/cohorts/compare',json=body,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code==403
 response=client.post(base+'/cohorts/compare',json=body,headers=headers)
 assert response.status_code==200,response.text
 assert response.json()['cohort_overlap']['shared_case_count']==1
 assert response.json()['current']['event_count']==2
 body['current_filters']['contains_origin']='PRIVATE_ACCOUNT'
 invalid=client.post(base+'/cohorts/compare',json=body,headers=headers)
 assert invalid.status_code==422 and 'PRIVATE_ACCOUNT' not in invalid.text
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


def test_maximum_log_with_only_last_event_matching_uses_bounded_membership_work(logs):
    store,_,connect=logs
    stream=StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(('case_id','activity','timestamp','participant','team','origin'))
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    for position in range(5000):
        last=position==4999
        writer.writerow(('case_key_1','Review' if last else 'Open',
            (start+timedelta(seconds=position)).isoformat(),
            'participant_2' if last else 'participant_1','team_2' if last else 'team_1',
            'source_2' if last else 'source_1'))
    identifier=store.save(stream.getvalue().encode(),retention_days=7,confirmed=True,
        participant_labels_authorized=True,case_links_authorized=True)['dataset_id']
    # Bound SQLite work rather than wall-clock time. Re-evaluating case
    # membership for every event makes this legal input quadratic (~300M VM
    # instructions). Leave generous headroom for SQLite version differences.
    instructions=0
    def progress():
        nonlocal instructions
        instructions+=1000
        return int(instructions>2_000_000)
    @contextmanager
    def counted():
        with connect() as conn:
            conn.set_progress_handler(progress,1000)
            yield conn
    store.connect=counted
    result=store.cohort(identifier,contains_origin='source_2',contains_participant='participant_2',
        contains_team='team_2',contains_activity='Review')
    assert result['selection']['selected_event_count']==5000 and result['selection']['selected_case_count']==1
    assert result['report']['case_correlation']['sources'][1]['event_count']==1
    assert result['report']['variants'][0]['path_truncated']
    assert instructions<2_000_000
    store.connect=connect
    # Other datasets reuse case ordinals but must not leak membership.
    store.save(b'case_id,activity,timestamp,origin\ncase_key_1,Open,2026-10-20T00:00:00Z,source_4\n',
        retention_days=7,confirmed=True,case_links_authorized=True)
    assert store.cohort(identifier,contains_origin='source_4')['report'] is None
    assert store.events(identifier,1,after=4900)['events'][-1]['origin']=='source_2'
