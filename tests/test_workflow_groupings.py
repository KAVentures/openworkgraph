"""Correcting inferred classifications preserves evidence and immutable knowledge."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from server.workflow_groupings import WorkflowGroupings,GroupSave,GroupMove,GroupingError
from shared.execution_grouping import ExecutionGrouping
from shared.workflow_knowledge import KnowledgeStore,KnowledgeWrite

A='execution:'+'a'*16
B='execution:'+'b'*16
C='execution:'+'c'*16


@pytest.fixture
def groups(tmp_path):
    @contextmanager
    def connect():
        conn=sqlite3.connect(tmp_path/'knowledge.db',timeout=10)
        try:
            with conn:yield conn
        finally:conn.close()
    knowledge=KnowledgeStore(SimpleNamespace(connect=connect,_execute=lambda conn,sql,params=():conn.execute(sql,params)))
    checked=[]
    def verify(ids,**window):
        if not set(ids)<={A,B,C}:raise ValueError('Execution unavailable in the permitted evidence window')
        checked.append((list(ids),window))
    return WorkflowGroupings(knowledge,verify=verify),checked


def save(groups,title,ids,previous=None):
    return groups.save(GroupSave(title=title,execution_ids=ids,user_confirmed=True,
        workflow_id=previous['workflow_id'] if previous else None,expected_revision=previous['revision'] if previous else 0))


def test_split_merge_rename_and_forget_preserve_individual_histories(groups):
    store,checked=groups
    original=save(store,'Review work',[A,B])
    split=store.move(GroupMove(source_id=original['workflow_id'],source_revision=1,new_title='Exceptions',execution_ids=[B],user_confirmed=True))
    assert split['source']['execution_grouping']['execution_ids']==[A]
    assert split['target']['execution_grouping']['execution_ids']==[B]
    assert split['source']['revision']==2 and split['target']['revision']==1
    history=store.history(original['workflow_id'])['groups']
    assert history[1]['execution_grouping']['execution_ids']==[A,B]
    assert history[1]['content_sha256']==original['content_sha256']
    merged=store.move(GroupMove(source_id=split['target']['workflow_id'],source_revision=1,
        target_id=original['workflow_id'],target_revision=2,execution_ids=[B],user_confirmed=True))
    assert merged['target']['execution_grouping']['execution_ids']==[A,B]
    assert merged['source']['execution_grouping']['execution_ids']==[]
    renamed=save(store,'Corrected label',[A,B],merged['target'])
    assert renamed['revision']==4
    assert len(checked)==1  # already-reviewed references require no new source read
    assert renamed['data_layer']=='user_reviewed_knowledge'
    assert renamed['observed_evidence'] is False and renamed['execution_authorization'] is False
    assert renamed['execution_grouping']['business_case_join_verified'] is False
    with pytest.raises(GroupingError):store.forget(renamed['workflow_id'],expected_revision=3,confirmed=True)
    assert store.forget(renamed['workflow_id'],expected_revision=4,confirmed=True)['versions_deleted']==4
    assert len(store.inventory()['groups'])==1


def test_conflicts_invalid_membership_and_missing_confirmation_make_no_partial_changes(groups):
    store,checked=groups
    with pytest.raises(GroupingError):store.save(GroupSave(title='No permission',execution_ids=[A]))
    assert not checked
    with pytest.raises(ValueError):save(store,'Unavailable',['execution:'+'f'*16])
    assert not store.inventory()['groups']
    first=save(store,'First',[A,B]);other=save(store,'Other',[C])
    with pytest.raises(GroupingError):save(store,'Duplicate assignment',[A])
    with pytest.raises(ValueError):save(store,'Duplicate input',[A,A])
    for change in ({'source_revision':2},{'target_revision':2},{'execution_ids':[C]}):
        request={'source_id':first['workflow_id'],'source_revision':1,'target_id':other['workflow_id'],
            'target_revision':1,'execution_ids':[A],'user_confirmed':True}|change
        with pytest.raises(GroupingError):store.move(GroupMove(**request))
    assert store.get(first['workflow_id'])['revision']==1
    assert store.get(other['workflow_id'])['revision']==1
    with pytest.raises(ValidationError):ExecutionGrouping(execution_ids=[A],business_case_join_verified=True)


def test_second_group_write_failure_rolls_back_both_versions(groups,monkeypatch):
    store,_=groups;first=save(store,'First',[A,B]);other=save(store,'Other',[C])
    original=store.knowledge.save_in_transaction;calls=[0]
    def fail(*args,**kwargs):
        calls[0]+=1
        if calls[0]==2:raise RuntimeError('synthetic second write failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(store.knowledge,'save_in_transaction',fail)
    with pytest.raises(RuntimeError):store.move(GroupMove(source_id=first['workflow_id'],source_revision=1,
        target_id=other['workflow_id'],target_revision=1,execution_ids=[B],user_confirmed=True))
    assert store.get(first['workflow_id'])==first and store.get(other['workflow_id'])==other


def test_concurrent_splits_have_one_winner_without_duplicate_membership(groups):
    store,_=groups;first=save(store,'Original',[A,B])
    def split(item):
        try:return store.move(GroupMove(source_id=first['workflow_id'],source_revision=1,
            new_title='Split '+item,execution_ids=[item],user_confirmed=True))
        except GroupingError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(split,[A,B]))
    assert results.count('conflict')==1
    memberships=[identifier for group in store.inventory()['groups'] for identifier in group['execution_grouping']['execution_ids']]
    assert sorted(memberships)==[A,B]


def test_knowledge_compatibility_owner_isolation_and_grouping_preservation(groups):
    store,checked=groups;first=save(store,'Reviewed',[A])
    foreign=WorkflowGroupings(store.knowledge,owner='another-person',verify=store.verify)
    assert not foreign.inventory()['groups']
    with pytest.raises(LookupError):foreign.get(first['workflow_id'])
    with pytest.raises(ValueError):store.knowledge.save('local',KnowledgeWrite(workflow_id=first['workflow_id'],
        title='Generic edit',procedure='Do not lose a grouping',source_client='test',user_confirmed=True,expected_revision=1))
    with pytest.raises(ValueError):store.knowledge.save('local',KnowledgeWrite(workflow_id='group_'+'f'*32,
        title='Bypass classification checks',procedure='Not a reviewed grouping write',source_client='test',
        user_confirmed=True,execution_grouping=ExecutionGrouping(execution_ids=[A])))
    portable=KnowledgeWrite(workflow_id='normal',title='Normal',procedure='User knowledge',source_client='test',user_confirmed=True).portable()
    assert 'execution_grouping' not in portable


def test_grouping_api_checks_real_retained_executions_and_dashboard_auth(tmp_path):
    code=r'''
import json,sys
from datetime import datetime,timezone
from fastapi.testclient import TestClient
from server.secure_app import app
from server import db,local_auth
from server.procedural_memory import load_recent_evidence,derive_executions
sys.path.insert(0,'tests')
from test_procedural_memory_v062 import _run
headers={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
base='/v1/workflow-groupings'
with TestClient(app) as client:
    raw=_run('synthetic-a',0)+_run('synthetic-b',10)
    today=datetime.now(timezone.utc).date().isoformat()
    for event in raw:event['observed_at']=today+event['observed_at'][10:]
    db.insert_events(raw)
    executions=derive_executions(load_recent_evidence(limit=25000))
    ids=[row['execution_id'] for row in executions]
    assert len(ids)>=2
    with db.connect() as conn:before=[tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]
    assert client.get(base).status_code==401
    assert client.post(base,headers=headers,json={'title':'Unconfirmed','execution_ids':ids[:2]}).status_code==403
    missing=client.post(base,headers=headers,json={'title':'Unknown','execution_ids':['execution:'+'f'*16],'user_confirmed':True})
    assert missing.status_code==409,missing.text
    saved=client.post(base,headers=headers,json={'title':'Reviewed work','execution_ids':ids[:2],'user_confirmed':True})
    assert saved.status_code==200,saved.text
    assert saved.headers['cache-control']=='no-store'
    value=saved.json();identifier=value['workflow_id']
    assert value['execution_grouping']['execution_ids']==ids[:2]
    assert 'SUPERSECRET' not in saved.text and 'patient@example.com' not in saved.text
    split=client.post(base+'/move',headers=headers,json={'source_id':identifier,'source_revision':1,
        'new_title':'Selected variation','execution_ids':[ids[0]],'user_confirmed':True})
    assert split.status_code==200,split.text
    history=client.get(base+'/history',headers=headers,params={'workflow_id':identifier}).json()
    assert len(history['groups'])==2 and history['groups'][-1]['revision']==1
    assert client.post(base,headers=headers,json={'title':'Bad','execution_ids':[ids[0]],'user_confirmed':'true'}).status_code==422
    assert client.post(base,headers={**headers,'Content-Type':'application/json'},content=b'{'*131073).status_code==413
    removed=client.post(base+'/forget',headers=headers,json={'workflow_id':identifier,'expected_revision':2,'user_confirmed':True})
    assert removed.status_code==200,removed.text
    with db.connect() as conn:after=[tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]
    assert before==after
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),
        WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,
                          capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


def test_group_and_merged_membership_limits_are_enforced_before_partial_writes(groups):
    original,_=groups
    store=WorkflowGroupings(original.knowledge,verify=lambda *args,**kwargs:None)
    refs=['execution:'+format(i,'016x') for i in range(102)]
    large=save(store,'Large',refs[:25]);other=save(store,'Other',[refs[25]])
    with pytest.raises(GroupingError):store.move(GroupMove(source_id=other['workflow_id'],source_revision=1,
        target_id=large['workflow_id'],target_revision=1,execution_ids=[refs[25]],user_confirmed=True))
    assert store.get(large['workflow_id'])==large and store.get(other['workflow_id'])==other
    for index in range(98):
        save(store,'Group '+str(index),['execution:'+format(index+26,'016x')])
    assert len(store.inventory()['groups'])==100
    with pytest.raises(GroupingError):save(store,'Too many',['execution:'+format(10000,'016x')])
