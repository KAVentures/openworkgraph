"""Saved-graph conformance keeps full case denominators and bounded unknown work."""
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from server import event_log_model_conformance as graph
from server import event_log_workspace as workspace
from test_process_catalog import model
from test_parallel_process_model import parallel_model


@pytest.fixture(autouse=True)
def local_workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)


def imported(paths,tied=False):
    out=StringIO();writer=csv.writer(out);writer.writerow(['case','activity','time','who'])
    start=datetime(2024,1,1,tzinfo=timezone.utc)
    for case,path in enumerate(paths):
        for index,activity in enumerate(path):
            stamp=start+timedelta(seconds=0 if tied and case==0 else index)
            writer.writerow([str(case),activity,stamp.isoformat(),'bot' if case<4 and index==0 else 'person'])
    return workspace.import_log(BytesIO(out.getvalue().encode()),name='graph',source_format='csv',mapping={
        'case_column':'case','activity_column':'activity','timestamp_column':'time','resource_column':'who'})['log_id']


def packet(process=None):
    return {'workflow_id':'review','revision':1,'content_sha256':'a'*64,'title':'Case review',
            'saved_at':'2024-01-01T00:00:00Z','process_model':(process or model()).model_dump()}


def selection(**changes):
    return graph.SavedGraphQuery.model_validate({'workflow_id':'review','revision':1,'content_sha256':'a'*64,**changes})


def test_weighted_exact_graph_comparison_and_whole_case_cohorts():
    paths=[['Open','Review','Close']]*4+[['Open','Close']]*2+[['Review','Open','Close']]+[['Open','Review ','Close']]*3
    log=imported(paths,tied=True)
    result=graph.compare(log,packet(),selection())
    assert (result['cases'],result['analyzed_cases'],result['structural_match_cases'],result['structural_deviation_cases'],result['unknown_cases'])==(10,10,4,6,0)
    assert result['ordering_uncertain_cases']==1
    assert result['analysis_coverage']==1 and result['comparison_complete']
    assert result['structural_fitness']['score']==1-10/58
    assert result['visible_prefix_precision']['score']==1
    assert result['visible_prefix_precision']['case_population_coverage']==0.4
    assert result['process_reference']['revision']==1
    assert not result['business_rules_evaluated'] and not result['policy_compliance_verified']
    assert not result['business_outcome_verified'] and not result['execution_authorized']
    assert any(v['path']==['Open','Review ','Close'] and not v['exact_structural_match'] for v in result['displayed_variants'])
    cohort=graph.compare(log,packet(),selection(cohort={'resource':'bot'}))
    assert cohort['cases']==4 and cohort['structural_match_cases']==4


def test_limits_leave_cases_unknown_and_do_not_clip_them_into_matches(monkeypatch):
    log=imported([['Open','Review','Close']]*4+[['Open','Close']]*2+[['Open']*301]*3)
    result=graph.compare(log,packet(),selection(max_variants=1))
    assert (result['structural_match_cases'],result['structural_deviation_cases'],result['unknown_cases'])==(4,0,5)
    assert result['unknown_case_reasons']=={'trace_length_limit':3,'variant_limit':2,'alignment_work_budget':0}
    assert result['analysis_coverage']==4/9 and result['structural_match_share_of_analyzed_cases']==1
    assert result['structural_match_share_of_all_cases_lower_bound']==4/9 and not result['comparison_complete']
    monkeypatch.setattr(graph,'MAX_ALIGNMENT_WORK',0)
    result=graph.compare(log,packet(),selection())
    assert result['analyzed_cases']==0 and result['unknown_cases']==9
    assert result['unknown_case_reasons']['alignment_work_budget']==6
    assert result['structural_fitness']['score'] is None
    assert result['structural_fitness']['lower_bound']==0 and result['structural_fitness']['upper_bound']==1
    assert result['visible_prefix_precision']['score'] is None
    assert all(v['exact_structural_match'] is None and v['minimum_activity_edit_count'] is None for v in result['displayed_variants'])


def test_legacy_alignment_is_not_called_when_the_state_space_exceeds_budget(monkeypatch):
    log=imported([['Open','Review','Close']]*4+[['Open','Close']]*2)
    monkeypatch.setattr(graph,'MAX_ALIGNMENT_WORK',16)  # One four-position, four-node state space.
    result=graph.compare(log,packet(),selection())
    assert result['analyzed_cases']==4 and result['unknown_cases']==2
    assert result['structural_match_cases']==4
    monkeypatch.setattr(graph,'MAX_ALIGNMENT_WORK',0)
    def forbidden(*args,**kwargs):raise AssertionError('unbounded legacy alignment must not start')
    monkeypatch.setattr(graph,'compare_path',forbidden)
    assert graph.compare(log,packet(),selection())['unknown_cases']==6


def test_parallel_saved_graph_accepts_interleavings_and_requires_every_branch(monkeypatch):
    paths=[['A1','A2','B1','B2','Close'],['A1','B1','A2','B2','Close'],['B1','B2','A1','A2','Close'],['A1','A2','Close']]
    log=imported(paths)
    result=graph.compare(log,packet(parallel_model()),selection())
    assert result['structural_match_cases']==3 and result['structural_deviation_cases']==1
    monkeypatch.setattr(graph,'MAX_ALIGNMENT_WORK',0)
    result=graph.compare(log,packet(parallel_model()),selection())
    assert result['unknown_cases']==4 and result['structural_deviation_cases']==0


def test_empty_cohort_and_mismatched_reference_do_not_create_verdicts():
    log=imported([['Open','Review','Close']])
    result=graph.compare(log,packet(),selection(cohort={'resource':'absent'}))
    assert result['cases']==0 and result['analysis_coverage'] is None
    assert result['structural_match_share_of_analyzed_cases'] is None
    for changes in [{'workflow_id':'other'},{'revision':2},{'content_sha256':'b'*64}]:
        with pytest.raises(workspace.EventLogWorkspaceError,match='does not match'):
            graph.compare(log,packet(),selection(**changes))


def test_saved_graph_comparison_http_pins_revisions_and_refuses_ai_access(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth,event_log_model_conformance as graph
from server import workflow_evidence_routes as routes
model={'goal':'Review the declared case','baseline_source':'user_declared','baseline_path':['Open','Review','Close'],
       'activities':[{'id':x.lower(),'label':x} for x in ['Open','Review','Close']],
       'transitions':[{'source':'open','target':'review'},{'source':'review','target':'close'}],
       'entry_activity_id':'open','terminal_activity_ids':['close']}
with TestClient(app) as client:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 made=client.post('/v1/event-logs/import?format=csv&name=g&case_column=case&activity_column=activity&timestamp_column=time',content=b'case,activity,time\nC,Open,2024-01-01T00:00:00Z\nC,Review,2024-01-01T00:01:00Z\nC,Close,2024-01-01T00:02:00Z\n',headers={**h,'X-OWG-Local-Storage-Confirmed':'true'})
 assert made.status_code==200,made.text
 url='/v1/event-logs/'+made.json()['log_id']+'/process-conformance'
 save={'workflow_id':'review','title':'Review case','user_confirmed':True,'process_model':model,'expected_revision':0}
 first=client.post('/v1/process-catalog',json=save,headers=h)
 assert first.status_code==200,first.text
 record=first.json();spec={key:record[key] for key in ['workflow_id','revision','content_sha256']}
 assert client.post(url,json=spec).status_code==401
 assert client.post(url,json=spec,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code==403
 answer=client.post(url,json=spec,headers=h)
 assert answer.status_code==200,answer.text
 assert answer.json()['structural_match_cases']==1 and answer.headers['cache-control']=='no-store'
 model['activities'][1]['label']='Check'
 second=client.post('/v1/process-catalog',json={**save,'expected_revision':1,'process_model':model},headers=h)
 assert second.status_code==200,second.text
 assert client.post(url,json=spec,headers=h).json()['structural_match_cases']==1
 assert client.post(url,json={key:second.json()[key] for key in spec},headers=h).json()['structural_deviation_cases']==1
 assert client.post(url,json={**spec,'content_sha256':'b'*64},headers=h).status_code==409
 assert client.post(url,json={**spec,'revision':999},headers=h).status_code==404
 assert client.post(url,json={**spec,'revision':True},headers=h).status_code==422
 assert client.post(url,json={**spec,'owner':'foreign'},headers=h).status_code==422
 original=graph.compare
 def deleting(*args,**kwargs):
  result=original(*args,**kwargs);routes._knowledge.forget('local','review');return result
 graph.compare=deleting
 assert client.post(url,json=spec,headers=h).status_code==404
 from server import db
 with db.connect() as conn:assert conn.execute('SELECT count(*) FROM events').fetchone()[0]==0
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),
         'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr


def test_oversized_history_is_not_clipped_into_a_matching_prefix(monkeypatch):
    monkeypatch.setattr(graph,'MAX_TRACE_LENGTH',3)
    log=imported([['Open','Review','Close'],['Open','Review','Close','Extra']])
    result=graph.compare(log,packet(),selection())
    assert result['structural_match_cases']==1 and result['unknown_cases']==1
    assert result['structural_deviation_cases']==0
    assert result['unknown_case_reasons']['trace_length_limit']==1


def test_possible_incomplete_prefix_is_not_lost_from_structural_deviations():
    log=imported([['Open','Review'],['Open','Close']])
    result=graph.compare(log,packet(),selection())
    variants={tuple(v['path']):v for v in result['displayed_variants']}
    assert variants[('Open','Review')]['could_be_incomplete_log_prefix'] is True
    assert variants[('Open','Close')]['could_be_incomplete_log_prefix'] is False
    assert result['structural_deviation_cases']==2 and not result['policy_compliance_verified']


def test_parallel_branch_entry_cannot_deadlock_a_saved_graph_comparison():
    from pydantic import ValidationError
    from shared.process_model import ProcessModel
    value=parallel_model().model_dump()
    for node in value['activities']:
        if node['id']=='close':node['kind']='decision'
    value['activities'].append({'id':'done','label':'Done','kind':'end'})
    value['transitions'] += [{'source':'close','target':'fork','rule_id':'again'},
                            {'source':'close','target':'done','rule_id':'finish'}]
    value['business_rules']=[{'id':'again','text':'Repeat checks','user_confirmed':True},
                             {'id':'finish','text':'Finish checks','user_confirmed':True}]
    value['entry_activity_id']='A1';value['terminal_activity_ids']=['done']
    with pytest.raises(ValidationError,match='cannot enter a branch'):
        ProcessModel.model_validate(value)
    old=packet();old['process_model']=value
    with pytest.raises(workspace.EventLogWorkspaceError,match='cannot be compared'):
        graph.compare('log_not_read',old,selection())
    # An explicit entry before the fork preserves the valid exclusive outer loop.
    value['activities'].append({'id':'start','label':'Start','kind':'start'})
    value['transitions'] += [{'source':'start','target':'fork'}]
    # Forks require one arrival source; route the initial arrival through the
    # existing exclusive decision instead of adding a second split arrival.
    value['transitions'][-1]={'source':'start','target':'close'}
    value['entry_activity_id']='start'
    valid=ProcessModel.model_validate(value)
    from shared.process_conformance import compare_path
    assert compare_path(('A1','A2','B1','B2'),valid)['exact_structural_match'] is True


def test_precision_budget_does_not_change_completed_alignment_verdicts(monkeypatch):
    from shared import process_metrics
    monkeypatch.setattr(process_metrics,'MAX_PRECISION_WORK',0)
    log=imported([['Open','Review','Close']])
    result=graph.compare(log,packet(),selection())
    assert result['comparison_complete'] and result['structural_match_cases']==1
    assert result['structural_fitness']['score']==1
    assert result['visible_prefix_precision']['score'] is None
    assert result['visible_prefix_precision']['unknown_reason']=='precision_work_budget'
