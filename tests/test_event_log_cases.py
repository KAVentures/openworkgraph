"""Whole-case evidence pages over large imported logs, without canonical capture."""
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from server import event_log_cases as pages
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def imported(rows):
    body='case,activity,time,who\n'+'\n'.join(rows)
    return workspace.import_log(BytesIO(body.encode()), name='cases', source_format='csv', mapping={
        'case_column':'case','activity_column':'activity','timestamp_column':'time','resource_column':'who'})['log_id']


def test_case_pages_cover_full_population_and_preserve_whole_case_filters():
    log=imported([f'c{i:03},{a},2024-01-01T0{n}:00:00Z,{"bot" if a=="B" else "person"}'
                  for i in range(75) for n,a in enumerate(['A','B','A'] if i%2 else ['A','Close'])])
    result=pages.cases(log, {'cohort':{'activity':'B','resource':'person'},'limit':7})
    assert result['selected_cases']==37
    keys=[]
    while True:
        keys.extend(row['case_id'] for row in result['cases'])
        assert all(row['events']==3 for row in result['cases'])
        if result['next_after'] is None:break
        result=pages.cases(log, {'cohort':{'activity':'B','resource':'person'},'limit':7,'after':result['next_after']})
    assert len(keys)==len(set(keys))==37
    exact=pages.cases(log, {'path':['A','Close']})
    assert exact['selected_cases']==38
    assert pages.cases(log, {'cohort':{'resource':'missing'}})['cases']==[]
    detail=pages.events(log, {'case_id':keys[0]})
    assert [row['activity'] for row in detail['events']]==['A','B','A']
    assert detail['derived_from_supplied_log'] is True
    assert detail['verified_business_workflow'] is False and detail['execution_authorized'] is False


def test_event_pages_include_all_events_and_disclose_equal_timestamps():
    log=imported([f' Case,Step {i},2024-01-01T00:00:00Z, bot ' for i in range(507)] +
                 ['Case ,Other,2024-01-01T00:00:00Z,person'])
    result=pages.cases(log,{})
    assert result['selected_cases']==2
    first=pages.events(log,{'case_id':' Case','limit':250})
    assert first['total_events']==507 and first['timestamp_ties']==506
    assert 'do not verify' in first['ordering']
    rows=first['events'];cursor=first['next_offset']
    while cursor is not None:
        result=pages.events(log,{'case_id':' Case','offset':cursor,'limit':250})
        rows+=result['events'];cursor=result['next_offset']
    assert len(rows)==len({row['event_id'] for row in rows})==507
    assert {row['activity'] for row in rows}=={f'Step {i}' for i in range(507)}
    assert all(row['resource']==' bot ' for row in rows)
    assert pages.events(log,{'case_id':'Case '})['total_events']==1
    other=imported(['Other,A,2024-01-01T00:00:00Z,person'])
    with pytest.raises(workspace.EventLogWorkspaceError):pages.events(other,{'case_id':' Case'})
    workspace.delete_log(log)
    with pytest.raises(workspace.EventLogWorkspaceError):pages.events(log,{'case_id':' Case'})


def test_ocel_cases_are_object_scoped_and_qualifiers_do_not_duplicate_events():
    payload={'objects':[{'id':'o','type':'order'},{'id':'i','type':'item'}],
             'events':[{'id':'e','type':' Review > ','time':'2024-01-01T00:00:00Z','relationships':[
                 {'objectId':'o','qualifier':'created'},{'objectId':'o','qualifier':'reviewed'},{'objectId':'i','qualifier':'item'}]}]}
    log=workspace.import_log(BytesIO(json.dumps(payload).encode()),name='objects',source_format='ocel')['log_id']
    assert pages.cases(log,{},'order')['cases'][0]['case_id']=='o'
    assert pages.events(log,{'case_id':'o'},'order')['total_events']==1
    assert pages.events(log,{'case_id':'i'},'item')['events'][0]['activity']==' Review > '
    with pytest.raises(workspace.EventLogWorkspaceError):pages.events(log,{'case_id':'i'},'order')
    assert pages.cases(log,{'path':[' Review > ']},'order')['selected_cases']==1
    assert pages.cases(log,{'path':['Review >']},'order')['selected_cases']==0


@pytest.mark.parametrize('spec',[None,{'limit':True},{'limit':101},{'after':'x'*201},{'path':[]},{'path':[3]}, {'unknown':1}])
def test_case_queries_reject_unbounded_or_ambiguous_requests(spec):
    with pytest.raises(workspace.EventLogWorkspaceError):pages.cases('not-read',spec)


def test_case_query_http_is_dashboard_only_and_does_not_write_capture(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 response=client.post('/v1/event-logs/import?format=csv&name=e&case_column=case&activity_column=activity&timestamp_column=time',content=b'case,activity,time\nC,A,2024-01-01T00:00:00Z\nC,B,2024-01-01T00:01:00Z\n',headers={**h,'X-OWG-Local-Storage-Confirmed':'true'})
 assert response.status_code==200,response.text
 base='/v1/event-logs/'+response.json()['log_id']
 for suffix,spec in [('/cases/query',{}),('/case-events/query',{'case_id':'C'})]:
  assert client.post(base+suffix,json=spec).status_code==401
  assert client.post(base+suffix,json=spec,headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code==403
  r=client.post(base+suffix,json=spec,headers=h)
  assert r.status_code==200,r.text
  assert r.headers['cache-control']=='no-store'
 assert client.post(base+'/case-events/query',json={'case_id':'C','limit':1},headers=h).json()['next_offset']==1
 assert client.post(base+'/cases/query',json={'path':['A','B']},headers=h).json()['selected_cases']==1
 assert client.post(base+'/cases/query',content=b'x',headers={**h,'Content-Type':'text/plain'}).status_code==415
 assert client.post(base+'/cases/query',content=b' '*70000,headers={**h,'Content-Type':'application/json'}).status_code==413
 from server import db
 with db.connect() as conn: assert conn.execute('SELECT count(*) FROM events').fetchone()[0]==0
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),
         'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('objects',[[],[{'id':'unlinked','type':'order'}]])
def test_unlinked_ocel_keeps_events_without_inventing_case_identity(objects):
    payload={'objects':objects,'events':[{'id':'e','type':'A','time':'2024-01-01T00:00:00Z','relationships':[]}]}
    log=workspace.import_log(BytesIO(json.dumps(payload).encode()),name='unlinked',source_format='ocel')['log_id']
    if objects:
        assert pages.cases(log,{},'order')['selected_cases']==0
    else:
        assert workspace.object_types(log)['object_types']==[]
        with pytest.raises(workspace.EventLogWorkspaceError,match='no case objects'):
            pages.cases(log,{})
    out=StringIO();workspace.export_log(log,'ocel',out)
    assert [e['id'] for e in json.loads(out.getvalue())['events']]==['e']
    with pytest.raises(workspace.EventLogWorkspaceError,match='no events'):
        workspace.import_log(BytesIO(b'<log/>'),name='empty',source_format='xes')
    assert len(workspace.list_logs()['logs'])==1
