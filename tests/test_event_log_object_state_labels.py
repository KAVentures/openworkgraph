"""Owner-only literal lookup for recorded event-time state checks."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from server import event_log_activity_mappings as mappings
from server import event_log_object_state_labels as labels
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def fixture():
    values=[{'name':'status','time':'1970-01-01T00:00:00Z','value':''}]
    values.extend({'name':'status','time':(datetime(2024,1,1,tzinfo=timezone.utc)+timedelta(minutes=n)).isoformat(),
                   'value':f'v{n:03}'} for n in range(110))
    values.extend([{'name':'status','time':'2024-02-01T00:00:00Z','value':'line\r\nbreak'},
                   {'name':'odd\nname','time':'1970-01-01T00:00:00Z','value':'x'}])
    objects=[{'id':'order1','type':'order','attributes':values},
             {'id':'item1','type':'item','attributes':[{'name':'status','time':'1970-01-01T00:00:00Z','value':'other type'}]}]
    events=[{'id':f'e{n:03}','type':f'rare%_{n:03}','time':'2024-01-01T00:00:00Z',
             'relationships':[{'objectId':'order1','qualifier':''},{'objectId':'order1','qualifier':'payer'}]} for n in range(110)]
    events.append({'id':'other','type':'other','time':'2024-01-01T00:00:00Z','relationships':[{'objectId':'item1','qualifier':'item'}]})
    return {'objectTypes':[],'eventTypes':[],'objects':objects,'events':events}


def load():
    return workspace.import_log(BytesIO(json.dumps(fixture()).encode()),name='one',source_format='ocel')['log_id']


def test_raw_activity_lookup_pages_and_ignores_mappings():
    log=load();other=load()
    conn=workspace._connect()
    try:
        conn.execute("INSERT INTO events VALUES (?, 'unlinked', 'only-one', TIMESTAMP '2024-01-01', '', '{}')",[log])
    finally:
        conn.close()
    made=mappings.create(log,{'name':'map','groups':[{'label':'Mapped','activities':['rare%_000']}],'collapse_repeats':False})
    mappings.apply_mapping(log,made['mapping_id'])
    first=labels.search(log,{'kind':'activity','search':'rare%_','limit':50})
    second=labels.search(log,{'kind':'activity','search':'rare%_','limit':50,'after':first['next_after'],'expected_revision':first['revision']})
    third=labels.search(log,{'kind':'activity','search':'rare%_','limit':50,'after':second['next_after'],'expected_revision':first['revision']})
    assert first['labels']+second['labels']+third['labels']==[f'rare%_{n:03}' for n in range(110)]
    assert third['next_after'] is None and all(first['checkable'])
    assert labels.search(log,{'kind':'activity','search':'Mapped'})['labels']==[]
    assert labels.search(log,{'kind':'activity','search':'only-one'})['labels']==['only-one']
    assert labels.search(other,{'kind':'activity','search':'only-one'})['labels']==[]
    assert labels.search(other,{'kind':'activity','search':"%' OR 1=1 --"})['labels']==[]
    assert 'order1' not in json.dumps(first)


def test_attribute_value_qualifier_lookup_preserves_empty_cursor_and_scope():
    log=load()
    names=labels.search(log,{'kind':'attribute','object_type':'order'})
    assert names['labels']==['odd\nname','status'] and names['checkable']==[False,True]
    assert labels.search(log,{'kind':'attribute','object_type':'item'})['labels']==['status']
    first=labels.search(log,{'kind':'value','object_type':'order','attribute':'status','limit':1})
    assert first['labels']==[''] and first['next_after']==''
    second=labels.search(log,{'kind':'value','object_type':'order','attribute':'status','limit':1,'after':'','expected_revision':first['revision']})
    assert second['labels']==['line\r\nbreak']
    assert labels.search(log,{'kind':'value','object_type':'item','attribute':'status'})['labels']==['other type']
    assert labels.search(log,{'kind':'qualifier','object_type':'order'})['labels']==['','payer']
    assert labels.search(log,{'kind':'qualifier','object_type':'item'})['labels']==['item']
    for invalid in ({'kind':'value','object_type':'order'}, {'kind':'activity','object_type':'order'},
                    {'kind':'activity','after':''}, {'kind':'value','object_type':'order','attribute':'bad\nname'},
                    {'kind':'qualifier','object_type':'order','limit':True}, {'kind':'activity','extra':1}):
        with pytest.raises(workspace.EventLogWorkspaceError):labels.search(log,invalid)
    newer={'objectTypes':[],'eventTypes':[],'objects':[{'id':'new','type':'order'}],
           'events':[{'id':'new-event','type':'Pay','time':'2024-02-01T00:00:00Z','relationships':[{'objectId':'new','qualifier':''}]}]}
    workspace.append_log(log,BytesIO(json.dumps(newer).encode()),source_format='ocel')
    with pytest.raises(workspace.EventLogWorkspaceError,match='changed'):
        labels.search(log,{'kind':'value','object_type':'order','attribute':'status','after':'','expected_revision':first['revision']})


def test_lookup_uses_one_read_snapshot_during_append(monkeypatch):
    log=load();original=labels._revision;appended=[]
    def interleave(conn,log_id):
        revision=original(conn,log_id)
        if not appended:
            appended.append(True)
            newer={'objectTypes':[],'eventTypes':[],'objects':[{'id':'new','type':'order'}],
                   'events':[{'id':'new-event','type':'newly-recorded','time':'2024-02-01T00:00:00Z',
                              'relationships':[{'objectId':'new','qualifier':''}]}]}
            workspace.append_log(log,BytesIO(json.dumps(newer).encode()),source_format='ocel')
        return revision
    monkeypatch.setattr(labels,'_revision',interleave)
    before=labels.search(log,{'kind':'activity','search':'newly-recorded'})
    assert before['revision']==0 and before['labels']==[]
    after=labels.search(log,{'kind':'activity','search':'newly-recorded'})
    assert after['revision']==1 and after['labels']==['newly-recorded']


def test_owner_only_http_and_strict_body(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_event_log_object_state_labels import load
with TestClient(app) as c:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    log=load();url='/v1/event-logs/'+log+'/object-state-check/labels'
    assert c.post(url,json={'kind':'activity'},headers=bearer).status_code in (401,403)
    result=c.post(url,json={'kind':'activity','search':'rare%_'},headers=owner)
    assert result.status_code==200 and result.headers['cache-control']=='no-store',result.text
    assert len(result.json()['labels'])==50
    assert c.post(url,content=b'{"kind":"activity","kind":"value"}',headers={**owner,'content-type':'application/json'}).status_code==422
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth')}
    result=subprocess.run([sys.executable,'-c',"import sys;sys.path.insert(0,'tests');"+code],env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
