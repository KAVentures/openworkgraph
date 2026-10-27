"""Owned additive-schema upgrade and SQLite backup restore contracts."""
import sqlite3
from gateway.db import GatewayDB
from gateway import employees as roster,scim_provisioning as scim
from gateway.human_access import init_human_access_schema,actor_teams


def database(path):
    db=GatewayDB('sqlite:///'+str(path));db.init();roster.init_employee_schema(db);init_human_access_schema(db)
    return db


def test_additive_schema_upgrade_is_idempotent_and_does_not_adopt_or_change_existing_identities(tmp_path):
    db=database(tmp_path/'legacy.db')
    employee,_=roster.upsert_employee(db,'acme',email='existing@acme.test',display_name='Existing identity',teams=['manual'])
    scim.init_schema(db);scim.init_schema(db)
    service=scim.Provisioning(db);provider=service.create_provider('acme','New IdP','fixture-admin');pid=provider['provider']['provider_id']
    service.set_enabled(pid,1,True,'fixture-admin')
    assert service.list_users(pid,provider['token'])['totalResults']==0
    assert roster.get_employee(db,'acme',employee['employee_id'])==employee
    assert actor_teams(db,'acme',employee['actor_id'])=={'manual'}


def test_current_backup_restore_preserves_link_tombstone_offboarding_epoch_and_credential_revocation(tmp_path):
    db=database(tmp_path/'source.db');scim.init_schema(db);service=scim.Provisioning(db)
    provider=service.create_provider('acme','Owned backup fixture','fixture-admin');pid=provider['provider']['provider_id'];token=provider['token']
    service.set_enabled(pid,1,True,'fixture-admin')
    made=service.create_user(pid,token,{'schemas':[scim.CORE],'userName':'person@acme.test','externalId':'native-person'})
    invitation=roster.create_personal_invite(db,'acme',made['id'],max_devices=2)
    device=roster.enroll_with_personal_invite(db,token=invitation['token'],requested_organization_id='acme',device_id='owned-backup-device')
    service.delete_user(pid,token,made['id'],made['meta']['version']);service.revoke(pid,2,'fixture-admin')
    path=tmp_path/'restored.db'
    with db.connect() as original,sqlite3.connect(path) as target:original.backup(target)
    restored=database(path);scim.init_schema(restored);copy=scim.Provisioning(restored)
    assert copy.list_providers('acme')==service.list_providers('acme')
    assert roster.get_employee(restored,'acme',made['id'])['status']=='offboarded'
    assert restored.authenticate(device['token']) is None
    assert roster.peek_personal_invite(restored,invitation['token']) is None
    try:copy.list_users(pid,token)
    except scim.Error as error:assert error.status==401
    else:raise AssertionError('Restored revoked provider credential was accepted')
    with db.connect() as a,restored.connect() as b:
        for table in ['gateway_scim_providers','gateway_scim_users','gateway_employee_access_epochs','gateway_employees','gateway_actor_teams','access_tokens','gateway_personal_invites','audit_log']:
            left=[tuple(row) for row in a.execute('SELECT * FROM '+table+' ORDER BY 1')]
            right=[tuple(row) for row in b.execute('SELECT * FROM '+table+' ORDER BY 1')]
            assert left==right,table
