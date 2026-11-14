"""Bounded quarantine work preserves exact reviewed row scope."""
from io import BytesIO
import json
import pytest
from server import event_log_quarantine as q
from server import event_log_workspace as workspace
from test_event_log_quarantine import ROWS,MAPPING


@pytest.fixture
def log_id(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    return workspace.import_log(BytesIO(ROWS.encode()),name='Synthetic quarantine',source_format='csv',mapping={**MAPPING,'keep_rejected':True})['log_id']


def test_range_preview_release_and_stale_scope(log_id):
    before=q.quarantine(log_id)
    selected=before['rows'][0]
    body={'naive_timestamp_offset':'+01:00','batch':0,'row_start':selected['row'],'row_end':selected['row']}
    preview=q.preview(log_id,body)
    assert preview['rows_considered']==1 and preview['rows_eligible']==1
    token=preview['expected_preview_sha256']
    with pytest.raises(workspace.EventLogWorkspaceError,match='preview'):
        q.release(log_id,{**body,'row_end':selected['row']+1,'expected_preview_sha256':token})
    assert q.quarantine(log_id)==before
    result=q.release(log_id,{**body,'expected_preview_sha256':token})
    assert result['decision']['rows_released']==1 and result['decision']['rows_still_kept']==3
    assert (result['decision']['row_start'],result['decision']['row_end'])==(selected['row'],selected['row'])
    assert workspace.summary(log_id)['events']==3


@pytest.mark.parametrize('extra',[{'row_start':1}, {'row_start':1,'row_end':2},
    {'batch':0,'row_start':True,'row_end':2},{'batch':0,'row_start':5,'row_end':2},
    {'batch':0,'row_start':0,'row_end':1000000000},{'batch':2147483648}])
def test_invalid_selection_fails_without_mutation(log_id,extra):
    before=q.quarantine(log_id)
    for action in (q.preview,q.release):
        with pytest.raises(workspace.EventLogWorkspaceError):action(log_id,{'naive_timestamp_offset':'+01:00',**extra})
    assert q.quarantine(log_id)==before


def test_oversized_count_refuses_before_loading_and_narrow_range_remains_usable(log_id,monkeypatch):
    conn=workspace._connect()
    try:
        value=json.dumps({'case':'c','activity':'A','time':'2024-01-01 10:00:00','resource':'','attributes':{}})
        conn.execute("INSERT INTO quarantine SELECT ?, 0, i+100000, '[]', ? FROM range(10001) t(i)",[log_id,value])
    finally:conn.close()
    before=workspace.summary(log_id)['events']
    def forbidden(*args):pytest.fail('oversized selection must not reach eligibility materialization')
    original=q._eligible
    monkeypatch.setattr(q,'_eligible',forbidden)
    for action in (q.preview,q.release):
        with pytest.raises(workspace.EventLogWorkspaceError,match='narrower row range'):
            action(log_id,{'naive_timestamp_offset':'+01:00','batch':0})
    assert workspace.summary(log_id)['events']==before
    monkeypatch.setattr(q,'_eligible',original)
    p=q.preview(log_id,{'naive_timestamp_offset':'+01:00','batch':0,'row_start':100000,'row_end':100001})
    assert p['rows_considered']==2 and p['rows_eligible']==2


def test_byte_budget_refuses_even_when_row_count_is_small(log_id,monkeypatch):
    before=q.quarantine(log_id)
    monkeypatch.setattr(q,'MAX_WORK_BYTES',1)
    with pytest.raises(workspace.EventLogWorkspaceError,match='narrower row range'):
        q.preview(log_id,{'naive_timestamp_offset':'+01:00','batch':0,'row_start':1,'row_end':100})
    assert q.quarantine(log_id)==before


def test_review_pagination_handles_batch_numbers_above_two(log_id):
    conn=workspace._connect()
    try:
        value=json.dumps({'case':'later','activity':'A','time':'2024-01-01 10:00:00','resource':'','attributes':{}})
        conn.execute("INSERT INTO quarantine VALUES (?,3,1,'[]',?)",[log_id,value])
    finally:conn.close()
    page=q.quarantine(log_id,after=2_999_999_999)
    assert len(page['rows'])==1 and page['rows'][0]['batch']==3 and page['rows'][0]['row']==1
    assert page['rows'][0]['position']==3_000_000_001


def test_http_scoped_preview_and_release_require_owner(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code=r'''
import sys
sys.path.insert(0,'tests')
from test_event_log_quarantine import ROWS
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    ai={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/import?format=csv&name=owned&case_column=case&activity_column=activity&timestamp_column=time&keep_rejected=true'
    made=client.post(url,content=ROWS.encode(),headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    base='/v1/event-logs/'+made.json()['log_id']+'/quarantine'
    body={'naive_timestamp_offset':'+01:00','batch':0,'row_start':2,'row_end':2}
    assert client.post(base+'/preview',json=body,headers=ai).status_code in (401,403)
    p=client.post(base+'/preview',json=body,headers=owner)
    assert p.status_code==200 and p.headers['cache-control']=='no-store',p.text
    assert p.json()['rows_considered']==1 and p.json()['rows_eligible']==1
    token=p.json()['expected_preview_sha256']
    stale=client.post(base+'/release',json={**body,'row_end':3,'expected_preview_sha256':token},headers=owner)
    assert stale.status_code==422,stale.text
    r=client.post(base+'/release',json={**body,'expected_preview_sha256':token},headers=owner)
    assert r.status_code==200 and r.json()['decision']['rows_released']==1,r.text
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    run=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert run.returncode==0,run.stdout+'\n'+run.stderr


def test_changed_row_content_invalidates_scoped_preview(log_id):
    selected=q.quarantine(log_id)['rows'][0]
    body={'naive_timestamp_offset':'+01:00','batch':0,'row_start':selected['row'],'row_end':selected['row']}
    token=q.preview(log_id,body)['expected_preview_sha256']
    conn=workspace._connect()
    try:
        value=conn.execute('SELECT row FROM quarantine WHERE log_id=? AND batch=0 AND row_n=?',[log_id,selected['row']]).fetchone()[0]
        changed=json.loads(value);changed['resource']='different'
        conn.execute('UPDATE quarantine SET row=? WHERE log_id=? AND batch=0 AND row_n=?',[json.dumps(changed),log_id,selected['row']])
    finally:conn.close()
    before=q.quarantine(log_id)
    with pytest.raises(workspace.EventLogWorkspaceError,match='preview'):
        q.release(log_id,{**body,'expected_preview_sha256':token})
    assert q.quarantine(log_id)==before and workspace.summary(log_id)['events']==2


def test_multi_batch_scope_over_limit_requires_narrower_selection(log_id,monkeypatch):
    conn=workspace._connect()
    try:
        conn.execute('INSERT INTO quarantine SELECT log_id,1,row_n,reasons,row FROM quarantine WHERE log_id=? AND row_n=(SELECT min(row_n) FROM quarantine WHERE log_id=?)',[log_id,log_id])
    finally:conn.close()
    before=q.quarantine(log_id)
    assert len(before['batches'])==2
    monkeypatch.setattr(q,'MAX_WORK_ROWS',2)
    with pytest.raises(workspace.EventLogWorkspaceError,match='narrower row range'):
        q.preview(log_id,{'naive_timestamp_offset':'+01:00'})
    assert q.quarantine(log_id)==before
    first=before['rows'][0]['row']
    assert q.preview(log_id,{'naive_timestamp_offset':'+01:00','batch':0,'row_start':first,'row_end':first})['rows_considered']==1
