"""Enrollment racing provisioning offboarding must not escape the token sweep."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from gateway import employees as roster,scim_provisioning as scim
from gateway.human_access import init_human_access_schema
from test_process_collaboration import store


def account(db):
    roster.init_employee_schema(db);init_human_access_schema(db);scim.init_schema(db)
    service=scim.Provisioning(db);provider=service.create_provider('acme','Owned race fixture','admin');pid=provider['provider']['provider_id'];token=provider['token']
    service.set_enabled(pid,1,True,'admin')
    made=service.create_user(pid,token,{'schemas':[scim.CORE],'userName':'person@acme.test'})
    return service,pid,token,made


def test_enrollment_racing_scim_offboarding_never_leaves_an_active_device_token(store,monkeypatch):
    db=store.db;service,pid,token,made=account(db)
    invite=roster.create_personal_invite(db,'acme',made['id'])
    entered,release,swept=Event(),Event(),Event();execute=db._execute
    def observe(conn,sql,params=()):
        if 'INSERT INTO access_tokens' in sql:
            entered.set();assert release.wait(10)
        result=execute(conn,sql,params)
        if 'UPDATE access_tokens SET revoked_at' in sql:swept.set()
        return result
    monkeypatch.setattr(db,'_execute',observe)
    change={'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False}]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        enrollment=pool.submit(roster.enroll_with_personal_invite,db,token=invite['token'],requested_organization_id='acme',device_id='racing-device')
        try:
            assert entered.wait(5)
            offboard=pool.submit(service.patch_user,pid,token,made['id'],made['meta']['version'],change)
            # Before the fix PostgreSQL sweeps zero tokens then waits on the
            # invitation; the enroller can still commit a new active token.
            # With employee-first locking the sweep waits for enrollment.
            swept.wait(2)
        finally:release.set()
        device=enrollment.result();disabled=offboard.result()
    assert disabled['active'] is False
    assert db.authenticate(device['token']) is None
    assert roster.peek_personal_invite(db,invite['token']) is None


@pytest.mark.parametrize('kind',['invitation','link'])
def test_concurrent_invitation_or_device_link_cannot_escape_offboarding_and_rehire(store,monkeypatch,kind):
    db=store.db;service,pid,token,made=account(db)
    device_token='owned-race-device-credential'
    if kind=='link':
        db.put_token(token_id='owned-race-device',token=device_token,token_type='device',
            organization_id='acme',actor_id='unlinked@acme.test',device_id='racing-device',scopes={'evidence:write'})
    entered,release,disabled_done=Event(),Event(),Event();execute=db._execute
    sql_target='INSERT INTO gateway_personal_invites' if kind=='invitation' else 'UPDATE access_tokens SET actor_id'
    def observe(conn,sql,params=()):
        if sql_target in sql:
            entered.set();assert release.wait(10)
        return execute(conn,sql,params)
    monkeypatch.setattr(db,'_execute',observe)
    change={'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'active','value':False}]}
    def offboard():
        result=service.patch_user(pid,token,made['id'],made['meta']['version'],change)
        disabled_done.set();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        if kind=='invitation':creation=pool.submit(roster.create_personal_invite,db,'acme',made['id'])
        else:creation=pool.submit(roster.link_device,db,'acme','racing-device',made['id'],reattribute_history=False,linked_by='owned-admin')
        try:
            assert entered.wait(5);disabled=pool.submit(offboard)
            disabled_done.wait(2)
        finally:release.set()
        created=creation.result();inactive=disabled.result()
    assert inactive['active'] is False
    if kind=='link':assert db.authenticate(device_token) is None
    change['Operations'][0]['value']=True
    assert service.patch_user(pid,token,made['id'],inactive['meta']['version'],change)['active']
    if kind=='link':assert db.authenticate(device_token) is None
    else:assert roster.peek_personal_invite(db,created['token']) is None
