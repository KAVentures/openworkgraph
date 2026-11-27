"""Owner-only, revision-aware event lookup for object state checks."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from server import event_log_workspace as workspace
from server import event_log_object_state_events as lookup


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def doc():
    events=[{'id':f'e%_{n:03}','type':'Pay','time':'2024-01-01T12:00:00Z','relationships':[]} for n in range(110)]
    events += [{'id':'other','type':'Pay','time':'2024-02-01T00:00:00Z','relationships':[]},
               {'id':'different','type':'Other','time':'2024-01-01T00:00:00Z','relationships':[]}]
    return {'objectTypes':[],'eventTypes':[],'objects':[],'events':events}


def load():
    return workspace.import_log(BytesIO(json.dumps(doc()).encode()),name='lookup',source_format='ocel')['log_id']


def test_exact_activity_literal_search_paging_dates_and_isolation():
    log=load(); other=load(); request={'activity':'Pay','search':'e%_','limit':50}
    a=lookup.search(log,request);b=lookup.search(log,{**request,'after':a['next_after'],'expected_revision':a['revision']})
    c=lookup.search(log,{**request,'after':b['next_after'],'expected_revision':a['revision']})
    assert [x['event_id'] for x in a['events']+b['events']+c['events']]==[f'e%_{n:03}' for n in range(110)]
    assert c['next_after'] is None and a['events'][0]['at']=='2024-01-01T12:00:00Z'
    assert lookup.search(log,{'activity':'Pay','search':'%'})['events']
    assert lookup.search(log,{'activity':'Pay','search':"%' OR 1=1 --"})['events']==[]
    assert lookup.search(log,{'activity':'Other'})['events'][0]['event_id']=='different'
    assert [x['event_id'] for x in lookup.search(log,{'activity':'Pay','from_date':'2024-02-01'})['events']]==['other']
    assert lookup.search(log,{'activity':'Pay','search':'other','until_date':'2024-02-01'})['events']==[]
    conn=workspace._connect()
    try:conn.execute("INSERT INTO events VALUES (?, 'unique', 'Pay', TIMESTAMP '2024-01-01', '', '{}')",[log])
    finally:conn.close()
    assert lookup.search(other,{'activity':'Pay','search':'unique'})['events']==[]
    for bad in ({'activity':''},{'activity':'Pay','after':'x'},{'activity':'Pay','limit':True},
                {'activity':'Pay','from_date':'2024-02-30'}, {'activity':'Pay','from_date':'2024-02-01','until_date':'2024-02-01'},
                {'activity':'Pay','search':'\x00'}, {'activity':'Pay','extra':True}):
        with pytest.raises(workspace.EventLogWorkspaceError):lookup.search(log,bad)
    newer={'objectTypes':[],'eventTypes':[],'objects':[],
           'events':[{'id':'new','type':'Pay','time':'2024-03-01T00:00:00Z','relationships':[]}]}
    workspace.append_log(log,BytesIO(json.dumps(newer).encode()),source_format='ocel')
    with pytest.raises(workspace.EventLogWorkspaceError,match='changed'):
        lookup.search(log,{**request,'after':a['next_after'],'expected_revision':a['revision']})


def test_one_snapshot_across_append(monkeypatch):
    log=load();original=lookup._revision;seen=[]
    def interleave(conn,log_id):
        revision=original(conn,log_id)
        if not seen:
            seen.append(True)
            newer={'objectTypes':[],'eventTypes':[],'objects':[],
                   'events':[{'id':'new','type':'Pay','time':'2024-03-01T00:00:00Z','relationships':[]}]}
            workspace.append_log(log,BytesIO(json.dumps(newer).encode()),source_format='ocel')
        return revision
    monkeypatch.setattr(lookup,'_revision',interleave)
    before=lookup.search(log,{'activity':'Pay','search':'new'})
    assert before['revision']==0 and before['events']==[]
    after=lookup.search(log,{'activity':'Pay','search':'new'})
    assert after['revision']==1 and after['events'][0]['event_id']=='a1:new'


def test_owner_only_http_and_strict_body(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_object_state_events import load
with TestClient(app) as c:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    log=load();url='/v1/event-logs/'+log+'/object-state-check/events/search'
    assert c.post(url,json={'activity':'Pay'},headers=bearer).status_code in (401,403)
    r=c.post(url,json={'activity':'Pay'},headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store',r.text
    assert len(r.json()['events'])==50
    assert c.post(url,content=b'{"activity":"Pay","activity":"Other"}',headers={**owner,'content-type':'application/json'}).status_code==422
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth')}
    result=subprocess.run([sys.executable,'-c',"import sys;sys.path.insert(0,'tests');"+code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr


def test_lookup_restricts_object_scope_and_exact_qualifier():
    from test_event_log_object_state_check import fixture
    log=workspace.import_log(BytesIO(json.dumps(fixture()).encode()),name='object scope',source_format='ocel')['log_id']
    request={'activity':'Pay','scope_object_id':'a','object_type':'order'}
    assert {r['event_id'] for r in lookup.search(log,request)['events']}=={'e1','e2','e3','e8'}
    assert [r['event_id'] for r in lookup.search(log,{**request,'event_qualifier':'payer'})['events']]==['e1']
    assert len(lookup.search(log,{**request,'event_qualifier':''})['events'])==4
    assert lookup.search(log,{**request,'object_type':'invoice'})['events']==[]
    for bad in ({'activity':'Pay','scope_object_id':'a'}, {'activity':'Pay','object_type':'order'},
                {'activity':'Pay','event_qualifier':''}):
        with pytest.raises(workspace.EventLogWorkspaceError):lookup.search(log,bad)
