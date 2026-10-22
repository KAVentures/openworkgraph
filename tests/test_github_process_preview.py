"""Selected GitHub histories reuse canonical identity and separate source consent."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from server import record_graph as graph
from server.github_process_preview import github_selected_cases_process
from test_record_graph_source_join_v0134 import _row, REF_A, REF_B


def snapshot(monkeypatch, reviews=None, state='MERGED', merged='2026-10-20T10:10:00Z', closed=None):
    monkeypatch.setattr(graph.outcome_tracker,'enabled',lambda:True)
    monkeypatch.setattr(graph,'resolve_resource_reference',lambda ref:{'provider':'github','resource_kind':'pull_request',
        'host':'github.com','resolver_locator':'PRIVATE_OWNER/PRIVATE_REPO/pull/17'})
    report={'state':state,'mergedAt':merged,'closedAt':closed,'reviews':reviews if reviews is not None else [
        {'id':'PRR_PRIVATE_1','state':'CHANGES_REQUESTED','submittedAt':'2026-10-20T10:00:00Z','body':'PRIVATE_REVIEW_TEXT'},
        {'id':'PRR_PRIVATE_2','state':'APPROVED','submittedAt':'2026-10-20T10:05:00Z','author':{'login':'PRIVATE_ACTOR'}}]}
    return graph._gh_pr_snapshot(REF_A,which=lambda _:'/owned/gh',
        runner=lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps(report)))


def test_source_projection_uses_minimized_review_ids_and_existing_lifecycle_metadata(monkeypatch):
    receipt=snapshot(monkeypatch)
    assert [row['action'] for row in receipt['process_audit_events']]==[
        'review_changes_requested','review_approved','pull_request_merged']
    assert 'PRIVATE_' not in json.dumps(receipt)
    report=github_selected_cases_process([receipt])
    assert report['process']['variants'][0]['observed_path']==['Changes requested','Review approved','Pull request merged']
    assert report['process']['case_count']==1 and report['process']['input_rows']==3
    assert report['process']['metrics']['median_case_elapsed_seconds']==600
    assert report['all_selected_source_audits_complete'] is False
    assert report['process']['source_event_window_may_be_partial'] is True
    for flag in ('result_persisted','tenant_inventory_scanned','cross_case_identity_inferred','business_outcomes_verified'):
        assert report[flag] is False
    assert 'source_event:' not in json.dumps(report) and REF_A not in json.dumps(report)


def test_review_duplicates_conflicts_and_states_without_action_timestamps_are_not_invented(monkeypatch):
    rows=[{'id':'PRR_same','state':'APPROVED','submittedAt':'2026-10-20T10:00:00Z'}]
    rows += deepcopy(rows)
    rows += [{'id':'PRR_distinct','state':'APPROVED','submittedAt':'2026-10-20T10:00:00Z'},
        {'id':'PRR_pending','state':'PENDING','submittedAt':'2026-10-20T10:01:00Z'},
        {'id':'PRR_dismissed','state':'DISMISSED','submittedAt':'2026-10-20T10:02:00Z'},
        {'id':'PRR_invalid','state':{},'submittedAt':'2026-10-20T10:03:00Z'},
        {'id':'PRR_missing','state':'COMMENTED','submittedAt':None}]
    receipt=snapshot(monkeypatch,rows)
    assert [row['action'] for row in receipt['process_audit_events']].count('review_approved')==2
    report=github_selected_cases_process([receipt])
    assert report['process']['input_rows']==3
    assert report['process']['data_quality']['cases_with_tied_timestamps']==1
    conflicted=[{'id':'PRR_conflict','state':state,'submittedAt':'2026-10-20T10:00:00Z'}
                for state in ('APPROVED','CHANGES_REQUESTED')]
    assert [row['action'] for row in snapshot(monkeypatch,conflicted)['process_audit_events']]==['pull_request_merged']
    closed=snapshot(monkeypatch,[],state='CLOSED',merged=None,closed='2026-10-20T11:00:00Z')
    assert closed['process_audit_events'][0]['action']=='pull_request_closed_unmerged'
    assert snapshot(monkeypatch,[],state='CLOSED',merged=None,closed='PRIVATE_BAD_TIMESTAMP')['process_audit_events']==[]
    for malformed in ('PRIVATE_INVALID_MERGE', True, {'unexpected':'value'}):
        invalid=snapshot(monkeypatch,[],state='CLOSED',merged=malformed)
        assert invalid['availability']=='unknown' and invalid['source_state_reported'] is False


def test_provider_type_missing_conflicting_and_truncated_proof_block_source_reads():
    calls=[]
    source=lambda ref:calls.append(ref) or {'provider':'github','availability':'source_reported'}
    wanted=('github','pull_request','github.com')
    for rows,limit in [([],25000),([_row(1,provider='jira',kind='issue')],25000),
                       ([_row(1),_row(2,provider='jira')],25000),([_row(1),_row(2)],1)]:
        result=graph.record_graph(REF_A,_raw_events=rows,include_source_status=True,source_reader=source,
                                 source_event_limit=limit,required_source_type=wanted)
        assert result['source_evidence']['availability']=='unknown'
    assert calls==[]
    graph.record_graph(REF_A,_raw_events=[_row(1)],include_source_status=True,source_reader=source,required_source_type=wanted)
    assert calls==[REF_A]


def test_selected_reference_cases_remain_separate_and_missing_receipts_do_not_add_events(monkeypatch):
    receipt=snapshot(monkeypatch)
    second=deepcopy(receipt);second['process_audit_events']=second['process_audit_events'][1:]
    report=github_selected_cases_process([receipt,None,second])
    assert report['audited_record_count']==2 and report['unavailable_or_empty_record_count']==1
    assert report['process']['case_count']==2 and report['process']['variant_count']==2
    assert report['source_case_ids_included'] is False
    assert github_selected_cases_process([None])['process'] is None


def test_http_authorization_bounds_json_and_retained_canonical_identity(tmp_path):
    code=r'''
import json
from datetime import datetime,timezone
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth,record_graph,db
from test_record_graph_source_join_v0134 import _row,REF_A,REF_B
called=[]
def source(ref):
 called.append(ref)
 return {'provider':'github','object_type':'pull_request','availability':'source_reported','process_audit_events':[
 {'action':'review_approved','observed_at':'2026-10-20T10:00:00Z','source_event_ref':'source_event:'+'a'*32},
 {'action':'pull_request_merged','observed_at':'2026-10-20T10:10:00Z','source_event_ref':'source_event:'+'b'*32}]}
record_graph._gh_pr_snapshot=source
with TestClient(app) as client:
 auth={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
 url='/v1/process-mining/github-selected-cases';body={'resource_refs':[REF_A],'confirm_source_reads':True}
 assert client.post(url,json=body).status_code==401
 assert client.post(url,headers=bearer,json=body).status_code==403
 for changed,status in [({'confirm_source_reads':False},403),({'resource_refs':[REF_A,REF_A]},422),
  ({'resource_refs':['PRIVATE_NATIVE_ID']},422),({'resource_refs':[]},422)]:
  assert client.post(url,headers=auth,json=body|changed).status_code==status
 for raw in (b'{"confirm_source_reads":false,"confirm_source_reads":true}',b'{"resource_refs":NaN}'):
  assert client.post(url,headers=auth|{'Content-Type':'application/json'},content=raw).status_code==422
 assert client.post(url,headers=auth|{'Content-Type':'application/json'},content=b'x'*2049).status_code==413
 missing=client.post(url,headers=auth,json=body);assert missing.status_code==200 and missing.json()['process'] is None
 assert called==[]
 event=_row(1);event.update(device_id='synthetic',app='Browser',window_title='Owned workflow',duration_seconds=1)
 event['observed_at']=datetime.now(timezone.utc).isoformat();db.insert_events([event])
 good=client.post(url,headers=auth,json=body)
 assert good.status_code==200,good.text
 assert good.json()['process']['case_count']==1 and good.json()['process']['input_rows']==2
 assert good.headers['cache-control']=='no-store' and REF_A not in good.text
 assert called==[REF_A]
 with db.connect() as c:assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==1
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),
        'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json'),
        'PYTHONPATH':str(Path(__file__).resolve().parent)}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,
                          capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
