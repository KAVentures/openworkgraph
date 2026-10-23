"""Per-activity support uses full selected event histories, never examples or people inference."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from io import StringIO
import csv
import json
import sqlite3

from server.process_mining_csv import mine_csv_events
from server.process_log_store import ProcessLogStore


def test_activity_and_exact_variant_support_preserve_unknowns_and_overlapping_case_counts():
    raw=b'''case_id,activity,timestamp,participant,team,origin
case_key_1,Open,2026-10-20T00:00:00Z,participant_1,team_1,source_1
case_key_1,Review,2026-10-20T00:01:00Z,participant_1,team_1,source_1
case_key_1,Review,2026-10-20T00:02:00Z,participant_2,,source_2
case_key_1,Close,2026-10-20T00:03:00Z,participant_1,team_1,source_2
case_key_2,Open,2026-10-20T00:00:00Z,participant_1,team_2,source_1
case_key_2,Review,2026-10-20T00:01:00Z,,team_2,source_2
case_key_2,Close,2026-10-20T00:02:00Z,participant_2,team_2,source_2
'''
    result=mine_csv_events(raw,participant_labels_authorized=True,case_links_authorized=True)
    review=next(row for row in result['activities'] if row['activity']=='Review')
    assert review['event_count']==3 and review['case_count']==2
    p=review['label_support']['participant']
    assert p['labels']==[{'label':'participant_1','event_count':1,'case_count':1},
                         {'label':'participant_2','event_count':1,'case_count':1}]
    assert p['labeled_event_count']==2 and p['missing_label_event_count']==1 and p['missing_label_case_count']==1
    assert p['label_count']==2 and not p['case_support_counts_are_additive']
    assert not p['identity_and_membership_verified'] and not p['effort_or_handoffs_verified']
    origin=review['label_support']['origin']
    assert origin['labels']==[{'label':'source_2','event_count':2,'case_count':2},
                              {'label':'source_1','event_count':1,'case_count':1}]
    assert origin['missing_label_event_count']==0
    repeated=next(v for v in result['variants'] if v['path_event_count']==4)
    short=next(v for v in result['variants'] if v['path_event_count']==3)
    a=next(row for row in repeated['activities'] if row['activity']=='Review')['label_support']['participant']
    b=next(row for row in short['activities'] if row['activity']=='Review')['label_support']['participant']
    assert a['labeled_event_count']==2 and a['missing_label_event_count']==0
    assert b['label_count']==0 and b['missing_label_event_count']==1 and b['labels']==[]
    assert sum(row['case_count'] for row in p['labels'])+p['missing_label_case_count']>review['case_count']
    json.dumps(result)  # No private case sets or internal counters escape.


def test_hidden_steps_unsampled_cases_display_caps_and_whole_case_cohort_reconstruction(tmp_path):
    out=StringIO(newline='');writer=csv.writer(out)
    writer.writerow(('case_id','activity','timestamp','participant','team','origin'))
    start=datetime(2026,10,20,tzinfo=timezone.utc)
    for number in range(1,41):
        for position in range(27):
            activity='Open' if position<25 else 'Review' if position==25 else 'Close'
            writer.writerow(('case_key_'+str(number),activity,(start+timedelta(minutes=number,seconds=position)).isoformat(),
                'participant_'+str(number) if activity=='Review' else 'participant_1',
                'team_2' if position>=25 else 'team_1','source_2' if position>=25 else 'source_1'))
    raw=out.getvalue().encode();result=mine_csv_events(raw,participant_labels_authorized=True,case_links_authorized=True)
    assert result['variants'][0]['path_truncated'] and result['variants'][0]['case_examples_truncated']
    global_row=next(row for row in result['activities'] if row['activity']=='Review')
    variant_row=next(row for row in result['variants'][0]['activities'] if row['activity']=='Review')
    assert global_row['label_support']==variant_row['label_support']
    support=global_row['label_support']['participant']
    assert support['label_count']==40 and support['labeled_event_count']==40
    assert support['rows_returned']==20 and support['rows_truncated'] and len(support['labels'])==20
    assert global_row['label_support']['origin']['labels']==[{'label':'source_2','event_count':40,'case_count':40}]
    @contextmanager
    def connect():
        conn=sqlite3.connect(tmp_path/'logs.db');conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()
    store=ProcessLogStore(connect)
    identifier=store.save(raw,retention_days=7,confirmed=True,participant_labels_authorized=True,case_links_authorized=True)['dataset_id']
    cohort=store.cohort(identifier,started_since=(start+timedelta(minutes=36)).isoformat(),
        started_before=(start+timedelta(minutes=40,seconds=1)).isoformat())['report']
    assert cohort['case_count']==5 and cohort['input_rows']==135
    selected=next(row for row in cohort['activities'] if row['activity']=='Review')['label_support']
    assert selected['participant']['label_count']==5 and not selected['participant']['rows_truncated']
    assert {row['label'] for row in selected['participant']['labels']}=={'participant_'+str(n) for n in range(36,41)}
    assert selected['origin']['labels']==[{'label':'source_2','event_count':5,'case_count':5}]
    assert cohort['cases'][0]['case']=='Case 36'


def test_no_metadata_is_inferred_for_structural_logs_and_blank_labels_remain_unknown():
    raw=b'case_id,activity,timestamp\na,Open,2026-10-20T00:00:00Z\n'
    normal=mine_csv_events(raw)
    assert all('label_support' not in row for row in normal['activities'])
    assert all('label_support' not in row for v in normal['variants'] for row in v['activities'])
    blanks=b'case_id,activity,timestamp,participant,team\na,Open,2026-10-20T00:00:00Z,,\na,Open,2026-10-20T00:01:00Z,,\n'
    rich=mine_csv_events(blanks,participant_labels_authorized=True)
    support=rich['activities'][0]['label_support']
    assert set(support)=={'participant','team'}
    for dim in support.values():
        assert dim['labels']==[] and dim['label_count']==0 and dim['labeled_event_count']==0
        assert dim['missing_label_event_count']==2 and dim['missing_label_case_count']==1


def test_existing_retained_label_rows_regenerate_missing_projection_without_rewriting_snapshot(tmp_path):
    raw=b'case_id,activity,timestamp,participant\na,Open,2026-10-20T00:00:00Z,participant_1\na,Open,2026-10-20T00:01:00Z,participant_2\n'
    @contextmanager
    def connect():
        conn=sqlite3.connect(tmp_path/'legacy.db');conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()
    store=ProcessLogStore(connect)
    identifier=store.save(raw,retention_days=7,confirmed=True,participant_labels_authorized=True)['dataset_id']
    expected=store.report(identifier)
    with connect() as conn:
        old=json.loads(conn.execute('SELECT report_json FROM process_log_datasets').fetchone()[0])
        for row in old['activities']:row.pop('label_support')
        for variant in old['variants']:
            for row in variant['activities']:row.pop('label_support')
        conn.execute('UPDATE process_log_datasets SET report_json=?',(json.dumps(old),))
    with connect() as conn:before='\n'.join(conn.iterdump())
    rebuilt=store.report(identifier)
    assert rebuilt['activities']==expected['activities']
    assert rebuilt['variants']==expected['variants']
    assert rebuilt['data_quality']==old['data_quality']
    assert 'conformance' not in rebuilt
    with connect() as conn:assert '\n'.join(conn.iterdump())==before
