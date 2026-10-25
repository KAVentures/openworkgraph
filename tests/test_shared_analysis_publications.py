"""Shared aggregate artifacts: tenant/ACL isolation, CAS, expiry, retention and real HTTP."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gateway.analysis_publications import AnalysisPublications, Access, Delete, Publish, PublicationError
from gateway.hardening import HardeningSettings
from gateway.human_access import HumanAccessSettings, HumanPrincipal, _normalize_group_map
from gateway.human_enterprise_app import create_human_enterprise_app
from gateway.settings import GatewaySettings
from shared.analysis_snapshot import Snapshot, content_digest
from test_process_collaboration import store as process_store

SCOPES=frozenset({'org:analysis:read','org:analysis:write'})


def person(actor='alice',org='acme',scopes=SCOPES):
    return HumanPrincipal(subject=actor,organization_id=org,actor_id=actor,groups=frozenset(),scopes=scopes,issuer='https://idp.test')


def snapshot(value=4):
    return Snapshot(generated_at=datetime(2026,1,1,tzinfo=timezone.utc),level='cases',
        columns=[{'heading':'Group 1','kind':'group'},{'heading':'Measure 1','kind':'number','unit':'cases','aggregation':'count'}],
        rows=[['G1_1',value]],matching_cases=4,matching_rows=4,rows_truncated=False,
        source_content_sha256='a'*64,analysis_spec_sha256='b'*64)


def publication(expected=0,**kwargs):
    return Publish(title='Selected case counts',snapshot=snapshot(),expected_revision=expected,
                   user_confirmed=True,organization_sharing_confirmed=True,**kwargs)


@pytest.fixture
def store(process_store):
    result=AnalysisPublications(process_store.db);result.initialize();return result


def access(store,wid,members,expected=1):
    return store.access(person(),wid,Access(expected_access_revision=expected,members=members,user_confirmed=True))


def test_default_denial_tenant_scope_and_no_hidden_inventory(store):
    d=store.publish(person(),publication());wid=d['view_id']
    assert d['source_freshness']=='unknown_source_not_shared' and not d['execution_authorized']
    assert d['content_sha256']==content_digest(d['snapshot'])
    for p in [person('bob'),person(org='other'),person('root','other',SCOPES|{'org:analysis:admin'})]:
        assert store.inventory(p)['views']==[]
        with pytest.raises(PublicationError) as error:store.detail(p,wid)
        assert error.value.status==404
    for scopes in [frozenset(),frozenset({'org:process:read','org:process:admin'}),frozenset({'org:evidence:read'})]:
        with pytest.raises(PublicationError) as error:store.detail(person(scopes=scopes),wid)
        assert error.value.status==403
    assert store.detail(person('root',scopes=SCOPES|{'org:analysis:admin'}),wid)['your_role']=='admin'


def test_member_read_editor_history_revoke_and_audited_exports(store):
    first=store.publish(person(),publication());wid=first['view_id']
    access(store,wid,[{'actor_id':'bob','role':'viewer'},{'actor_id':'eve','role':'editor'}])
    assert store.detail(person('bob'),wid)['snapshot']==first['snapshot']
    with pytest.raises(PublicationError):store.publish(person('bob'),publication(1),wid)
    second=store.publish(person('eve'),publication(1),wid)
    assert second['revision']==2 and second['expires_at']==first['expires_at']
    assert store.detail(person('bob'),wid,1)['snapshot']==first['snapshot']
    assert not store.detail(person(),wid,1)['permissions']['edit']
    store.detail(person('bob'),wid,2,exporting=True)
    access(store,wid,[],expected=2)
    for revision in [1,2,None]:
        with pytest.raises(PublicationError) as error:store.detail(person('bob'),wid,revision,exporting=True)
        assert error.value.status==404
    with pytest.raises(PublicationError):store.access(person('eve'),wid)
    audit=store.db.audit_rows('acme',100)
    assert any(r['action']=='analysis.exported' for r in audit)
    encoded=json.dumps(audit)
    assert 'Selected case counts' not in encoded and 'G1_1' not in encoded


def test_atomic_revision_and_access_conflicts(store):
    wid=store.publish(person(),publication())['view_id']
    def update(_):
        try:return store.publish(person(),publication(1),wid)['revision']
        except PublicationError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:out=list(pool.map(update,range(2)))
    assert sorted(map(str,out))==['2','conflict']
    access(store,wid,[{'actor_id':'bob','role':'viewer'}])
    with pytest.raises(PublicationError):access(store,wid,[],expected=1)
    assert store.detail(person('bob'),wid)['revision']==2
    assert store.access(person(),wid)['access_revision']==2


def test_expiry_purges_even_when_request_returns_404_and_never_revives(store):
    current=datetime(2026,1,1,tzinfo=timezone.utc);store.now=lambda:current
    d=store.publish(person(),publication(expires_in_days=1));wid=d['view_id']
    access(store,wid,[{'actor_id':'bob','role':'viewer'}])
    current+=timedelta(days=1)
    with pytest.raises(PublicationError) as error:store.detail(person('bob'),wid)
    assert error.value.status==404
    with store.db.connect() as c:
        for table in ['gateway_analysis_views','gateway_analysis_revisions','gateway_analysis_members']:
            assert store.q(c,f'SELECT count(*) FROM {table} WHERE organization_id=? AND view_id=?',('acme',wid)).fetchone()[0]==0
    assert store.purge_expired()==0
    with pytest.raises(PublicationError):store.access(person(),wid,Access(expected_access_revision=2,members=[],expires_in_days=90,user_confirmed=True))


def test_owner_renewal_deletion_and_stale_delete_are_separate_from_revision(store):
    d=store.publish(person(),publication());wid=d['view_id']
    renewed=store.access(person(),wid,Access(expected_access_revision=1,members=[],expires_in_days=90,user_confirmed=True))
    assert renewed['expires_at']>d['expires_at']
    with pytest.raises(PublicationError):store.delete(person(),wid,Delete(expected_revision=1,expected_access_revision=1,user_confirmed=True))
    assert store.delete(person(),wid,Delete(expected_revision=1,expected_access_revision=2,user_confirmed=True))['deleted']
    with pytest.raises(PublicationError):store.detail(person(),wid)


def test_revision_quota_no_silent_pruning_or_cross_tenant_capacity(store,monkeypatch):
    import gateway.analysis_publications as module
    monkeypatch.setattr(module,'MAX_VIEWS',1)
    wid=store.publish(person(),publication())['view_id']
    with pytest.raises(PublicationError):store.publish(person('bob'),publication())
    assert store.publish(person(org='other'),publication())['revision']==1
    for revision in range(1,10):store.publish(person(),publication(revision),wid)
    with pytest.raises(PublicationError):store.publish(person(),publication(10),wid)
    assert store.detail(person(),wid,1)['viewing_revision']==1


@pytest.mark.parametrize('change',[
    {'execution_authorized':True}, {'source_independently_verified':True}, {'case_ids':['private']},
    {'rows':[['private-case-1',4]]}, {'rows':[['G1_1',float('inf')]]}, {'rows':[['G1_1',True]]},
    {'rows':[['G1_1',2**60]]}, {'rows':[['G1_1',1e308]]}, {'rows':[['G1_1',4,5]]},
    {'rows':[['G1_1',4]]*101}, {'generated_at':'2026-01-01T00:00:00'},
])
def test_snapshot_rejects_hidden_content_unsafe_values_and_authority(change):
    with pytest.raises(ValidationError):Snapshot.model_validate(snapshot().model_dump(mode='json')|change)


def test_grants_scopes_and_confirmations_are_explicit(store):
    mapping=_normalize_group_map('{"analysts":["org:analysis:read","org:analysis:write"]}')
    assert mapping['analysts']==SCOPES
    with pytest.raises(PublicationError):store.publish(person(),publication().model_copy(update={'organization_sharing_confirmed':False}))
    with pytest.raises(ValidationError):Access(expected_access_revision=1,members=[{'actor_id':'bob','role':'viewer'}]*2)


def test_corrupt_publication_never_serves_unchecked_data_or_a_false_digest(store):
    d=store.publish(person(),publication());wid=d['view_id']
    with store.db.connect() as c:
        body=d['snapshot'];body['rows'][0][-1]=123
        store.q(c,'UPDATE gateway_analysis_revisions SET payload=? WHERE organization_id=? AND view_id=?',
                (json.dumps(body),'acme',wid))
    with pytest.raises(PublicationError,match='integrity check'):store.detail(person(),wid)
    assert store.delete(person(),wid,Delete(expected_revision=1,expected_access_revision=1,user_confirmed=True))['deleted']


class Verifier:
    def verify(self,token):
        if token not in {'alice','bob','foreign','process-only','read-only'}:raise ValueError('invalid')
        return {'sub':token,'actor':token,'org':'other' if token=='foreign' else 'acme',
                'groups':['process'] if token=='process-only' else ['read'] if token=='read-only' else ['analysis']}


def app(tmp_path):
    settings=GatewaySettings(database_url=f'sqlite:///{tmp_path / "shared-api.db"}',admin_token='admin',enrollment_token='enroll')
    human=HumanAccessSettings(issuer='https://idp.test',audience='owg',jwks_url='https://idp.test/jwks',organization_claim='org',actor_claim='actor',
        group_scope_map={'analysis':SCOPES,'read':frozenset({'org:analysis:read'}),'process':frozenset({'org:process:read'})})
    return create_human_enterprise_app(settings=settings,hardening=HardeningSettings(),human_access=human,verifier=Verifier())


def test_real_http_portal_and_no_cross_scope_session_or_service_fallback(tmp_path):
    with TestClient(app(tmp_path)) as c:
        headers=lambda who:{'Authorization':'Bearer '+who}
        portal=c.get('/analyses');assert portal.status_code==200
        assert "script-src 'nonce-" in portal.headers['content-security-policy'] and '__NONCE__' not in portal.text
        assert 'localStorage' not in portal.text and 'sessionStorage' not in portal.text
        assert c.get('/analysis-snapshot-client.js').headers['x-content-type-options']=='nosniff'
        for token in ['admin','enroll','bad']:
            assert c.get('/v1/human/analyses',headers=headers(token)).status_code==401
        assert c.get('/v1/human/analyses',headers={'Authorization':'OWG-Process-Session bad'}).status_code==401
        assert c.get('/v1/human/analyses',headers=headers('process-only')).status_code==403
        value=publication().model_dump(mode='json')
        assert c.post('/v1/human/analyses',json=value,headers=headers('read-only')).status_code==403
        made=c.post('/v1/human/analyses',json=value,headers=headers('alice'));assert made.status_code==200,made.text
        assert made.headers['cache-control']=='no-store'
        wid=made.json()['view_id'];base='/v1/human/analyses/'+wid
        assert c.get(base,headers=headers('bob')).status_code==404
        assert c.get(base+'?revision='+str(10**100),headers=headers('alice')).status_code==422
        assert c.post(base,json={'not':'a publication'},headers=headers('read-only')).status_code==403
        assert c.get(base,headers=headers('foreign')).status_code==404
        grant={'expected_access_revision':1,'members':[{'actor_id':'bob','role':'viewer'}],'user_confirmed':True}
        assert c.post(base+'/access',json=grant,headers=headers('alice')).status_code==200
        report=c.get(base+'/export?revision=1',headers=headers('bob'));assert report.status_code==200
        assert report.json()['snapshot']==made.json()['snapshot'] and 'attachment' in report.headers['content-disposition']
        assert c.post(base+'/access',json={**grant,'expected_access_revision':2,'members':[]},headers=headers('alice')).status_code==200
        assert c.get(base+'/export?revision=1',headers=headers('bob')).status_code==404
        assert c.post('/v1/human/analyses',content=b'{"title":"x","title":"y"}',headers={**headers('alice'),'Content-Type':'application/json'}).status_code==422
        assert c.post('/v1/human/analyses',content=b'x'*(161*1024),headers={**headers('alice'),'Content-Type':'application/json'}).status_code==413
