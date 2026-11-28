"""Local saved state-check definitions are scoped and revision checked."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from server import event_log_object_state_saved as saved
from server import event_log_object_state_check as checks
from server import event_log_workspace as workspace
from test_event_log_object_state_check import fixture


@pytest.fixture(autouse=True)
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)


def load():
    return workspace.import_log(BytesIO(json.dumps(fixture()).encode()),name='one',source_format='ocel')['log_id']


def definition():
    return {'activity':'Pay','object_type':'order','attribute':'status','expected_value':'line\r\nbreak','event_qualifier':''}


def test_save_load_update_delete_and_log_scope():
    log,other=load(),load()
    first=saved.save(log,{'name':'  Review  ','definition':definition()})
    assert first['name']=='Review' and first['revision']==1
    assert first['definition']['expected_value']=='line\r\nbreak' and first['definition']['event_qualifier']==''
    assert saved.get(log,first['check_id'])==first
    assert saved.listing(log)['checks'][0]['check_id']==first['check_id']
    assert checks.analyze(log,first['definition'])['selected_events']==8
    with pytest.raises(workspace.EventLogWorkspaceError,match='already exists'):
        saved.save(log,{'name':'Review','definition':definition()})
    sibling=saved.save(other,{'name':'Review','definition':definition()})
    with pytest.raises(workspace.EventLogWorkspaceError,match='no saved'):
        saved.get(other,first['check_id'])
    changed=saved.update(log,first['check_id'],{'name':'Renamed','definition':{**definition(),'expected_value':''},'expected_revision':1})
    assert changed['revision']==2 and changed['created_at']==first['created_at'] and changed['definition']['expected_value']==''
    unrestricted=saved.save(log,{'name':'Unrestricted','definition':{k:v for k,v in definition().items() if k!='event_qualifier'}})
    assert 'event_qualifier' not in unrestricted['definition']
    with pytest.raises(workspace.EventLogWorkspaceError,match='already exists'):
        saved.update(log,unrestricted['check_id'],{'name':'Renamed','definition':definition(),'expected_revision':1})
    with pytest.raises(workspace.EventLogWorkspaceError,match='changed'):
        saved.update(log,first['check_id'],{'name':'Lost','definition':definition(),'expected_revision':1})
    with pytest.raises(workspace.EventLogWorkspaceError,match='changed'):
        saved.delete(log,first['check_id'],1)
    assert saved.delete(log,first['check_id'],2)=={'deleted':first['check_id']}
    saved.save(log,{'name':'Delete with log','definition':definition()})
    workspace.delete_log(log)
    assert saved.get(other,sibling['check_id'])==sibling
    conn=workspace._connect()
    try:assert conn.execute('SELECT count(*) FROM object_state_saved_checks WHERE log_id=?',[log]).fetchone()[0]==0
    finally:conn.close()


def test_concurrent_updates_refuse_lost_edits():
    log=load();first=saved.save(log,{'name':'Review','definition':definition()})
    def update(name):
        try:return saved.update(log,first['check_id'],{'name':name,'definition':definition(),'expected_revision':1})
        except workspace.EventLogWorkspaceError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(update,['One','Two']))
    assert sum(result is not None for result in results)==1
    assert saved.get(log,first['check_id'])['revision']==2


def test_per_log_limit_is_enforced(monkeypatch):
    log=load();monkeypatch.setattr(saved,'MAX_CHECKS',1)
    saved.save(log,{'name':'One','definition':definition()})
    with pytest.raises(workspace.EventLogWorkspaceError,match='at most 1'):
        saved.save(log,{'name':'Two','definition':definition()})
    assert len(saved.listing(log)['checks'])==1


def test_portable_template_preview_checks_destination_without_saving():
    source=load()
    packet={'format':saved.TEMPLATE_FORMAT,'name':'  Review  ','definition':{
        'activity':'Pay','object_type':'order','attribute':'status','expected_value':'approved','event_qualifier':''}}
    preview=saved.preview_template(source,packet)
    assert preview['name']=='Review' and preview['definition']['event_qualifier']==''
    assert all(preview['label_checks'].values()) and preview['log_revision']==0
    assert saved.listing(source)['checks']==[]
    destination_doc={'objectTypes':[],'eventTypes':[],
        'objects':[{'id':'o','type':'purchase','attributes':[{'name':'status','time':'1970-01-01T00:00:00Z','value':'approved'}]}],
        'events':[{'id':'e','type':'Ship','time':'2024-01-01T00:00:00Z','relationships':[{'objectId':'o','qualifier':''}]}]}
    dest=workspace.import_log(BytesIO(json.dumps(destination_doc).encode()),name='dest',source_format='ocel')['log_id']
    missing=saved.preview_template(dest,packet)
    assert not any(missing['label_checks'].values()) and saved.listing(dest)['checks']==[]
    unrestricted={**packet,'definition':{k:v for k,v in packet['definition'].items() if k!='event_qualifier'}}
    assert saved.preview_template(source,unrestricted)['label_checks']['qualified_activity_type_link_present'] is None
    exact={**packet,'definition':{**packet['definition'],'expected_value':'line\r\nbreak'}}
    assert saved.preview_template(source,exact)['definition']['expected_value']=='line\r\nbreak'
    for invalid in ({**packet,'event_id':'private'}, {**packet,'format':'wrong'},
                    {**packet,'definition':{**packet['definition'],'results':[]}}):
        with pytest.raises(workspace.EventLogWorkspaceError):saved.preview_template(source,invalid)


@pytest.mark.parametrize('payload',[
    {'name':'x','definition':{**definition(),'status':'unknown'}},
    {'name':'x','definition':{**definition(),'offset':1}},
    {'name':'x','definition':{**definition(),'extra':1}},
    {'name':'x','definition':{**definition(),'event_qualifier':None,'attribute':''}},
    {'name':'bad\nname','definition':definition()},
    {'name':'x','definition':{**definition(),'expected_value':'\ud800'}},
])
def test_invalid_definitions_are_not_saved(payload):
    log=load()
    with pytest.raises(workspace.EventLogWorkspaceError):saved.save(log,payload)
    assert saved.listing(log)['checks']==[]


def test_owner_only_http_and_strict_body(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_object_state_saved import load,definition
with TestClient(app) as c:
    h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    log=load();url='/v1/event-logs/'+log+'/object-state-check/saved'
    payload={'name':'Review','definition':definition()}
    assert c.post(url,json=payload,headers=bearer).status_code in (401,403)
    r=c.post(url,json=payload,headers=h)
    assert r.status_code==200 and r.headers['cache-control']=='no-store',r.text
    key=r.json()['check_id'];assert c.get(url+'/'+key,headers=h).json()['definition']['event_qualifier']==''
    assert c.post(url,content=b'{"name":"one","name":"two"}',headers={**h,'content-type':'application/json'}).status_code==422
    preview_url='/v1/event-logs/'+log+'/object-state-check/template/preview'
    packet={'format':'openworkgraph.object-state-check.v1',**payload}
    assert c.post(preview_url,json=packet,headers=bearer).status_code in (401,403)
    preview=c.post(preview_url,json=packet,headers=h)
    assert preview.status_code==200 and preview.headers['cache-control']=='no-store',preview.text
    assert c.post(preview_url,content=b'{"format":"one","format":"two"}',headers={**h,'content-type':'application/json'}).status_code==422
    assert c.post(preview_url,content=b' '+b'x'*65536,headers={**h,'content-type':'application/json'}).status_code==413
    assert c.put(url+'/'+key,json={**payload,'expected_revision':1},headers=h).json()['revision']==2
    assert c.delete(url+'/'+key,params={'expected_revision':1},headers=h).status_code==422
    assert c.delete(url+'/'+key,params={'expected_revision':2},headers=h).status_code==200
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth')}
    result=subprocess.run([sys.executable,'-c',"import sys;sys.path.insert(0,'tests');"+code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
