"""Roster disablement must apply beyond device enrollment credentials."""
import pytest
import asyncio
import json
import time
from fastapi.testclient import TestClient
from gateway import employees as roster
from gateway.human_access import HumanAccessSettings,init_human_access_schema
from gateway.identity_routes import _RosterMappingVerifier
from gateway_identity_helpers import make_app
from test_process_collaboration import store
from test_process_signin import signed_portal as process_signed_portal
from test_analysis_signin import signed_portal as analysis_signed_portal
import test_process_signin as process_flow
import test_analysis_signin as analysis_flow


class Verifier:
    def __init__(self,actor='opaque-person',org='acme'):self.actor=actor;self.org=org
    def verify(self,token):return {'sub':self.actor,'owg_org':self.org,'groups':['readers']}


def employee(db,*,org='acme',email='person@acme.test',subject='opaque-person'):
    roster.init_employee_schema(db);init_human_access_schema(db)
    value,_=roster.upsert_employee(db,org,email=email)
    with db.connect() as c:db._execute(c,'UPDATE gateway_employees SET sso_subject=? WHERE employee_id=?',(subject,value['employee_id']))
    return value


@pytest.mark.parametrize('identity',['opaque-person','person@acme.test'])
def test_delegated_identity_is_denied_after_roster_offboarding(store,identity):
    db=store.db;e=employee(db);settings=HumanAccessSettings(actor_claim='sub',organization_claim='owg_org')
    verifier=_RosterMappingVerifier(Verifier(identity),settings,db)
    assert verifier.verify('valid-provider-token')['sub']=='person@acme.test'
    roster.offboard_employee(db,'acme',e['employee_id'])
    with pytest.raises(ValueError):verifier.verify('same-still-valid-provider-token')


def test_inactive_roster_identity_is_denied_at_real_delegated_http_entry(tmp_path):
    settings=HumanAccessSettings(issuer='https://idp.test',audience='api',jwks_url='https://idp.test/jwks',group_scope_map={'readers':frozenset({'org:process:read','org:analysis:read'})})
    app,db=make_app(tmp_path,human_access=settings,verifier=Verifier())
    with TestClient(app) as client:
        e=employee(db);headers={'Authorization':'Bearer still-valid-provider-token'}
        assert client.get('/v1/human/processes',headers=headers).status_code==200
        assert client.get('/v1/human/analyses',headers=headers).status_code==200
        roster.offboard_employee(db,'acme',e['employee_id'])
        for path in ['/v1/human/me','/v1/human/processes','/v1/human/analyses']:
            assert client.get(path,headers=headers).status_code==401


@pytest.fixture(params=['process','analysis'])
def portal(request):
    return request.param,request.getfixturevalue(request.param+'_signed_portal')


def test_existing_portal_session_and_fresh_sso_are_denied_after_offboarding(portal):
    kind,(client,app,db,idp,settings)=portal;flow=process_flow if kind=='process' else analysis_flow
    e=employee(db,subject='actor-a')
    _,headers=flow.credentials(flow.callback(client,idp,flow.start(client)))
    path='/v1/human/'+('processes' if kind=='process' else 'analyses')
    assert client.get(path,headers=headers).status_code==200
    roster.offboard_employee(db,'acme',e['employee_id'])
    assert client.get(path,headers=headers).status_code==401
    response=flow.callback(client,idp,flow.start(client))
    assert response.headers['location'].startswith('/'+('processes' if kind=='process' else 'analyses')+'#error=')


def test_rehiring_does_not_restore_old_browser_sessions(portal):
    kind,(client,app,db,idp,settings)=portal;flow=process_flow if kind=='process' else analysis_flow
    e=employee(db,subject='actor-a')
    _,headers=flow.credentials(flow.callback(client,idp,flow.start(client)))
    path='/v1/human/'+('processes' if kind=='process' else 'analyses')
    roster.offboard_employee(db,'acme',e['employee_id'])
    roster.upsert_employee(db,'acme',email=e['email'])
    assert client.get(path,headers=headers).status_code==401
    _,fresh=flow.credentials(flow.callback(client,idp,flow.start(client)))
    assert client.get(path,headers=fresh).status_code==200


def test_tenant_local_status_does_not_require_a_roster_for_unregistered_users(store):
    db=store.db;e=employee(db);roster.offboard_employee(db,'acme',e['employee_id'])
    settings=HumanAccessSettings(actor_claim='sub',organization_claim='owg_org')
    assert _RosterMappingVerifier(Verifier('new-user'),settings,db).verify('token')['sub']=='new-user'
    assert _RosterMappingVerifier(Verifier('opaque-person','other'),settings,db).verify('token')['sub']=='opaque-person'


def test_conflicting_roster_mappings_are_withheld_instead_of_choosing_first(store):
    db=store.db;employee(db);employee(db,email='another@acme.test')
    settings=HumanAccessSettings(actor_claim='sub',organization_claim='owg_org')
    with pytest.raises(ValueError):_RosterMappingVerifier(Verifier(),settings,db).verify('token')


@pytest.mark.parametrize('kind',['process','analysis'])
def test_monotonic_revocation_does_not_depend_on_clock_and_legacy_sessions_need_rebinding(store,kind,monkeypatch):
    from gateway.portal_signin import PortalSessionStore
    from gateway.auth import token_hash
    db=store.db;e=employee(db)
    settings=HumanAccessSettings(issuer='https://idp.test',group_scope_map={'readers':frozenset({'org:'+kind+':read'})})
    sessions=PortalSessionStore(db,settings,portal=kind);sessions.initialize()
    claims={'iss':settings.issuer,'sub':'opaque-person','owg_org':'acme','groups':['readers'],'exp':int(time.time())+300}
    monkeypatch.setattr(roster,'_now',lambda:'2000-01-01T00:00:00.000000Z')
    tokens=[]
    for expected in range(3):
        token,p=sessions.issue(claims);tokens.append(token);assert p.actor_id==e['actor_id']
        with db.connect() as c:
            raw=db._execute(c,f'SELECT principal_json FROM {sessions.table} WHERE session_hash=?',(token_hash(token),)).fetchone()[0]
            assert json.loads(raw)['roster_binding']=={'employee_id':e['employee_id'],'generation':expected}
        roster.offboard_employee(db,'acme',e['employee_id']);roster.upsert_employee(db,'acme',email=e['email'])
        for old in tokens:
            with pytest.raises(ValueError):sessions.read(old)
    fresh,_=sessions.issue(claims);assert sessions.read(fresh).actor_id==e['actor_id']
    roster.upsert_employee(db,'acme',email=e['email'],display_name='Updated display name')
    assert sessions.read(fresh).actor_id==e['actor_id']  # A name edit is not revocation.
    # Simulate an existing v1 session lacking the new roster binding. An active
    # employee needs fresh sign-in; upgrading must not reopen pre-offboard access.
    with db.connect() as c:
        row=db._execute(c,f'SELECT principal_json FROM {sessions.table} WHERE session_hash=?',(token_hash(fresh),)).fetchone()
        raw=json.loads(row[0]);raw.pop('roster_binding')
        db._execute(c,f'UPDATE {sessions.table} SET principal_json=? WHERE session_hash=?',(json.dumps(raw),token_hash(fresh)))
    with pytest.raises(ValueError):sessions.read(fresh)


def test_revocation_epoch_failure_rolls_back_employee_and_device_disable(store,monkeypatch):
    db=store.db;e=employee(db);original=db._execute
    with db.connect() as c:
        db._execute(c,"INSERT INTO access_tokens VALUES ('device-test','hash','device','acme',?,'laptop','[]','2026-01-01',NULL)",(e['actor_id'],))
    def fail(c,sql,args=()):
        if 'INSERT INTO gateway_employee_access_epochs' in sql:raise RuntimeError('Synthetic epoch write failure')
        return original(c,sql,args)
    with monkeypatch.context() as patch:
        patch.setattr(db,'_execute',fail)
        with pytest.raises(RuntimeError):roster.offboard_employee(db,'acme',e['employee_id'])
    assert roster.get_employee(db,'acme',e['employee_id'])['status']=='active'
    with db.connect() as c:assert db._execute(c,"SELECT revoked_at FROM access_tokens WHERE token_id='device-test'").fetchone()[0] is None


def test_remote_mcp_auth_rechecks_current_offboarding_without_token_cache(store):
    from gateway.conversation_mcp import DelegatedTokenVerifier
    db=store.db;e=employee(db)
    class Delegated(Verifier):
        def verify(self,token):return {**super().verify(token),'scope':'owg.read','exp':int(time.time())+300}
    settings=HumanAccessSettings(actor_claim='sub',organization_claim='owg_org',mcp_oauth_scope='owg.read')
    verifier=DelegatedTokenVerifier(settings,_RosterMappingVerifier(Delegated(),settings,db))
    assert asyncio.run(verifier.verify_token('same-valid-provider-token')) is not None
    roster.offboard_employee(db,'acme',e['employee_id'])
    assert asyncio.run(verifier.verify_token('same-valid-provider-token')) is None


def test_untrusted_email_claim_does_not_bind_or_disable_unregistered_actor(store):
    db=store.db;e=employee(db);roster.offboard_employee(db,'acme',e['employee_id'])
    class Other(Verifier):
        def verify(self,token):return {**super().verify(token),'sub':'unrelated-user','email':e['email'],'email_verified':False}
    settings=HumanAccessSettings(actor_claim='sub',organization_claim='owg_org')
    assert _RosterMappingVerifier(Other(),settings,db).verify('token')['sub']=='unrelated-user'
