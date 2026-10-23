"""Explicit alias metadata, complete-case cohorts and minimized local retention."""
from datetime import datetime,timedelta,timezone
from io import StringIO
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server.process_mining_csv import mine_csv_events,parse_csv_events,ProcessMiningCSVError
from server.process_log_store import ProcessLogError
from test_process_case_cohorts import logs


def sample():
    return b'''case_id,activity,timestamp,participant,team
PRIVATE_ONE,Open,2026-10-20T00:00:00Z,participant_1,team_1
PRIVATE_ONE,Review,2026-10-20T00:01:00Z,participant_2,team_2
PRIVATE_ONE,Close,2026-10-20T00:02:00Z,participant_1,team_1
PRIVATE_TWO,Open,2026-10-20T00:03:00Z,participant_2,team_1
PRIVATE_TWO,Close,2026-10-20T00:04:00Z,participant_2,team_1
'''


def test_explicit_alias_analysis_counts_support_and_changes_without_people_or_handoff_claims():
    report=mine_csv_events(sample(),participant_labels_authorized=True)
    metadata=report['participant_labels'];summary=metadata['dimensions_summary']
    assert summary['participant']['labels']==[
        {'label':'participant_1','event_count':2,'case_count':1},
        {'label':'participant_2','event_count':3,'case_count':2}]
    assert summary['participant']['adjacent_label_change_count']==2
    assert summary['team']['adjacent_label_change_count']==2
    assert metadata['label_scope']=='this_selected_log_only'
    assert not any(metadata[key] for key in ('native_participant_identifiers_included',
        'labels_are_verified_people','team_membership_verified','human_handoffs_verified','anonymization_verified'))
    assert report['cases'][0]['participant_labels']==['participant_1','participant_2']
    assert report['cases'][0]['events'][1]['participant']=='participant_2'
    assert 'PRIVATE_' not in json.dumps(report)


def test_unconfirmed_or_native_identity_columns_fail_without_echoing_values():
    for flag in (False,None,1,'true'):
        with pytest.raises(ProcessMiningCSVError):mine_csv_events(sample(),participant_labels_authorized=flag)
    with pytest.raises(ProcessMiningCSVError):parse_csv_events(sample())
    for identity in ('ALICE_SECRET','participant_0','participant_1001','participant_01','team_1'):
        with pytest.raises(ProcessMiningCSVError) as error:
            mine_csv_events(sample().replace(b'participant_1',identity.encode()),participant_labels_authorized=True)
        assert 'ALICE_SECRET' not in str(error.value)


def test_unknown_and_tied_event_labels_do_not_invent_handoffs():
    data=b'''case_id,activity,timestamp,participant
x,Open,2026-10-20T00:00:00Z,participant_1
x,Review,2026-10-20T00:00:00Z,participant_2
x,Check,2026-10-20T00:01:00Z,
x,Close,2026-10-20T00:02:00Z,participant_1
'''
    result=mine_csv_events(data,participant_labels_authorized=True)['participant_labels']['dimensions_summary']['participant']
    assert result['adjacent_label_change_count']==0 and result['changes']==[]
    assert result['ambiguous_tied_timestamp_changes']==1
    assert result['unknown_adjacent_pairs']==2 and result['missing_label_events']==1


def test_changes_after_a_tied_time_group_have_no_verified_predecessor():
    raw=b'case_id,activity,timestamp,participant\nx,Open,2026-10-20T00:00:00Z,participant_1\nx,Review,2026-10-20T00:00:00Z,participant_2\nx,Close,2026-10-20T00:01:00Z,participant_3\n'
    result=mine_csv_events(raw,participant_labels_authorized=True)['participant_labels']['dimensions_summary']['participant']
    assert result['adjacent_label_change_count']==0
    assert result['ambiguous_tied_timestamp_changes']==2


def test_cohorts_match_both_labels_on_one_event_and_preserve_other_participants(logs):
    store,connect=logs
    with pytest.raises(ProcessMiningCSVError):store.save(sample(),retention_days=7,confirmed=True)
    saved=store.save(sample(),retention_days=7,confirmed=True,participant_labels_authorized=True)
    identifier=saved['dataset_id']
    assert not saved['native_participant_identifiers_retained']
    with connect() as conn:before='\n'.join(conn.iterdump())
    selected=store.cohort(identifier,contains_participant='participant_2',contains_team='team_2')
    assert selected['selection']['selected_case_count']==1 and selected['selection']['selected_event_count']==3
    assert selected['report']['cases'][0]['case']=='Case 1'
    assert [event['participant'] for event in selected['report']['cases'][0]['events']]==['participant_1','participant_2','participant_1']
    assert store.cohort(identifier,contains_participant='participant_1',contains_team='team_2')['report'] is None
    assert [row['case_number'] for row in store.cases(identifier,contains_team='team_1')['cases']]==[1,2]
    assert store.events(identifier,1)['events'][1]['team']=='team_2'
    assert store.report(identifier)['participant_labels']['dimensions']==['participant','team']
    for wrong in ('ALICE_SECRET','participant_1001',"participant_1' OR 1=1"):
        with pytest.raises(ProcessLogError):store.cohort(identifier,contains_participant=wrong)
    with connect() as conn:assert '\n'.join(conn.iterdump())==before and 'PRIVATE_' not in before
    store.forget(identifier,confirmed=True)
    with connect() as conn:assert conn.execute('SELECT COUNT(*) FROM process_log_events').fetchone()[0]==0


def test_label_support_and_late_cohorts_use_all_cases_and_keep_import_aliases(logs):
    store,_=logs;stream=StringIO();writer=csv.writer(stream)
    writer.writerow(('case_id','activity','timestamp','participant','team'))
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    for i in range(40):
        for j,activity in enumerate(('Open','Close')):
            writer.writerow((f'PRIVATE_{i}',activity,(start+timedelta(minutes=i,seconds=j)).isoformat(),
                'participant_1' if i<35 else 'participant_9','team_1'))
    saved=store.save(stream.getvalue().encode(),retention_days=7,confirmed=True,participant_labels_authorized=True)
    result=store.cohort(saved['dataset_id'],contains_participant='participant_9')
    assert result['selection']['selected_case_count']==5
    assert [row['case'] for row in result['report']['cases']]==[f'Case {i}' for i in range(36,41)]
    assert result['report']['participant_labels']['dimensions_summary']['participant']['labels']==[
        {'label':'participant_9','event_count':10,'case_count':5}]
    assert result['report']['variants'][0]['variant_id']=='variant_1'


def test_structural_snapshots_upgrade_without_gaining_participant_claims(logs):
    store,connect=logs
    raw=b'case_id,activity,timestamp\nx,Open,2026-10-20T00:00:00Z\nx,Close,2026-10-20T00:01:00Z\n'
    saved=store.save(raw,retention_days=7,confirmed=True)['dataset_id']
    # Recreate the prior six-column event table, preserving its rows.
    with connect() as conn:
        conn.execute('ALTER TABLE process_log_events RENAME TO prior_events')
        conn.execute('CREATE TABLE process_log_events (dataset_id TEXT NOT NULL,case_number INTEGER NOT NULL,position INTEGER NOT NULL,activity TEXT NOT NULL,observed_at TEXT NOT NULL,elapsed_since_previous_seconds REAL,PRIMARY KEY(dataset_id,case_number,position))')
        conn.execute('INSERT INTO process_log_events SELECT dataset_id,case_number,position,activity,observed_at,elapsed_since_previous_seconds FROM prior_events')
        conn.execute('DROP TABLE prior_events')
    assert store.events(saved,1)['events'][0]['activity']=='Open'
    assert 'participant' not in store.events(saved,1)['events'][0]
    assert 'participant_labels' not in store.report(saved)
    assert store.cohort(saved)['selection']['selected_event_count']==2


def test_real_api_requires_explicit_dashboard_authorization_before_label_processing(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
raw=b'case_id,activity,timestamp,participant,team\nPRIVATE_CASE,Open,2026-10-20T00:00:00Z,participant_1,team_1\nPRIVATE_CASE,Close,2026-10-20T00:01:00Z,participant_2,team_2\n'
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session(),'Content-Type':'text/csv'}
approved={**headers,'X-OWG-Participant-Labels-Authorized':'true'}
base='/v1/process-mining'
with TestClient(app) as client:
    assert client.post(base+'/preview',headers=headers,content=raw).status_code==422
    token=local_auth.ensure_api_token()
    bearer={**approved,'Authorization':'Bearer '+token}
    structural=b'case_id,activity,timestamp\nx,Open,2026-10-20T00:00:00Z\n'
    assert client.post(base+'/preview',headers={key:value for key,value in bearer.items() if key!='X-OWG-Participant-Labels-Authorized'},content=structural).status_code==200
    assert client.post(base+'/preview',headers=bearer,content=raw).status_code==403
    from unittest.mock import patch
    with patch('server.workflow_evidence_routes._catalog_record',side_effect=AssertionError('No private model read')):
        assert client.post(base+'/preview?workflow_id=private&revision=1',headers=bearer,content=raw).status_code==403
    result=client.post(base+'/preview',headers=approved,content=raw)
    assert result.status_code==200,result.text
    assert result.headers['cache-control']=='no-store' and 'PRIVATE_' not in result.text
    native=client.post(base+'/preview',headers=approved,content=raw.replace(b'participant_1',b'ALICE_SECRET'))
    assert native.status_code==422 and 'ALICE_SECRET' not in native.text
    saved=client.post(base+'/datasets?retention_days=7',headers={**approved,'X-OWG-Retention-Confirmed':'true'},content=raw)
    assert saved.status_code==200,saved.text
    query={'dataset_id':saved.json()['dataset_id'],'contains_participant':'participant_2','contains_team':'team_2'}
    cohort=client.get(base+'/datasets/cohort',headers=headers,params=query)
    assert cohort.status_code==200,cohort.text
    assert cohort.json()['selection']['selected_event_count']==2
    cases=client.get(base+'/datasets/cases',headers=headers,params=query)
    assert cases.status_code==200 and len(cases.json()['cases'])==1
    assert client.get(base+'/datasets/cases',headers=bearer,params=query).status_code==403
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    run=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stdout+run.stderr
