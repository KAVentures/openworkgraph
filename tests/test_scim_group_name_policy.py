"""Provider-local Group constraints, explicit admin policy and transaction fences."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from gateway import scim_provisioning as scim, employees as roster
from gateway.scim_group_name_policy import Policies
from gateway.scim_groups import Groups
from test_process_collaboration import store
from test_scim_groups import group
from test_scim_routes import http, user


def endpoint(pid, organization='acme'):
    return '/v1/admin/scim-providers/' + organization + '/' + pid + '/group-name-policy'


def set_policy(ctx, enabled=True, revision=None):
    client, _, owner, _, _, pid = ctx
    if revision is None:
        revision = client.get(endpoint(pid), headers=owner).json()['revision']
    return client.patch(endpoint(pid), headers=owner, json={'revision': revision, 'enabled': enabled})


def test_existing_collisions_need_explicit_resolution_and_old_defaults_stay_unchanged(http):
    client, db, owner, h, base, pid = http
    a = client.post(base + '/Groups', headers=h, json=group())
    b = client.post(base + '/Groups', headers=h, json=group(displayName='  aNALYSTs  '))
    assert a.status_code == b.status_code == 201
    initial = client.get(endpoint(pid), headers=owner)
    assert initial.headers['cache-control'] == 'no-store'
    assert initial.json()['enabled'] is False
    assert initial.json()['active_groups'] == 2 and initial.json()['colliding_groups'] == 1
    assert set_policy(http).status_code == 409
    assert client.get(endpoint(pid), headers=owner).json() == initial.json()
    changed = client.put(b.headers['location'], headers={**h, 'If-Match': b.headers['etag']},
                         json=group(displayName='Operations'))
    assert changed.status_code == 200
    policy = set_policy(http)
    assert policy.status_code == 200 and policy.json()['enabled'] is True
    assert policy.json()['revision'] == 3 and policy.json()['colliding_groups'] == 0
    assert client.get(a.headers['location'], headers=h).json() == a.json()
    duplicate = client.post(base + '/Groups', headers=h, json=group(displayName='ANALYSTS'))
    assert duplicate.status_code == 409 and duplicate.json()['scimType'] == 'uniqueness'
    assert 'ANALYSTS' not in duplicate.text
    assert set_policy(http, False).status_code == 200
    assert client.post(base + '/Groups', headers=h, json=group()).status_code == 201


@pytest.mark.parametrize('method', ['PUT', 'PATCH'])
def test_unicode_rename_refusal_is_atomic_and_tombstone_names_are_reusable(http, method):
    client, db, owner, h, base, pid = http
    assert set_policy(http).status_code == 200
    a = client.post(base + '/Groups', headers=h, json=group(displayName='Straße'))
    b = client.post(base + '/Groups', headers=h, json=group(displayName='Other'))
    body = (group(displayName='STRASSE') if method == 'PUT' else
        {'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'displayName', 'value': 'STRASSE'}]})
    denied = client.request(method, b.headers['location'], headers={**h, 'If-Match': b.headers['etag']}, json=body)
    assert denied.status_code == 409
    assert client.get(b.headers['location'], headers=h).json() == b.json()
    assert client.delete(a.headers['location'], headers={**h, 'If-Match': a.headers['etag']}).status_code == 204
    fresh = client.post(base + '/Groups', headers=h, json=group(displayName='STRASSE'))
    assert fresh.status_code == 201 and fresh.json()['id'] != a.json()['id']
    same = client.put(fresh.headers['location'], headers={**h, 'If-Match': fresh.headers['etag']},
                      json=group(displayName='STRASSE'))
    assert same.json() == fresh.json() and same.headers['etag'] == fresh.headers['etag']


def test_group_metadata_never_grants_employee_access_and_policy_does_not_change_user_versions(http):
    client, db, owner, h, base, pid = http
    person = client.post(base + '/Users', headers=h, json=user())
    made = client.post(base + '/Groups', headers=h, json=group(members=[{'value': person.json()['id']}]))
    before = client.get(person.headers['location'], headers=h)
    assert set_policy(http).status_code == 200
    assert client.get(person.headers['location'], headers=h).json() == before.json()
    assert client.get('/v1/human/processes', headers=h).status_code == 401
    roster.offboard_employee(db, 'acme', person.json()['id'])
    assert client.get(made.headers['location'], headers=h).json()['members'] == []
    assert client.get(endpoint(pid), headers=owner).json()['enabled'] is True


@pytest.mark.parametrize('body', [
    {}, {'revision': 2, 'enabled': 1}, {'revision': True, 'enabled': True},
    {'revision': 2.0, 'enabled': True}, {'revision': 2, 'enabled': 'true'},
    {'revision': 2, 'enabled': True, 'organization': 'other'},
])
def test_policy_requires_explicit_bounded_fields(http, body):
    client, _, owner, _, _, pid = http
    assert client.patch(endpoint(pid), headers=owner, json=body).status_code == 400
    assert client.get(endpoint(pid), headers=owner).json()['revision'] == 2


def test_policy_admin_scope_noop_stale_versions_and_provider_revocation(http):
    client, db, owner, h, base, pid = http
    assert client.get(endpoint(pid), headers=h).status_code == 401
    assert client.patch(endpoint(pid), headers=h, json={'revision': 2, 'enabled': True}).status_code == 401
    assert client.get(endpoint(pid, 'foreign'), headers=owner).status_code == 404
    assert client.get(endpoint(pid), headers=owner, params={'count': '1'}).status_code == 400
    assert set_policy(http, revision=1).status_code == 412
    before = set_policy(http).json()
    with db.connect() as conn:
        saved = roster._one(db, conn, 'SELECT * FROM gateway_scim_group_name_policies WHERE provider_id=?', (pid,))
    assert set_policy(http).json() == before
    with db.connect() as conn:
        assert roster._one(db, conn, 'SELECT * FROM gateway_scim_group_name_policies WHERE provider_id=?', (pid,)) == saved
        audits = roster._all(db, conn, 'SELECT details_json FROM audit_log WHERE action=?', ('scim.provider.group_name_policy',))
    assert [json.loads(a['details_json']) for a in audits] == [{'provider_id': pid, 'unique_group_names': True}]
    assert client.post('/v1/admin/scim-providers/acme/' + pid + '/revoke', headers=owner,
                       json={'revision': before['revision']}).status_code == 200
    assert set_policy(http, False).status_code == 409
    assert client.post(base + '/Groups', headers=h, json=group()).status_code == 401


def test_other_providers_have_independent_names_and_policy(http):
    client, db, owner, h, base, pid = http
    service = client.app.state.scim_provisioning
    assert set_policy(http).status_code == 200
    assert client.post(base + '/Groups', headers=h, json=group()).status_code == 201
    other = service.create_provider('acme', 'Other directory', 'fixture')
    oid, token = other['provider']['provider_id'], other['token']
    service.set_enabled(oid, 1, True, 'fixture')
    Policies(service).set('acme', oid, {'revision': 2, 'enabled': True}, 'fixture')
    assert Groups(service).create(oid, token, group())['displayName'] == 'Analysts'
    assert client.get(endpoint(oid, 'foreign'), headers=owner).status_code == 404


def test_bulk_conflict_does_not_undo_earlier_success_or_commit_later_operations(http):
    client, _, _, h, base, _ = http
    assert set_policy(http).status_code == 200
    batch = {'schemas': ['urn:ietf:params:scim:api:messages:2.0:BulkRequest'], 'failOnErrors': 1,
             'Operations': [{'method': 'POST', 'path': '/Groups', 'bulkId': str(i), 'data': body}
                            for i, body in enumerate([group(), group(displayName='ANALYSTS'), group(displayName='Later')])]}
    response = client.post(base + '/Bulk', headers=h, json=batch)
    statuses = [r['status'] for r in response.json()['Operations']]
    assert statuses[:2] == ['201', '409']
    assert client.get(base + '/Groups', headers=h).json()['totalResults'] == 1


def test_audit_failure_rolls_back_policy_and_provider_revision(http, monkeypatch):
    client, _, owner, _, _, pid = http
    service = client.app.state.scim_provisioning
    before = client.get(endpoint(pid), headers=owner).json()
    def reject(*args):
        raise RuntimeError('owned audit failure')
    monkeypatch.setattr(service, 'audit', reject)
    with pytest.raises(RuntimeError):
        Policies(service).set('acme', pid, {'revision': 2, 'enabled': True}, 'fixture')
    assert client.get(endpoint(pid), headers=owner).json() == before


def test_client_version_policy_and_group_constraint_remain_independent(http):
    client, _, owner, h, base, pid = http
    version_url = '/v1/admin/scim-providers/acme/' + pid + '/client-policy'
    assert client.patch(version_url, headers=owner, json={
        'revision': 2, 'enabled': True, 'confirmUnversioned': True}).status_code == 200
    assert set_policy(http).status_code == 200
    assert client.get(version_url, headers=owner).json()['unversioned_user_patch'] is True
    made = client.post(base + '/Groups', headers=h, json=group())
    assert client.patch(made.headers['location'], headers=h, json={
        'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'displayName', 'value': 'Other'}]}).status_code == 428
    assert client.put(made.headers['location'], headers={**h, 'If-Match': 'stale'}, json=group()).status_code == 412


def test_corrupt_restored_policy_is_not_silently_treated_as_disabled(tmp_path):
    from test_scim_upgrade_restore import database
    db = database(tmp_path / 'corrupt.db');scim.init_schema(db);service = scim.Provisioning(db)
    provider = service.create_provider('acme', 'Owned restore', 'fixture')
    pid, token = provider['provider']['provider_id'], provider['token']
    service.set_enabled(pid, 1, True, 'fixture')
    with db.connect() as conn:
        conn.execute('DROP TABLE gateway_scim_group_name_policies')
        conn.execute('CREATE TABLE gateway_scim_group_name_policies(provider_id TEXT PRIMARY KEY,enabled TEXT,updated_at TEXT)')
        conn.execute('INSERT INTO gateway_scim_group_name_policies VALUES (?,?,?)', (pid, 'corrupt-private-value', ''))
    for operation in [lambda: Policies(service).get('acme', pid),
                      lambda: Groups(service).create(pid, token, group())]:
        with pytest.raises(scim.Error) as caught:
            operation()
        assert caught.value.status == 503
        assert 'corrupt-private-value' not in caught.value.detail


@pytest.mark.parametrize('enable_race', [False, True])
def test_provider_lock_serializes_duplicate_create_and_enable_races(http, enable_race):
    client, db, owner, h, base, pid = http
    service = client.app.state.scim_provisioning
    token = h['Authorization'][7:]
    if enable_race:
        assert client.post(base + '/Groups', headers=h, json=group()).status_code == 201
    else:
        assert set_policy(http).status_code == 200
    gate = Barrier(2)
    def run(index):
        gate.wait(timeout=10)
        try:
            if enable_race and index == 0:
                return Policies(scim.Provisioning(db)).set('acme', pid, {'revision': 2, 'enabled': True}, 'fixture')
            return Groups(scim.Provisioning(db)).create(pid, token, group())
        except scim.Error as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [0, 1]))
    assert sum(isinstance(x, dict) for x in results) == 1
    assert results.count(409) == 1
    final = client.get(endpoint(pid), headers=owner).json()
    assert final['colliding_groups'] == (0 if final['enabled'] else 1)
    assert final['active_groups'] == (1 if final['enabled'] else 2)


def test_native_backup_and_old_additive_schema_restore(tmp_path):
    from test_scim_upgrade_restore import database
    db = database(tmp_path / 'source.db');scim.init_schema(db);service = scim.Provisioning(db)
    provider = service.create_provider('acme', 'Backup fixture', 'fixture')
    pid, token = provider['provider']['provider_id'], provider['token']
    service.set_enabled(pid, 1, True, 'fixture')
    Policies(service).set('acme', pid, {'revision': 2, 'enabled': True}, 'fixture')
    made = Groups(service).create(pid, token, group())
    backup = tmp_path / 'backup.db'
    with db.connect() as source, sqlite3.connect(backup) as target:
        source.backup(target)
    restored = database(backup);scim.init_schema(restored);copy = scim.Provisioning(restored)
    assert Policies(copy).get('acme', pid) == Policies(service).get('acme', pid)
    assert Groups(copy).get(pid, token, made['id']) == made
    with pytest.raises(scim.Error) as error:
        Groups(copy).create(pid, token, group())
    assert error.value.status == 409
    with restored.connect() as conn:
        conn.execute('DROP TABLE gateway_scim_group_name_policies')
    scim.init_schema(restored)
    assert Policies(copy).get('acme', pid)['enabled'] is False
    assert Groups(copy).get(pid, token, made['id']) == made
