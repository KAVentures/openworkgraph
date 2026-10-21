"""Shared process isolation, exact revision review, role revocation and atomic updates."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from uuid import uuid4
from urllib.parse import urlsplit,parse_qsl,urlencode,urlunsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gateway.db import GatewayDB
from gateway.hardening import HardeningSettings
from gateway.human_access import HumanAccessSettings, HumanPrincipal, _normalize_group_map
from gateway.human_enterprise_app import create_human_enterprise_app
from gateway.process_collaboration import (
    OrganizationProcessStore, SharedProcessWrite, ProcessAccessWrite, ProcessMember,
    ProcessReviewWrite, ProcessArchiveWrite, ProcessDeleteWrite, ProcessCollaborationError, namespace,
)
from gateway.settings import GatewaySettings
from shared.process_model import ProcessModel
from shared.workflow_knowledge import KnowledgeWrite

SCOPES=frozenset({'org:process:read','org:process:write','org:process:review'})


def person(actor='alice',org='acme',scopes=SCOPES):
    return HumanPrincipal(subject='subject-'+actor,organization_id=org,actor_id=actor,
                          groups=frozenset(),scopes=scopes,issuer='https://idp.test')


def model():
    return ProcessModel(goal='Resolve a selected case',baseline_source='selected_csv_variant',
        baseline_path=['Open','Close'],activities=[{'id':'open','label':'Open'},{'id':'close','label':'Close'}],
        transitions=[{'source':'open','target':'close'}],entry_activity_id='open',terminal_activity_ids=['close'])


def write(title='Case process',expected=0,**kwargs):
    return SharedProcessWrite(title=title,process_model=model(),expected_revision=expected,
                              user_confirmed=True,organization_sharing_confirmed=True,**kwargs)


@pytest.fixture(params=['sqlite'] + (['postgres'] if os.getenv('OWG_PROCESS_TEST_DATABASE_URL') else []))
def store(tmp_path,request):
    schema=None
    if request.param=='postgres':
        import psycopg
        from psycopg import sql
        target=os.environ['OWG_PROCESS_TEST_DATABASE_URL']
        parts=urlsplit(target)
        if parts.hostname not in {'127.0.0.1','localhost','postgres'}:
            pytest.fail('Use only a disposable local PostgreSQL database for collaboration tests')
        schema='owg_process_test_'+uuid4().hex
        with psycopg.connect(target) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        query=dict(parse_qsl(parts.query));query['options']='-csearch_path='+schema
        url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))
    else:
        url=f'sqlite:///{tmp_path / "gateway.db"}'
    try:
        db=GatewayDB(url);db.init()
        result=OrganizationProcessStore(db);result.initialize();yield result
    finally:
        if schema:
            with psycopg.connect(target) as conn:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def grant(store,wid,*,members=None,actor=None,expected=1,new_owner=None):
    return store.set_access(actor or person(),wid,ProcessAccessWrite(
        expected_access_revision=expected,members=members or [],new_owner_actor_id=new_owner,user_confirmed=True))


def review(record,decision='reviewed_design',expected=0,**changes):
    return ProcessReviewWrite(revision=record['revision'],content_sha256=record['content_sha256'],
        decision=decision,expected_review_number=expected,user_confirmed=True,**changes)


def test_default_scopes_do_not_grant_process_access(store):
    anonymous_role=person(scopes=frozenset({'self:evidence:read','org:evidence:read','self:knowledge:write'}))
    with pytest.raises(ProcessCollaborationError) as exc:
        store.inventory(anonymous_role)
    assert exc.value.status==403
    with pytest.raises(ProcessCollaborationError):
        store.save(anonymous_role,write())
    mapping=_normalize_group_map('{"reviewers":["org:process:read","org:process:review"]}')
    assert mapping['reviewers']==frozenset({'org:process:read','org:process:review'})


def test_explicit_sharing_tenant_isolation_and_personal_knowledge_separation(store):
    p=person()
    store.knowledge.save(json.dumps(['acme','alice'],separators=(',',':')),KnowledgeWrite(
        workflow_id='private',title='Private procedure',procedure='PRIVATE ONLY',source_client='test',user_confirmed=True))
    assert store.inventory(p)['processes']==[]
    with pytest.raises(ProcessCollaborationError):
        store.save(p,write().model_copy(update={'organization_sharing_confirmed':False}))
    shared=store.save(p,write());wid=shared['process']['workflow_id']
    assert shared['owner_actor_id']=='alice' and shared['author_actor_id']=='alice'
    assert shared['review']['status']=='needs_review'
    assert shared['process']['evidence_refs']==[]
    assert not shared['raw_evidence_rows_included']
    assert store.knowledge.list(namespace('acme'),wid)['workflows'][0]==shared['process']
    assert store.inventory(person('bob'))['processes'][0]['workflow_id']==wid
    assert 'PRIVATE ONLY' not in json.dumps(store.inventory(p))
    with pytest.raises(ProcessCollaborationError) as exc:
        store.detail(person(org='other'),wid)
    assert exc.value.status==404
    assert store.inventory(person(org='other'))['processes']==[]
    assert store.knowledge.list(json.dumps(['acme','bob'],separators=(',',':')))['workflows']==[]
    with pytest.raises(ValidationError):
        SharedProcessWrite(**{**write().model_dump(),'evidence_refs':['execution:private']})


def test_editor_assignment_does_not_grant_scope_and_revocation_takes_effect(store):
    wid=store.save(person(),write())['process']['workflow_id']
    bob=person('bob')
    with pytest.raises(ProcessCollaborationError):
        store.save(bob,write(expected=1),wid)
    grant(store,wid,members=[ProcessMember(actor_id='bob',role='editor')])
    restricted=person('bob',scopes=frozenset({'org:process:read'}))
    with pytest.raises(ProcessCollaborationError):
        store.save(restricted,write(expected=1),wid)
    changed=store.save(bob,write('Edited by Bob',1),wid)
    assert changed['author_actor_id']=='bob' and changed['process']['revision']==2
    grant(store,wid,expected=2)
    with pytest.raises(ProcessCollaborationError):
        store.save(bob,write(expected=2),wid)
    with pytest.raises(ProcessCollaborationError):
        grant(store,wid,expected=2)


def test_review_pins_current_revision_requires_independent_actor_and_retains_history(store):
    first=store.save(person(),write());wid=first['process']['workflow_id']
    with pytest.raises(ProcessCollaborationError):
        store.review(person(),wid,review(first['process']))
    bob=person('bob')
    with pytest.raises(ProcessCollaborationError):
        store.review(bob,wid,review(first['process']))
    grant(store,wid,members=[ProcessMember(actor_id='bob',role='reviewer')])
    wrong=review(first['process']).model_copy(update={'content_sha256':'0'*64})
    with pytest.raises(ProcessCollaborationError):
        store.review(bob,wid,wrong)
    approved=store.review(bob,wid,review(first['process'],comment='Design reviewed; live tools still require authorization'))
    assert approved['review']['status']=='reviewed_design'
    assert approved['review']['execution_authorization'] is False
    assert approved['your_review_number']==1
    with pytest.raises(ProcessCollaborationError):
        store.review(bob,wid,review(first['process']))
    changes=store.review(bob,wid,review(first['process'],'changes_requested',1,comment='Clarify exception'))
    assert changes['review']['status']=='changes_requested'
    latest=store.save(person(),write('Revised design',1),wid)
    assert latest['review']['status']=='needs_review'
    assert latest['process']['revision']==2
    with pytest.raises(ProcessCollaborationError):
        store.review(bob,wid,review(first['process'],expected=2))
    old=store.detail(bob,wid,revision=1)
    assert old['viewing_historical_revision']
    assert not old['permissions']['review'] and not old['permissions']['edit']
    assert old['review']['status']=='changes_requested'
    with store.db.connect() as conn:
        rows=store.db._execute(conn,'SELECT COUNT(*) FROM gateway_process_reviews').fetchone()[0]
    assert rows==2


def test_reviewer_revocation_and_owner_transfer_need_current_permissions(store):
    first=store.save(person(),write());wid=first['process']['workflow_id']
    grant(store,wid,members=[ProcessMember(actor_id='bob',role='reviewer')])
    grant(store,wid,expected=2)
    with pytest.raises(ProcessCollaborationError):
        store.review(person('bob'),wid,review(first['process']))
    with pytest.raises(ProcessCollaborationError):
        grant(store,wid,expected=3,new_owner='bob')
    admin=person('admin',scopes=SCOPES|{'org:process:admin'})
    changed=grant(store,wid,actor=admin,expected=3,new_owner='bob')
    assert changed['owner_actor_id']=='bob'
    with pytest.raises(ProcessCollaborationError):
        store.save(person(),write(expected=1),wid)
    assert store.save(person('bob'),write(expected=1),wid)['process']['revision']==2


def test_archive_and_deletion_are_confirmed_version_checked_and_leave_private_knowledge(store):
    private_owner=json.dumps(['acme','alice'],separators=(',',':'))
    store.knowledge.save(private_owner,KnowledgeWrite(workflow_id='private',title='Private procedure',
        procedure='Keep private',source_client='test',user_confirmed=True))
    first=store.save(person(),write());wid=first['process']['workflow_id']
    archived=store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=1,archived=True,user_confirmed=True))
    assert archived['archived'] and not store.inventory(person())['processes']
    assert store.inventory(person(),include_archived=True)['processes'][0]['archived']
    with pytest.raises(ProcessCollaborationError):
        store.save(person(),write(expected=1),wid)
    with pytest.raises(ProcessCollaborationError):
        store.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=1,user_confirmed=True))
    deleted=store.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    assert deleted['versions_deleted']==1 and not deleted['personal_knowledge_deleted']
    assert store.knowledge.list(private_owner)['workflows'][0]['procedure']=='Keep private'
    with pytest.raises(ProcessCollaborationError):
        store.detail(person(),wid)
    with store.db.connect() as conn:
        for table in ('gateway_shared_processes','gateway_process_authors','gateway_process_members','gateway_process_reviews'):
            assert store.db._execute(conn,f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0


def test_concurrent_saves_preserve_one_winner_and_audit_is_minimized(store):
    wid=store.save(person(),write())['process']['workflow_id']
    def save(index):
        try:return store.save(person(),write('Draft '+str(index),1),wid)['process']['revision']
        except ValueError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(save,[1,2]))
    assert sorted(results,key=str)==[2,'conflict']
    rows=store.db.audit_rows('acme')
    assert sum(row['action']=='process.revision.saved' for row in rows)==1
    assert all('process_model' not in row['details'] and 'comment' not in row['details'] for row in rows)


def test_failed_audit_rolls_back_graph_metadata_and_author(store,monkeypatch):
    def fail(*args,**kwargs):raise RuntimeError('synthetic audit storage failure')
    monkeypatch.setattr(store,'_audit',fail)
    with pytest.raises(RuntimeError):store.save(person(),write())
    assert store.knowledge.list(namespace('acme'))['workflows']==[]
    with store.db.connect() as conn:
        assert store.db._execute(conn,'SELECT COUNT(*) FROM gateway_shared_processes').fetchone()[0]==0
        assert store.db._execute(conn,'SELECT COUNT(*) FROM gateway_process_authors').fetchone()[0]==0


def test_concurrent_review_decisions_have_one_winner_and_failed_audit_keeps_prior_decision(store,monkeypatch):
    first=store.save(person(),write());wid=first['process']['workflow_id']
    grant(store,wid,members=[ProcessMember(actor_id='bob',role='reviewer')])
    def submit(decision):
        try:
            store.review(person('bob'),wid,review(first['process'],decision))
            return 'saved'
        except ProcessCollaborationError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit,['reviewed_design','changes_requested']))==['conflict','saved']
    prior=store.detail(person('bob'),wid)
    assert prior['your_review_number']==1
    def fail(*args,**kwargs):raise RuntimeError('synthetic audit storage failure')
    with monkeypatch.context() as patch:
        patch.setattr(store,'_audit',fail)
        with pytest.raises(RuntimeError):
            store.review(person('bob'),wid,review(first['process'],'changes_requested',1))
    assert store.detail(person('bob'),wid)['review']==prior['review']


class Verifier:
    def verify(self,token):
        if token not in {'alice','bob','reader','foreign','no-process'}:raise ValueError('invalid')
        return {'sub':token,'actor':token,'org':'elsewhere' if token=='foreign' else 'acme',
                'groups':['readers'] if token=='reader' else ['writers'] if token in {'alice','bob','foreign'} else []}


def app(tmp_path):
    settings=GatewaySettings(database_url=f'sqlite:///{tmp_path / "api.db"}',admin_token='admin',enrollment_token='enroll')
    human=HumanAccessSettings(issuer='https://idp.test',audience='owg',jwks_url='https://idp.test/jwks',
        organization_claim='org',actor_claim='actor',group_scope_map={'writers':SCOPES,'readers':frozenset({'org:process:read'})})
    return create_human_enterprise_app(settings=settings,hardening=HardeningSettings(),human_access=human,verifier=Verifier())


def test_real_authenticated_api_and_nonce_portal(tmp_path):
    with TestClient(app(tmp_path)) as client:
        portal=client.get('/processes')
        assert portal.status_code==200
        assert "script-src 'nonce-" in portal.headers['content-security-policy']
        assert "style-src 'nonce-" in portal.headers['content-security-policy']
        assert "'unsafe-inline'" not in portal.headers['content-security-policy']
        assert '__NONCE__' not in portal.text
        assert 'localStorage' not in portal.text and 'sessionStorage' not in portal.text
        headers=lambda who:{'Authorization':'Bearer '+who}
        assert client.get('/v1/human/processes').status_code==401
        assert client.get('/v1/human/processes',headers=headers('admin')).status_code==401
        assert client.get('/v1/human/processes',headers=headers('no-process')).status_code==403
        assert client.post('/v1/human/processes',headers=headers('reader'),json=write().model_dump()).status_code==403
        saved=client.post('/v1/human/processes',headers=headers('alice'),json=write().model_dump())
        assert saved.status_code==200,saved.text
        assert saved.headers['cache-control']=='no-store'
        first=saved.json();wid=first['process']['workflow_id'];url='/v1/human/processes/'+wid
        assert client.get(url,headers=headers('foreign')).status_code==404
        assert client.post(url,headers=headers('bob'),json=write(expected=1).model_dump()).status_code==403
        access=ProcessAccessWrite(expected_access_revision=1,members=[ProcessMember(actor_id='bob',role='reviewer')],user_confirmed=True)
        assert client.post(url+'/access',headers=headers('alice'),json=access.model_dump()).status_code==200
        decision=client.post(url+'/reviews',headers=headers('bob'),json=review(first['process']).model_dump())
        assert decision.status_code==200,decision.text
        assert decision.json()['review']['status']=='reviewed_design'
        for fmt in ['json','pdd','bpmn']:
            exported=client.get(url+'/export',headers=headers('reader'),params={'revision':1,'format':fmt})
            assert exported.status_code==200,exported.text
            assert exported.headers['cache-control']=='no-store'
        history=client.get(url+'/history',headers=headers('reader')).json()
        assert history['workflows'][0]['revision']==1
        for body in [b'{"user_confirmed":true,"user_confirmed":false}',b'{"n":NaN}',b'['*2000+b']'*2000]:
            assert client.post('/v1/human/processes',headers={**headers('alice'),'Content-Type':'application/json'},content=body).status_code==422
        assert client.post('/v1/human/processes',headers={**headers('alice'),'Content-Type':'application/json'},content=b'x'*262145).status_code==413
