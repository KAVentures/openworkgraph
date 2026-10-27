"""Reconstructed old inconsistent authority must not survive upgrade/rehire."""
import pytest
from gateway import employees as roster,scim_provisioning as scim
from test_process_collaboration import store
from test_scim_enrollment_race import account


def legacy(db):
    service,pid,provider_token,made=account(db)
    uid=made['id'];invite=roster.create_personal_invite(db,'acme',uid)
    db.put_token(token_id='owned-legacy-device',token='owned-legacy-device-secret',token_type='device',
        organization_id='acme',actor_id=made['userName'],device_id='legacy-laptop',scopes={'events:write'})
    disabled=service.patch_user(pid,provider_token,uid,made['meta']['version'],
        {'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False}]})
    assert db.authenticate('owned-legacy-device-secret') is None
    # This deliberate fault reconstructs only a persisted pre-fix/race state.
    # It is not a new enrollment through the fixed current offboarding flow.
    with db.connect() as conn:
        db._execute(conn,'UPDATE access_tokens SET revoked_at=NULL WHERE token_id=?',('owned-legacy-device',))
        db._execute(conn,'UPDATE gateway_personal_invites SET revoked_at=NULL WHERE invite_id=?',(invite['invite_id'],))
    return service,pid,provider_token,disabled,invite


def rows(db,table):
    with db.connect() as conn:return [tuple(r) for r in db._execute(conn,'SELECT * FROM '+table+' ORDER BY 1').fetchall()]


def test_known_inactive_roster_device_cannot_authenticate_before_repair(store):
    db=store.db;legacy(db)
    assert db.authenticate('owned-legacy-device-secret') is None


def test_optional_roster_install_and_unrelated_credentials_keep_their_authority(store):
    db=store.db
    def key(identifier,org,actor,kind='device'):
        db.put_token(token_id=identifier,token=identifier+'-secret',token_type=kind,
            organization_id=org,actor_id=actor,device_id=identifier,scopes={'evidence:write'})
    key('core','acme','unknown')
    assert db.authenticate('core-secret') is not None  # Core Gateway has no roster.
    _,_,_,disabled,_=legacy(db)
    key('other-org','other',disabled['userName'])
    key('unknown','acme','unknown')
    key('admin','acme',disabled['userName'],'admin')
    active,_=roster.upsert_employee(db,'acme',email='active@example.test')
    key('active','acme',active['actor_id'])
    for token in ['core','other-org','unknown','admin','active']:
        assert db.authenticate(token+'-secret') is not None
    roster.init_employee_schema(db)
    for token in ['core','other-org','unknown','admin','active']:
        assert db.authenticate(token+'-secret') is not None


def test_known_inactive_explicit_device_binding_is_checked_even_with_an_old_actor(store):
    db=store.db;_,_,_,disabled,_=legacy(db)
    db.put_token(token_id='mapped',token='mapped-secret',token_type='device',organization_id='acme',
        actor_id='old-unlinked-actor',device_id='mapped-device',scopes={'evidence:write'})
    with db.connect() as conn:
        db._execute(conn,'''INSERT INTO gateway_device_identity
            (organization_id,device_id,employee_id,identity_source,linked_at,linked_by) VALUES (?,?,?,?,?,?)''',
            ('acme','mapped-device',disabled['id'],'admin_linked',roster._now(),'owned-admin'))
    assert db.authenticate('mapped-secret') is None
    roster.upsert_employee(db,'acme',email=disabled['userName'])
    assert db.authenticate('mapped-secret') is None


def test_real_http_refuses_legacy_inactive_device_and_never_revives_it_on_rehire(tmp_path):
    from fastapi.testclient import TestClient
    from gateway_identity_helpers import make_app,bootstrap_owner,add_employee,personal_invite,enroll,ingest
    app,db=make_app(tmp_path)
    with TestClient(app) as client:
        owner,_=bootstrap_owner(client)
        e=add_employee(client,owner,'legacy@example.test')
        invite=personal_invite(client,owner,e['employee_id'])
        device=enroll(client,invite['token'],'legacy-device').json()
        ingest(client,device['token'],'historical')
        roster.offboard_employee(db,'acme',e['employee_id'])
        with db.connect() as conn:
            db._execute(conn,"UPDATE access_tokens SET revoked_at=NULL WHERE token_type='device' AND device_id=?",('legacy-device',))
            db._execute(conn,'UPDATE gateway_personal_invites SET revoked_at=NULL WHERE invite_id=?',(invite['invite_id'],))
        url='/v1/evidence/batch';headers={'Authorization':'Bearer '+device['token']}
        assert client.post(url,headers=headers,json={'events':[]}).status_code==401
        add_employee(client,owner,'legacy@example.test')
        assert client.post(url,headers=headers,json={'events':[]}).status_code==401
        assert enroll(client,invite['token'],'old-invite-device').status_code in (400,401,403)
        fresh=personal_invite(client,owner,e['employee_id'])
        new=enroll(client,fresh['token'],'fresh-device');assert new.status_code==200,new.text
        ingest(client,new.json()['token'],'fresh-event')
        with db.connect() as conn:
            assert conn.execute('SELECT count(*) FROM evidence_events').fetchone()[0]==2
            assert conn.execute("SELECT count(*) FROM access_tokens WHERE device_id='legacy-device' AND revoked_at IS NOT NULL").fetchone()[0]==1
def test_startup_repairs_legacy_revocation_without_rewriting_identity_or_evidence(store):
    db=store.db;_,_,_,disabled,invite=legacy(db)
    tables=['gateway_employees','gateway_employee_access_epochs','gateway_actor_teams','evidence_events','audit_log','gateway_shared_processes']
    before={t:rows(db,t) for t in tables}
    roster.init_employee_schema(db)
    assert db.authenticate('owned-legacy-device-secret') is None
    with db.connect() as conn:
        assert db._execute(conn,'SELECT revoked_at FROM access_tokens WHERE token_id=?',('owned-legacy-device',)).fetchone()[0]
        assert db._execute(conn,'SELECT revoked_at FROM gateway_personal_invites WHERE invite_id=?',(invite['invite_id'],)).fetchone()[0]
    assert {t:rows(db,t) for t in tables}==before
    revoked=(rows(db,'access_tokens'),rows(db,'gateway_personal_invites'))
    roster.init_employee_schema(db)
    assert (rows(db,'access_tokens'),rows(db,'gateway_personal_invites'))==revoked


@pytest.mark.parametrize('method',['manual','scim'])
def test_explicit_rehire_permanently_revokes_legacy_credentials_before_activation(store,method):
    db=store.db;service,pid,provider_token,disabled,invite=legacy(db)
    if method=='manual':roster.upsert_employee(db,'acme',email=disabled['userName'])
    else:service.patch_user(pid,provider_token,disabled['id'],disabled['meta']['version'],
        {'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':True}]})
    assert roster.get_employee(db,'acme',disabled['id'])['status']=='active'
    assert db.authenticate('owned-legacy-device-secret') is None
    assert roster.peek_personal_invite(db,invite['token']) is None
    fresh=roster.create_personal_invite(db,'acme',disabled['id'])
    enrolled=roster.enroll_with_personal_invite(db,token=fresh['token'],requested_organization_id='acme',device_id='fresh-laptop')
    assert db.authenticate(enrolled['token']) is not None


@pytest.mark.parametrize('method',['startup','manual','scim'])
def test_repair_and_rehire_revocations_roll_back_atomically_on_failure(store,monkeypatch,method):
    db=store.db;service,pid,provider_token,disabled,_=legacy(db)
    tables=['gateway_employees','gateway_employee_access_epochs','access_tokens','gateway_personal_invites','gateway_scim_users','audit_log']
    before={t:rows(db,t) for t in tables};execute=db._execute
    def fail(conn,sql,params=()):
        result=execute(conn,sql,params)
        if 'UPDATE gateway_personal_invites SET revoked_at' in sql:raise RuntimeError('Injected legacy repair failure')
        return result
    monkeypatch.setattr(db,'_execute',fail)
    with pytest.raises(RuntimeError,match='Injected'):
        if method=='startup':roster.init_employee_schema(db)
        elif method=='manual':roster.upsert_employee(db,'acme',email=disabled['userName'])
        else:service.patch_user(pid,provider_token,disabled['id'],disabled['meta']['version'],
            {'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':True}]})
    assert {t:rows(db,t) for t in tables}==before


@pytest.mark.parametrize('method',['startup','manual','scim'])
def test_pooled_gateway_reconciles_the_same_inactive_lifecycle(store,method):
    from gateway.hardening import PooledGatewayDB
    db=PooledGatewayDB(store.db.database_url,pool_max_size=4)
    try:
        service,pid,provider_token,disabled,invite=legacy(db)
        if method=='startup':roster.init_employee_schema(db)
        elif method=='manual':roster.upsert_employee(db,'acme',email=disabled['userName'])
        else:service.patch_user(pid,provider_token,disabled['id'],disabled['meta']['version'],
            {'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':True}]})
        assert db.authenticate('owned-legacy-device-secret') is None
        assert roster.peek_personal_invite(db,invite['token']) is None
        assert bool(db._pool) is db.is_postgres
    finally:db.close()


def test_startup_rechecks_locked_status_and_preserves_a_concurrent_fresh_rehire(store,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event,get_ident
    db=store.db;_,_,_,disabled,_=legacy(db)
    entered,release=Event(),Event();lock=roster._lock_employee;owner={}
    def pause(db,conn,org,uid):
        if get_ident()==owner.get('thread'):
            entered.set();assert release.wait(10)
        return lock(db,conn,org,uid)
    monkeypatch.setattr(roster,'_lock_employee',pause)
    def startup():
        owner['thread']=get_ident();roster.init_employee_schema(db)
    def rehire():
        roster.upsert_employee(db,'acme',email=disabled['userName'])
        invite=roster.create_personal_invite(db,'acme',disabled['id'])
        return roster.enroll_with_personal_invite(db,token=invite['token'],requested_organization_id='acme',device_id='fresh-racing-device')
    with ThreadPoolExecutor(max_workers=2) as pool:
        upgrading=pool.submit(startup)
        try:
            assert entered.wait(5)
            fresh=pool.submit(rehire).result(timeout=5)
        finally:release.set()
        upgrading.result()
    assert db.authenticate(fresh['token']) is not None
    assert db.authenticate('owned-legacy-device-secret') is None
