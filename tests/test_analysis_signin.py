"""Analysis-only browser sessions from a verified company OIDC flow."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.human_access import HumanAccessSettings
from gateway.sso import SSOError
from gateway_identity_helpers import make_app
from test_process_collaboration import store
from test_gateway_sso_v096 import FakeIdP, ISSUER, CLIENT_ID

PUBLIC='https://gw.acme.test'


@pytest.fixture
def signed_portal(tmp_path,monkeypatch):
    monkeypatch.setenv('OWG_GATEWAY_ANALYSIS_SSO_ENABLED','true')
    monkeypatch.setenv('OWG_GATEWAY_PROCESS_SSO_ENABLED','false')
    monkeypatch.setenv('OWG_GATEWAY_SSO_ISSUER',ISSUER)
    monkeypatch.setenv('OWG_GATEWAY_SSO_CLIENT_ID',CLIENT_ID)
    monkeypatch.setenv('OWG_GATEWAY_PUBLIC_URL',PUBLIC)
    settings=HumanAccessSettings(issuer=ISSUER,audience='separate-api-resource',jwks_url=ISSUER+'/jwks',
        group_scope_map={'analysis-readers':frozenset({'org:analysis:read','self:evidence:read','org:evidence:read'}),
                         'analysis-editors':frozenset({'org:analysis:read','org:analysis:write'})})
    app,db=make_app(tmp_path,human_access=settings)
    idp=FakeIdP()
    app.state.sso_client.http_factory=lambda:httpx.Client(transport=httpx.MockTransport(idp.handler))
    with TestClient(app,base_url=PUBLIC) as client:
        yield client,app,db,idp,settings


def start(client):
    response=client.post('/v1/human/analyses/sign-in',headers={'Origin':PUBLIC})
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    cookie=response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie and 'Path=/sso/callback' in cookie
    query=parse_qs(urlsplit(response.json()['redirect_url']).query)
    assert query['code_challenge_method']==['S256']
    return query


def callback(client,idp,query,**claims):
    idp.next_claims={'nonce':query['nonce'][0],'sub':'actor-a','owg_org':'acme','email':'reader@acme.se',
        'email_verified':True,'groups':['analysis-readers'],**claims}
    return client.get('/sso/callback',params={'code':'good-code','state':query['state'][0]},follow_redirects=False)


def credentials(response):
    assert response.status_code==303
    assert response.headers['location'].startswith('/analyses#analysis_session=')
    token=parse_qs(urlsplit(response.headers['location']).fragment)['analysis_session'][0]
    return token,{'Authorization':'OWG-Analysis-Session '+token}


def test_verified_sso_session_is_minimized_analysis_only_and_revocable(signed_portal):
    client,app,db,idp,_=signed_portal
    assert client.get('/v1/human/analyses/sign-in/config').json()['company_sign_in_enabled']
    query=start(client)
    response=callback(client,idp,query)
    token,headers=credentials(response)
    assert response.headers['cache-control']=='no-store'
    assert response.headers['referrer-policy']=='no-referrer'
    me=client.get('/v1/human/analyses/session/me',headers=headers)
    assert me.json()=={'organization_id':'acme','actor_id':'actor-a','scopes':['org:analysis:read']}
    assert client.get('/v1/human/analyses',headers=headers).status_code==200
    # No ambient-cookie identity, service bearer, evidence or administrator use.
    assert client.get('/v1/human/analyses').status_code==401
    assert client.get('/v1/human/me',headers=headers).status_code==401
    assert client.get('/v1/admin/auth/me',headers=headers).status_code==401
    assert client.get('/v1/human/analyses',headers={'Authorization':'Bearer '+token}).status_code==401
    with db.connect() as conn:
        row=db._execute(conn,'SELECT session_hash, principal_json, expires_at FROM gateway_analysis_sessions').fetchone()
        assert token not in row[0]+row[1]
        identity=json.loads(row[1])
        assert set(identity)=={'subject','organization_id','actor_id','groups','issuer'}
        assert 'reader@acme.se' not in row[1] and 'id_token' not in row[1]
    assert client.post('/v1/human/analyses/session/logout',headers=headers).json()['signed_out']
    assert client.get('/v1/human/analyses',headers=headers).status_code==401


@pytest.mark.parametrize('change',['missing_cookie','foreign_cookie','no_scope','missing_org'])
def test_browser_binding_and_explicit_identity_permissions_are_required(signed_portal,change):
    client,app,db,idp,_=signed_portal
    query=start(client);claims={}
    if change in {'missing_cookie','foreign_cookie'}:
        client.cookies.clear()
        if change=='foreign_cookie':client.cookies.set('owg_analysis_login','foreign-binding',domain='gw.acme.test',path='/sso/callback')
    if change=='no_scope':claims['groups']=['evidence-only']
    if change=='missing_org':claims['owg_org']=''
    response=callback(client,idp,query,**claims)
    assert response.headers['location'].startswith('/analyses#error=')
    with db.connect() as conn:
        assert db._execute(conn,'SELECT COUNT(*) FROM gateway_analysis_sessions').fetchone()[0]==0
    assert client.get('/v1/human/analyses').status_code==401


def test_session_expiry_and_live_group_configuration_changes(signed_portal):
    client,app,db,idp,settings=signed_portal
    token,headers=credentials(callback(client,idp,start(client)))
    settings.group_scope_map['analysis-readers']=frozenset({'org:evidence:read'})
    assert client.get('/v1/human/analyses',headers=headers).status_code==401
    settings.group_scope_map['analysis-readers']=frozenset({'org:analysis:read','org:analysis:write'})
    assert client.get('/v1/human/analyses/session/me',headers=headers).json()['scopes']==['org:analysis:read','org:analysis:write']
    app.state.analysis_session_store.now=lambda:datetime.now(timezone.utc)+timedelta(minutes=16)
    assert client.get('/v1/human/analyses',headers=headers).status_code==401


def test_start_requires_same_origin_and_dedicated_browser_binding(signed_portal):
    client,_,db,idp,_=signed_portal
    for origin in (None,'https://attacker.test'):
        assert client.post('/v1/human/analyses/sign-in',headers={'Origin':origin} if origin else {}).status_code==403
    assert client.post('/v1/sso/start',json={'purpose':'analyses'}).status_code==422
    assert not idp.token_requests
    with db.connect() as conn:
        assert db._execute(conn,'SELECT COUNT(*) FROM gateway_sso_states').fetchone()[0]==0


def test_disabled_or_different_issuer_signin_never_grants_analysis_access(tmp_path,monkeypatch):
    monkeypatch.setenv('OWG_GATEWAY_SSO_ISSUER',ISSUER)
    monkeypatch.setenv('OWG_GATEWAY_SSO_CLIENT_ID',CLIENT_ID)
    monkeypatch.setenv('OWG_GATEWAY_PUBLIC_URL',PUBLIC)
    for enabled,issuer in [('false',ISSUER),('true','https://other-idp.test')]:
        monkeypatch.setenv('OWG_GATEWAY_ANALYSIS_SSO_ENABLED',enabled)
        app,_=make_app(tmp_path,human_access=HumanAccessSettings(issuer=issuer,audience='api',jwks_url=issuer+'/keys'))
        with TestClient(app,base_url=PUBLIC) as client:
            assert client.get('/v1/human/analyses/sign-in/config').json()['company_sign_in_enabled'] is False
            assert client.post('/v1/human/analyses/sign-in',headers={'Origin':PUBLIC}).status_code==404


def test_sso_state_consumption_is_atomic_across_concurrent_callbacks(signed_portal):
    client,app,db,idp,_=signed_portal
    query=start(client)
    idp.next_claims={'nonce':query['nonce'][0],'sub':'actor-a','owg_org':'acme','email':'reader@acme.se','groups':['analysis-readers']}
    def finish(_):
        try:return app.state.sso_client.finish(code='good-code',state=query['state'][0])[0]
        except SSOError:return 'already_used'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(finish,range(2)))==['already_used','analyses']
    assert len(idp.token_requests)==1


def test_session_creation_rolls_back_if_audit_fails(signed_portal,monkeypatch):
    client,app,db,idp,_=signed_portal
    original=db._execute
    def fail(conn,sql,params=()):
        if 'INSERT INTO audit_log' in sql:raise RuntimeError('synthetic audit failure')
        return original(conn,sql,params)
    monkeypatch.setattr(db,'_execute',fail)
    with pytest.raises(RuntimeError):
        app.state.analysis_session_store.issue({'iss':ISSUER,'sub':'actor-a','owg_org':'acme',
            'groups':['analysis-readers'],'exp':int(time.time())+120})
    with db.connect() as conn:
        assert original(conn,'SELECT COUNT(*) FROM gateway_analysis_sessions').fetchone()[0]==0


def test_session_storage_revocation_and_atomic_audit_on_supported_databases(store,monkeypatch):
    from gateway.analysis_signin import AnalysisSessionStore
    settings=HumanAccessSettings(issuer=ISSUER,group_scope_map={'readers':frozenset({'org:analysis:read','org:evidence:read'})})
    sessions=AnalysisSessionStore(store.db,settings);sessions.initialize()
    claims={'iss':ISSUER,'sub':'reader','owg_org':'acme','groups':['readers'],'exp':int(time.time())+300}
    token,principal=sessions.issue(claims)
    assert sessions.read(token).scopes==frozenset({'org:analysis:read'})
    original=store.db._execute
    def fail(conn,sql,params=()):
        if 'INSERT INTO audit_log' in sql:raise RuntimeError('synthetic audit failure')
        return original(conn,sql,params)
    with monkeypatch.context() as patch:
        patch.setattr(store.db,'_execute',fail)
        with pytest.raises(RuntimeError):sessions.revoke(token)
    assert sessions.read(token).actor_id=='reader'
    sessions.revoke(token)
    with pytest.raises(ValueError):sessions.read(token)


def test_analysis_session_and_process_session_cannot_cross_portals(signed_portal):
    client,app,db,idp,settings=signed_portal
    from gateway.process_signin import ProcessSessionStore
    # Give the same verified human both permissions, then independently issue
    # each kind of session. Even this identity must use the right credential.
    settings.group_scope_map['analysis-readers'] |= {'org:process:read','org:process:write'}
    token,headers=credentials(callback(client,idp,start(client)))
    claims={'iss':ISSUER,'sub':'actor-a','owg_org':'acme','groups':['analysis-readers'],'exp':int(time.time())+300}
    process=ProcessSessionStore(db,settings)
    other,_=process.issue(claims)
    for path in ['/v1/human/processes','/v1/human/processes/session/me','/v1/admin/auth/me','/v1/human/me']:
        assert client.get(path,headers=headers).status_code==401
    for path in ['/v1/human/analyses','/v1/human/analyses/session/me']:
        for scheme,value in [('OWG-Process-Session',other),('OWG-Analysis-Session',other),('OWG-Process-Session',token),('Bearer',token)]:
            assert client.get(path,headers={'Authorization':scheme+' '+value}).status_code==401
    assert client.get('/v1/human/analyses',headers=headers).status_code==200
    assert client.post('/v1/human/processes/session/logout',headers=headers).status_code==401
    assert client.get('/v1/human/analyses',headers=headers).status_code==200


def test_analysis_sso_does_not_grant_membership_or_write(signed_portal):
    from test_shared_analysis_publications import publication, person
    client,app,db,idp,settings=signed_portal
    settings.group_scope_map['analysis-readers'] |= {'org:process:admin','org:process:write'}
    token,headers=credentials(callback(client,idp,start(client)))
    publications=app.state.analysis_publications
    wid=publications.publish(person('another-owner'),publication())['view_id']
    assert client.get('/v1/human/analyses',headers=headers).json()['views']==[]
    assert client.get('/v1/human/analyses/'+wid,headers=headers).status_code==404
    from gateway.analysis_publications import Access
    publications.access(person('another-owner'),wid,Access(expected_access_revision=1,
        members=[{'actor_id':'actor-a','role':'viewer'}],user_confirmed=True))
    assert client.get('/v1/human/analyses/'+wid,headers=headers).status_code==200
    assert client.get('/v1/human/analyses/'+wid+'/export?revision=1',headers=headers).status_code==200
    assert client.post('/v1/human/analyses',headers=headers,content=b'not even JSON').status_code==403
    settings.group_scope_map['analysis-readers'] |= {'org:analysis:write'}
    # Editor membership is still required even after the company grants write.
    assert client.post('/v1/human/analyses/'+wid,headers=headers,content=b'not JSON').status_code==403
    publications.access(person('another-owner'),wid,Access(expected_access_revision=2,
        members=[{'actor_id':'actor-a','role':'editor'}],user_confirmed=True))
    request=publication(1).model_dump(mode='json')
    assert client.post('/v1/human/analyses/'+wid,headers=headers,json=request).status_code==200
    publications.access(person('another-owner'),wid,Access(expected_access_revision=3,members=[],user_confirmed=True))
    assert client.get('/v1/human/analyses/'+wid+'/export?revision=1',headers=headers).status_code==404


@pytest.mark.parametrize('claims',[
    {'aud':'wrong-audience'}, {'iss':'https://wrong-issuer.test'}, {'nonce':'foreign'},
    {'exp':int(time.time())-120}, {'email_verified':False}, {'groups':['process-only']},
    {'groups':['analysis-readers']+['group-'+str(i) for i in range(101)]},
    {'groups':['analysis-readers','oversized-group'*30]},
])
def test_invalid_provider_claims_never_issue_analysis_sessions(signed_portal,claims):
    client,_,db,idp,settings=signed_portal
    settings.group_scope_map['process-only']=frozenset({'org:process:read','org:process:write'})
    response=callback(client,idp,start(client),**claims)
    assert 'analysis_session=' not in response.headers['location']
    with db.connect() as conn:
        assert db._execute(conn,'SELECT COUNT(*) FROM gateway_analysis_sessions').fetchone()[0]==0


def test_process_binding_cannot_finish_analysis_login_and_state_is_single_use(signed_portal):
    client,_,db,idp,_=signed_portal
    query=start(client)
    cookie=client.cookies.get('owg_analysis_login')
    client.cookies.clear()
    client.cookies.set('owg_process_login',cookie,domain='gw.acme.test',path='/sso/callback')
    response=callback(client,idp,query)
    assert response.headers['location'].startswith('/analyses#error=')
    client.cookies.set('owg_analysis_login',cookie,domain='gw.acme.test',path='/sso/callback')
    replay=callback(client,idp,query)
    assert 'analysis_session=' not in replay.headers['location']
    assert len(idp.token_requests)==1


def test_analysis_feature_flag_does_not_enable_process_signin(signed_portal):
    client,_,_,_,_=signed_portal
    assert client.get('/v1/human/analyses/sign-in/config').json()['company_sign_in_enabled']
    assert not client.get('/v1/human/processes/sign-in/config').json()['company_sign_in_enabled']
    assert client.post('/v1/human/processes/sign-in',headers={'Origin':PUBLIC}).status_code==404


def test_session_lifetime_is_clipped_to_verified_expiry_and_fifteen_minutes(signed_portal):
    client,app,db,idp,_=signed_portal
    sessions=app.state.analysis_session_store
    now=datetime(2026,1,1,tzinfo=timezone.utc);sessions.now=lambda:now
    claims={'iss':ISSUER,'sub':'actor-a','owg_org':'acme','groups':['analysis-readers']}
    for seconds in (45,7200):
        token,_=sessions.issue({**claims,'exp':(now+timedelta(seconds=seconds)).timestamp()})
        sessions.now=lambda:now+timedelta(seconds=min(seconds,900)-1)
        assert sessions.read(token).actor_id=='actor-a'
        sessions.now=lambda:now+timedelta(seconds=min(seconds,900))
        with pytest.raises(ValueError):sessions.read(token)
        sessions.now=lambda:now
    with pytest.raises(ValueError):sessions.issue({**claims,'exp':now.timestamp()})
    with db.connect() as conn:
        encoded=json.dumps([list(row) for row in db._execute(conn,'SELECT * FROM gateway_analysis_sessions').fetchall()])
    assert 'org:evidence:read' not in encoded and 'org:analysis:read' not in encoded
