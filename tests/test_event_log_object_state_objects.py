"""Owner-only lookup of objects linked to an event-time check scope."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from server import event_log_workspace as workspace
from server import event_log_object_state_objects as lookup


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def doc():
    objects=[{'id':f'o%_{n:03}','type':'order'} for n in range(110)]
    objects += [{'id':'unused','type':'order'},{'id':'late','type':'order'},{'id':'invoice','type':'invoice'}]
    links=[{'objectId':o['id'],'qualifier':''} for o in objects[:110]]
    links += [{'objectId':'o%_000','qualifier':'payer'},{'objectId':'invoice','qualifier':''}]
    return {'objectTypes':[],'eventTypes':[],'objects':objects,'events':[
        {'id':'e1','type':'Pay','time':'2024-01-01T12:00:00Z','relationships':links},
        {'id':'e2','type':'Pay','time':'2024-02-01T00:00:00Z','relationships':[{'objectId':'late','qualifier':''}]},
        {'id':'e3','type':'Other','time':'2024-01-01T00:00:00Z','relationships':[{'objectId':'unused','qualifier':''}]}]}


def load():
    return workspace.import_log(BytesIO(json.dumps(doc()).encode()),name='object lookup',source_format='ocel')['log_id']


def test_lookup_scope_paging_literals_and_log_isolation():
    log=load();other=load();req={'activity':'Pay','object_type':'order','search':'o%_','limit':50}
    a=lookup.search(log,req);b=lookup.search(log,{**req,'after':a['next_after'],'expected_revision':a['revision']})
    c=lookup.search(log,{**req,'after':b['next_after'],'expected_revision':a['revision']})
    assert a['objects']+b['objects']+c['objects']==[f'o%_{n:03}' for n in range(110)] and c['next_after'] is None
    assert lookup.search(log,{'activity':'Pay','object_type':'order','search':"%' OR 1=1 --"})['objects']==[]
    assert lookup.search(log,{'activity':'Pay','object_type':'order','event_qualifier':'payer'})['objects']==['o%_000']
    assert len(lookup.search(log,{'activity':'Pay','object_type':'order','event_qualifier':''})['objects'])==50
    assert lookup.search(log,{'activity':'Pay','object_type':'order','scope_event_id':'e2'})['objects']==['late']
    assert lookup.search(log,{'activity':'Pay','object_type':'order','from_date':'2024-02-01'})['objects']==['late']
    assert lookup.search(log,{'activity':'Pay','object_type':'order','search':'late','until_date':'2024-02-01'})['objects']==[]
    assert lookup.search(log,{'activity':'Pay','object_type':'invoice'})['objects']==['invoice']
    assert lookup.search(log,{'activity':'Pay','object_type':'order','search':'unused'})['objects']==[]
    conn=workspace._connect()
    try:conn.execute("INSERT INTO objects VALUES (?, 'unique', 'order', '{}')",[log])
    finally:conn.close()
    assert lookup.search(other,{'activity':'Pay','object_type':'order','search':'unique'})['objects']==[]
    for bad in ({'activity':'Pay'},{'activity':'Pay','object_type':'order','after':'x'},
                {'activity':'Pay','object_type':'order','limit':True},
                {'activity':'Pay','object_type':'order','from_date':'2024-02-30'},
                {'activity':'Pay','object_type':'order','search':'\x00'},
                {'activity':'Pay','object_type':'order','extra':True}):
        with pytest.raises(workspace.EventLogWorkspaceError):lookup.search(log,bad)
    newer={'objectTypes':[],'eventTypes':[],'objects':[{'id':'new','type':'order'}],
           'events':[{'id':'new-event','type':'Pay','time':'2024-03-01T00:00:00Z','relationships':[{'objectId':'new','qualifier':''}]}]}
    workspace.append_log(log,BytesIO(json.dumps(newer).encode()),source_format='ocel')
    with pytest.raises(workspace.EventLogWorkspaceError,match='changed'):
        lookup.search(log,{**req,'after':a['next_after'],'expected_revision':a['revision']})


def test_one_snapshot_across_append(monkeypatch):
    log=load();original=lookup._revision;seen=[]
    def interleave(conn,log_id):
        revision=original(conn,log_id)
        if not seen:
            seen.append(True)
            newer={'objectTypes':[],'eventTypes':[],'objects':[{'id':'new','type':'order'}],
                   'events':[{'id':'new-event','type':'Pay','time':'2024-03-01T00:00:00Z','relationships':[{'objectId':'new','qualifier':''}]}]}
            workspace.append_log(log,BytesIO(json.dumps(newer).encode()),source_format='ocel')
        return revision
    monkeypatch.setattr(lookup,'_revision',interleave)
    before=lookup.search(log,{'activity':'Pay','object_type':'order','search':'new'})
    assert before['revision']==0 and before['objects']==[]
    after=lookup.search(log,{'activity':'Pay','object_type':'order','search':'new'})
    assert after['revision']==1 and after['objects']==['new']


def test_owner_only_http_and_strict_body(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_object_state_objects import load
with TestClient(app) as c:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    log=load();url='/v1/event-logs/'+log+'/object-state-check/objects/search'
    payload={'activity':'Pay','object_type':'order'}
    assert c.post(url,json=payload,headers=bearer).status_code in (401,403)
    r=c.post(url,json=payload,headers=owner)
    assert r.status_code==200 and r.headers['cache-control']=='no-store',r.text
    assert len(r.json()['objects'])==50
    assert c.post(url,content=b'{"activity":"Pay","activity":"Other"}',headers={**owner,'content-type':'application/json'}).status_code==422
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth')}
    result=subprocess.run([sys.executable,'-c',"import sys;sys.path.insert(0,'tests');"+code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
