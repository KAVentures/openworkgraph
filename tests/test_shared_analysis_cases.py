"""Separate case disclosure, exact parent binding and lifecycle on both supported databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gateway.analysis_case_data import CaseData, Attach, CaseAccess, CaseDelete, READ, WRITE
from gateway.analysis_publications import Access, Delete, PublicationError
from shared.analysis_case_dataset import CaseDataset
from test_shared_analysis_publications import store as parent_store, process_store, person, publication, app as app_factory, snapshot


def p(actor='alice',org='acme',extra=()):
    from dataclasses import replace
    return replace(person(actor,org),scopes=person().scopes|{READ,WRITE}|set(extra))


def dataset():
    a=snapshot().model_dump(mode='json')
    return CaseDataset(generated_at=a['generated_at'],source_content_sha256=a['source_content_sha256'],analysis_spec_sha256=a['analysis_spec_sha256'],
        cases=[{'code':'C1','events':[{'activity':'A1','elapsed_microseconds':0},{'activity':'A2','elapsed_microseconds':3000000},{'activity':'A1','elapsed_microseconds':5000000}]},
               *[{'code':'C'+str(i),'events':[{'activity':'A1','elapsed_microseconds':0},{'activity':'A2','elapsed_microseconds':2000000}]} for i in range(2,5)]])


@pytest.fixture
def cases(parent_store):return CaseData(parent_store)


def attach(cases):
    s=cases.parent;parent=s.publish(p(),publication());wid=parent['view_id']
    body=Attach(revision=1,parent_content_sha256=parent['content_sha256'],expected_parent_access_revision=1,dataset=dataset(),user_confirmed=True,case_sharing_confirmed=True)
    cases.attach(p(),wid,body)
    return wid,body


def grant(cases,wid,actors=('bob',),expected=1,parent_rev=2):
    info=cases.access(p(),wid,1)
    return cases.access(p(),wid,1,CaseAccess(revision=1,content_sha256=info['content_sha256'],expected_access_revision=expected,
        expected_parent_access_revision=parent_rev,members=[{'actor_id':a,'role':'viewer'} for a in actors],user_confirmed=True))


def member(cases,wid,actors=('bob',),expected=1):
    return cases.parent.access(p(),wid,Access(expected_access_revision=expected,members=[{'actor_id':a,'role':'viewer'} for a in actors],user_confirmed=True))


def test_aggregate_membership_and_admin_are_not_case_grants(cases):
    wid,_=attach(cases);member(cases,wid)
    assert cases.parent.detail(person('bob'),wid)['snapshot']
    for who in [person(),person('bob'),p('bob'),p('root',extra={'org:analysis:admin'}),p(org='foreign',extra={'org:analysis:admin'})]:
        with pytest.raises(PublicationError):cases.read(who,wid,1)
    grant(cases,wid)
    assert cases.read(p('bob'),wid,1)['matching_cases']==4
    with pytest.raises(PublicationError):cases.read(person('bob'),wid,1)
    # An aggregate admin with all case scopes still needs an explicit case grant.
    with pytest.raises(PublicationError):cases.access(p('root',extra={'org:analysis:admin'}),wid,1)


def test_exact_queries_variants_pagination_and_timeline(cases):
    wid,_=attach(cases)
    r=cases.read(p(),wid,1,limit=2)
    assert r['matching_cases']==4 and r['matching_events']==9 and r['rework_cases']==1 and r['median_duration_seconds']==2
    assert [c['code'] for c in r['cases']]==['C1','C2'] and r['next_after']==2
    assert [c['code'] for c in cases.read(p(),wid,1,after=2)['cases']]==['C3','C4']
    assert r['transitions']==[{'from':'A1','to':'A2','count':4},{'from':'A2','to':'A1','count':1}]
    v=r['variants'][1]['variant']
    assert cases.read(p(),wid,1,variant=v)['matching_cases']==1
    assert cases.read(p(),wid,1,activity='A99')['matching_cases']==0
    assert cases.read(p(),wid,1,case_code='C1')['case']['events'][-1]['elapsed_microseconds']==5000000
    with pytest.raises(PublicationError):cases.read(p(),wid,1,case_code='C99')
    assert cases.read(p(),wid,1,exporting=True)['dataset']==dataset().model_dump(mode='json')
    audit=json.dumps(cases.parent.db.audit_rows('acme',100))
    assert 'analysis.cases.exported' in audit and 'elapsed_microseconds' not in audit and 'C1' not in audit


def test_parent_acl_changes_invalidate_every_case_grant_even_if_membership_remains(cases):
    wid,_=attach(cases);member(cases,wid);grant(cases,wid)
    member(cases,wid,expected=2)
    assert not cases.access(p(),wid,1)['members'][0]['active']
    with pytest.raises(PublicationError):cases.read(p('bob'),wid,1)
    grant(cases,wid,expected=2,parent_rev=3)
    assert cases.read(p('bob'),wid,1)['matching_cases']==4
    grant(cases,wid,actors=(),expected=3,parent_rev=3)
    with pytest.raises(PublicationError):cases.read(p('bob'),wid,1,exporting=True)


def test_owner_only_attach_and_exact_population_source_spec_revision_and_confirmation(cases):
    s=cases.parent;parent=s.publish(p(),publication());wid=parent['view_id']
    body=Attach(revision=1,parent_content_sha256=parent['content_sha256'],expected_parent_access_revision=1,dataset=dataset(),user_confirmed=True,case_sharing_confirmed=True)
    for update in [{'revision':2},{'expected_parent_access_revision':2},{'parent_content_sha256':'e'*64},{'user_confirmed':False},{'case_sharing_confirmed':False}]:
        with pytest.raises(PublicationError):cases.attach(p(),wid,body.model_copy(update=update))
    for key in ['source_content_sha256','analysis_spec_sha256']:
        with pytest.raises(PublicationError):cases.attach(p(),wid,body.model_copy(update={'dataset':dataset().model_copy(update={key:'d'*64})}))
    with pytest.raises(PublicationError):cases.attach(p(),wid,body.model_copy(update={'dataset':dataset().model_copy(update={'cases':dataset().cases[:3]})}))
    s.access(p(),wid,Access(expected_access_revision=1,members=[{'actor_id':'eve','role':'editor'}],user_confirmed=True))
    with pytest.raises(PublicationError):cases.attach(p('eve'),wid,body.model_copy(update={'expected_parent_access_revision':2}))


def test_case_grants_are_exact_revision_and_new_parent_content_does_not_inherit(cases):
    wid,_=attach(cases);member(cases,wid);grant(cases,wid)
    cases.parent.publish(p(),publication(1),wid)
    assert cases.read(p('bob'),wid,1)['matching_cases']==4
    with pytest.raises(PublicationError):cases.read(p('bob'),wid,2)
    # Historical revision remains readable but cannot get a new attachment.
    with pytest.raises(PublicationError):cases.attach(p(),wid,Attach(revision=1,parent_content_sha256=cases.parent.detail(p(),wid,1)['content_sha256'],expected_parent_access_revision=2,dataset=dataset(),user_confirmed=True,case_sharing_confirmed=True))


def test_case_delete_erases_payload_and_cannot_replace_immutable_attachment(cases):
    wid,body=attach(cases);info=cases.access(p(),wid,1)
    delete=CaseDelete(revision=1,content_sha256=info['content_sha256'],expected_access_revision=1,expected_parent_access_revision=1,user_confirmed=True)
    with pytest.raises(PublicationError):cases.delete(p(),wid,delete.model_copy(update={'expected_access_revision':2}))
    # Corruption must fail reads but must never prevent owner erasure.
    with cases.parent.db.connect() as c:cases.parent.q(c,"UPDATE gateway_analysis_case_data SET payload='broken' WHERE view_id=?",(wid,))
    with pytest.raises(PublicationError):cases.read(p(),wid,1)
    cases.delete(p(),wid,delete)
    with pytest.raises(PublicationError):cases.attach(p(),wid,body)
    with cases.parent.db.connect() as c:
        row=cases.parent.q(c,'SELECT payload,deleted FROM gateway_analysis_case_data WHERE view_id=?',(wid,)).fetchone()
        assert tuple(row)==('',1)
    assert cases.parent.detail(p(),wid)['snapshot']


@pytest.mark.parametrize('mode',['delete','expire'])
def test_parent_lifecycle_erases_all_case_rows_and_grants(cases,mode):
    wid,_=attach(cases);member(cases,wid);grant(cases,wid)
    if mode=='delete':cases.parent.delete(p(),wid,Delete(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    else:
        now=cases.parent.now();cases.parent.now=lambda:now+timedelta(days=8)
        assert cases.parent.purge_expired()==1
    with cases.parent.db.connect() as c:
        for table in ['gateway_analysis_case_data','gateway_analysis_case_grants']:
            assert cases.parent.q(c,f'SELECT count(*) FROM {table}').fetchone()[0]==0
    with pytest.raises(PublicationError):cases.read(p('bob'),wid,1)


def test_concurrent_attach_and_grant_conflicts_are_atomic(cases):
    s=cases.parent;parent=s.publish(p(),publication());wid=parent['view_id']
    body=Attach(revision=1,parent_content_sha256=parent['content_sha256'],expected_parent_access_revision=1,dataset=dataset(),user_confirmed=True,case_sharing_confirmed=True)
    def publish(_):
        try:cases.attach(p(),wid,body);return 'ok'
        except PublicationError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(publish,range(2)))==['conflict','ok']
    member(cases,wid)
    def change(_):
        try:grant(cases,wid);return 'ok'
        except PublicationError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(change,range(2)))==['conflict','ok']


def test_audit_failure_rolls_back_case_publication_and_deletion(cases,monkeypatch):
    s=cases.parent;parent=s.publish(p(),publication());wid=parent['view_id'];original=s.audit
    def fail(*a,**kw):raise RuntimeError('synthetic audit failure')
    body=Attach(revision=1,parent_content_sha256=parent['content_sha256'],expected_parent_access_revision=1,dataset=dataset(),user_confirmed=True,case_sharing_confirmed=True)
    with monkeypatch.context() as patch:
        patch.setattr(s,'audit',fail)
        with pytest.raises(RuntimeError):cases.attach(p(),wid,body)
    cases.attach(p(),wid,body);info=cases.access(p(),wid,1)
    with monkeypatch.context() as patch:
        patch.setattr(s,'audit',fail)
        with pytest.raises(RuntimeError):cases.delete(p(),wid,CaseDelete(revision=1,content_sha256=info['content_sha256'],expected_access_revision=1,expected_parent_access_revision=1,user_confirmed=True))
    assert cases.read(p(),wid,1)['matching_cases']==4


@pytest.mark.parametrize('change',[
    lambda d:d.update(raw_events=['secret']),
    lambda d:d['cases'][0].update(person='secret'),
    lambda d:d['cases'][0]['events'][0].update(timestamp='2024-01-01'),
    lambda d:d['cases'][0].update(code='raw-id'),
    lambda d:d['cases'][0]['events'][0].update(activity='Original name'),
    lambda d:d['cases'][0]['events'][0].update(elapsed_microseconds=True),
    lambda d:d['cases'][0]['events'][0].update(elapsed_microseconds=0.5),
    lambda d:d['cases'][0]['events'][1].update(elapsed_microseconds=-1),
    lambda d:d['cases'][0]['events'][0].update(elapsed_microseconds=1),
    lambda d:d.update(absolute_times_included=True),
])
def test_contract_refuses_extra_fields_labels_bad_codes_and_unsafe_times(change):
    doc=dataset().model_dump(mode='json');change(doc)
    with pytest.raises(ValidationError):CaseDataset.model_validate(doc)


def test_real_http_auth_preflight_bounded_parser_scoped_queries_and_no_hidden_data(tmp_path):
    app=app_factory(tmp_path);app.state.human_access_settings.group_scope_map['analysis']|={READ,WRITE}
    h=lambda who:{'Authorization':'Bearer '+who}
    with TestClient(app) as c:
        parent=c.post('/v1/human/analyses',headers=h('alice'),json=publication().model_dump(mode='json')).json();wid=parent['view_id'];base='/v1/human/analyses/'+wid
        body=Attach(revision=1,parent_content_sha256=parent['content_sha256'],expected_parent_access_revision=1,dataset=dataset(),user_confirmed=True,case_sharing_confirmed=True).model_dump(mode='json')
        assert c.post(base+'/case-data',headers=h('read-only'),content=b'not JSON').status_code==403
        for token in ['process-only','foreign','bob']:
            assert c.post(base+'/case-data',headers=h(token),json=body).status_code in {403,404}
        for header in [{'Authorization':'Bearer admin'},{'Authorization':'OWG-Process-Session fake'}]:
            assert c.get(base+'/case-data?revision=1',headers=header).status_code==401
        assert c.post(base+'/case-data',headers=h('alice'),json=body).status_code==200
        assert c.get(base+'/case-data?revision=1',headers=h('bob')).status_code==404
        c.post(base+'/access',headers=h('alice'),json={'expected_access_revision':1,'members':[{'actor_id':'bob','role':'viewer'}],'user_confirmed':True})
        info=c.get(base+'/case-data/access?revision=1',headers=h('alice')).json()
        grant={'revision':1,'content_sha256':info['content_sha256'],'expected_access_revision':1,'expected_parent_access_revision':2,'members':[{'actor_id':'bob','role':'viewer'}],'user_confirmed':True}
        assert c.post(base+'/case-data/access',headers=h('alice'),json=grant).status_code==200
        reply=c.get(base+'/case-data?revision=1&limit=1',headers=h('bob'));assert reply.status_code==200,reply.text
        assert reply.headers['cache-control']=='no-store' and reply.json()['matching_cases']==4 and len(reply.json()['cases'])==1
        assert c.get(base+'/case-data/cases/C1?revision=1',headers=h('bob')).json()['case']['code']=='C1'
        assert c.get(base+'/case-data?revision=1&limit=501',headers=h('bob')).status_code==422
        assert c.get(base+'/case-data/export?revision=1',headers=h('bob')).headers['content-disposition'].startswith('attachment')
        assert c.get('/analysis-cases-client.js').headers['x-content-type-options']=='nosniff'
        assert c.post(base+'/case-data/access',headers=h('alice'),content=b'{"revision":1,"revision":1}',).status_code==415


def test_capacity_refuses_without_pruning_and_event_bound_is_exact(cases,monkeypatch):
    import gateway.analysis_case_data as module
    wid,body=attach(cases)
    other=cases.parent.publish(p(),publication())
    monkeypatch.setattr(module,'MAX_ATTACHMENTS',1)
    with pytest.raises(PublicationError,match='capacity'):cases.attach(p(),other['view_id'],body.model_copy(update={'parent_content_sha256':other['content_sha256']}))
    assert cases.read(p(),wid,1)['matching_cases']==4
    doc=dataset().model_dump(mode='json');doc['cases']=[{'code':'C1','events':[{'activity':'A1','elapsed_microseconds':0}]*5000}]
    assert CaseDataset.model_validate(doc)
    doc['cases'][0]['events'].append({'activity':'A1','elapsed_microseconds':0})
    with pytest.raises(ValidationError):CaseDataset.model_validate(doc)


def test_scoped_company_session_preserves_explicit_case_scope_but_not_membership(tmp_path,monkeypatch):
    import time
    from gateway.analysis_signin import AnalysisSessionStore
    app=app_factory(tmp_path)
    app.state.human_access_settings.group_scope_map['read']=frozenset({'org:analysis:read',READ,'org:evidence:read','org:process:admin'})
    with TestClient(app):
        session=app.state.analysis_session_store
        token,principal=session.issue({'iss':'https://idp.test','exp':int(time.time())+60,'sub':'bob','actor':'bob','org':'acme','groups':['read']})
        assert session.read(token).scopes==frozenset({'org:analysis:read',READ})
        parent=app.state.analysis_publications;wid=parent.publish(p(),publication())['view_id']
        with pytest.raises(PublicationError):app.state.analysis_case_data.read(principal,wid,1)
        session.revoke(token)
