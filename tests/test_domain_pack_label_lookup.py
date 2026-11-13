"""Selected-log literal label lookup: bounded pages and owner-only access."""
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest
from server import event_log_domain_packs as packs
from server import event_log_workspace as workspace


@pytest.fixture
def logs(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    def add(lines):
        data='case,activity,time\n'+''.join(f'c{i},{label},2026-01-01T00:00:00Z\n' for i,label in enumerate(lines))
        return workspace.import_log(BytesIO(data.encode()),name='synthetic',source_format='csv',mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})['log_id']
    return add([f'Prefix{i:03}' for i in range(205)]+['literal%_value']),add(['Prefix-private'])


def test_pages_are_bounded_exact_and_selected_log_only(logs):
    first,other=logs
    p1=packs.find_mapping_labels(first,'activities','Prefix')
    p2=packs.find_mapping_labels(first,'activities','Prefix',p1['next_after'])
    p3=packs.find_mapping_labels(first,'activities','Prefix',p2['next_after'])
    labels=p1['labels']+p2['labels']+p3['labels']
    assert [len(p1['labels']),len(p2['labels']),len(p3['labels'])]==[100,100,5]
    assert labels==[f'Prefix{i:03}' for i in range(205)] and p3['next_after'] is None
    assert packs.find_mapping_labels(other,'activities','Prefix')['labels']==['Prefix-private']
    assert packs.find_mapping_labels(first,'activities','prefix')['labels']==[]
    assert packs.find_mapping_labels(first,'activities','%_')['labels']==['literal%_value']
    assert packs.find_mapping_labels(first,'object_types','case')['labels']==['case']


def test_invalid_search_and_unknown_log_are_refused(logs):
    log,_=logs
    for kind,search,after in [('bad','x',''),('activities','',''),('activities','x'*201,''),('activities','x','y'*201)]:
        with pytest.raises(workspace.EventLogWorkspaceError):packs.find_mapping_labels(log,kind,search,after)
    with pytest.raises(workspace.EventLogWorkspaceError):packs.find_mapping_labels('unknown','activities','Prefix')


def test_http_owner_only_and_no_store(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    ai={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    csv=b'case,activity,time\nc1,Reviewed,2026-01-01T00:00:00Z\n'
    r=client.post('/v1/event-logs/import?format=csv&name=owned&case_column=case&activity_column=activity&timestamp_column=time',content=csv,headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert r.status_code==200,r.text
    url='/v1/event-logs/'+r.json()['log_id']+'/domain-pack-labels'
    body={'kind':'activities','search':'Review'}
    found=client.post(url,json=body,headers=owner)
    assert found.status_code==200 and found.json()['labels']==['Reviewed'] and found.headers['cache-control']=='no-store',found.text
    assert client.post(url,json=body,headers=ai).status_code in (401,403)
    assert client.post(url,json=body).status_code in (401,403)
    assert client.post(url,json={**body,'extra':True},headers=owner).status_code==422
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    run=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
