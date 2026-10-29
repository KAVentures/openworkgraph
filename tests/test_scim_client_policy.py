"""Owned SCIM client sequences for a deliberately opt-in compatibility scope."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from gateway import scim_provisioning as scim, employees as roster
from gateway.scim_client_policy import Policies
from gateway_identity_helpers import add_admin
from test_process_collaboration import store
from test_scim_routes import http, user


def endpoint(pid):
    return '/v1/admin/scim-providers/acme/' + pid + '/client-policy'


def enable(client, owner, pid, revision=2):
    r = client.patch(endpoint(pid), headers=owner,
        json={'revision': revision, 'enabled': True, 'confirmUnversioned': True})
    assert r.status_code == 200, r.text
    return r.json()


def patch(active=False):
    # The public Okta guide's pathless scalar-delta form, no client version.
    return {'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'value': {'active': active}}]}


def test_explicit_policy_default_off_delta_preservation_and_noop_versions(http):
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    url = made.headers['location']
    assert client.get(endpoint(pid), headers=owner).json()['unversioned_user_patch'] is False
    assert client.patch(url, headers=h, json=patch()).status_code == 428
    policy = enable(client, owner, pid)
    assert policy['revision'] == 3 and policy['scope'] == 'standalone_users_patch_only'
    changed = client.patch(url, headers=h, json=patch())
    assert changed.status_code == 200 and changed.json()['active'] is False, changed.text
    assert changed.json()['displayName'] == made.json()['displayName']
    assert changed.json()['externalId'] == made.json()['externalId']
    repeat = client.patch(url, headers=h, json=patch())
    assert repeat.json() == changed.json() and repeat.headers['etag'] == changed.headers['etag']
    with db.connect() as conn:
        rows = roster._all(db, conn, 'SELECT details_json FROM audit_log WHERE action=?', ('scim.user.unversioned_patch',))
    assert len(rows) == 2
    assert all(set(json.loads(r['details_json'])) == {'employee_id', 'policy_revision'} for r in rows)
    conditional = client.patch(url, headers={**h, 'If-Match': repeat.headers['etag']}, json=patch(True))
    assert conditional.status_code == 200 and conditional.json()['active'] is True
    assert client.patch(url, headers={**h, 'If-Match': repeat.headers['etag']}, json=patch()).status_code == 412
    off = client.patch(endpoint(pid), headers=owner, json={'revision': 3, 'enabled': False})
    assert off.status_code == 200 and off.json()['revision'] == 4
    assert client.patch(url, headers=h, json=patch(True)).status_code == 428
    current = client.get(url, headers=h)
    assert client.patch(url, headers={**h, 'If-Match': current.headers['etag']}, json=patch(True)).status_code == 200


@pytest.mark.parametrize('version,status', [('', 428), ('stale', 412), ('*', 412), ('W/"unknown"', 412)])
def test_supplied_versions_are_never_ignored_or_refreshed(http, version, status):
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    enable(client, owner, pid)
    r = client.patch(made.headers['location'], headers={**h, 'If-Match': version}, json=patch())
    assert r.status_code == status
    assert client.get(made.headers['location'], headers=h).json() == made.json()
    duplicate = list(h.items()) + [('If-Match', made.headers['etag']), ('If-Match', 'stale')]
    assert client.patch(made.headers['location'], headers=duplicate, json=patch()).status_code == 400


def test_only_standalone_user_patch_gets_missing_version_compatibility(http):
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    uid, url = made.json()['id'], made.headers['location']
    enable(client, owner, pid)
    assert client.put(url, headers=h, json=user()).status_code == 428
    assert client.delete(url, headers=h).status_code == 428
    group = client.post(base + '/Groups', headers=h,
        json={'schemas': ['urn:ietf:params:scim:schemas:core:2.0:Group'], 'displayName': 'Team'})
    assert client.patch(group.headers['location'], headers=h,
        json={'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'displayName', 'value': 'No'}]}).status_code == 428
    batch = client.post(base + '/Bulk', headers=h, json={
        'schemas': ['urn:ietf:params:scim:api:messages:2.0:BulkRequest'],
        'Operations': [{'method': 'PATCH', 'path': '/Users/' + uid, 'data': patch()}]})
    assert batch.json()['Operations'][0]['status'] == '428'
    service = client.app.state.scim_provisioning
    with pytest.raises(scim.Error) as denied:
        service.patch_user(pid, h['Authorization'][7:], uid, None, patch())
    assert denied.value.status == 428
    assert client.patch(url, headers=h, json={**patch(), 'standalone_client': True}).status_code == 400
    assert client.get(url, headers=h).json() == made.json()


@pytest.mark.parametrize('body', [
    {'revision': 2, 'enabled': True}, {'revision': 2, 'enabled': True, 'confirmUnversioned': False},
    {'revision': True, 'enabled': True, 'confirmUnversioned': True},
    {'revision': 2, 'enabled': 1, 'confirmUnversioned': True},
    {'revision': 2, 'enabled': True, 'confirmUnversioned': 'true'},
    {'revision': 2, 'enabled': True, 'confirmUnversioned': 1},
    {'revision': 2.0, 'enabled': True, 'confirmUnversioned': True},
    {'revision': 2, 'enabled': True, 'confirmUnversioned': True, 'scope': 'all'},
])
def test_explicit_policy_types_and_confirmation_are_required(http, body):
    client, db, owner, h, base, pid = http
    assert client.patch(endpoint(pid), headers=owner, json=body).status_code == 400
    state = client.get(endpoint(pid), headers=owner).json()
    assert state['revision'] == 2 and state['unversioned_user_patch'] is False


def test_admin_provider_tenant_and_stale_revision_boundaries(http):
    client, db, owner, h, base, pid = http
    viewer, _, _ = add_admin(client, owner, 'viewer@acme.test', 'viewer')
    body = {'revision': 2, 'enabled': True, 'confirmUnversioned': True}
    assert client.patch(endpoint(pid), headers=viewer, json=body).status_code == 403
    for wrong in ({}, h):
        assert client.get(endpoint(pid), headers=wrong).status_code == 401
        assert client.patch(endpoint(pid), headers=wrong, json=body).status_code == 401
    assert client.patch(endpoint(pid).replace('/acme/', '/other/'), headers=owner, json=body).status_code == 404
    enable(client, owner, pid)
    assert client.patch(endpoint(pid), headers=owner, json=body).status_code == 412
    assert client.post('/v1/admin/scim-providers/acme/' + pid + '/revoke', headers=owner, json={'revision': 3}).status_code == 200
    assert client.patch(endpoint(pid), headers=owner, json={'revision': 4, 'enabled': False}).status_code == 409
    assert client.patch(base + '/Users/unknown', headers=h, json=patch()).status_code == 401


def test_policy_audit_failure_rolls_back_revision_and_use_failure_rolls_back_offboarding(http, monkeypatch):
    client, db, owner, h, base, pid = http
    service = client.app.state.scim_provisioning
    audit = service.audit
    def reject(*args):
        raise RuntimeError('Owned audit failure')
    monkeypatch.setattr(service, 'audit', reject)
    with pytest.raises(RuntimeError):
        Policies(service).set('acme', pid, {'revision': 2, 'enabled': True, 'confirmUnversioned': True}, 'fixture')
    assert client.get(endpoint(pid), headers=owner).json()['revision'] == 2
    monkeypatch.setattr(service, 'audit', audit)
    made = client.post(base + '/Users', headers=h, json=user())
    uid = made.json()['id']
    invite = roster.create_personal_invite(db, 'acme', uid)
    device = roster.enroll_with_personal_invite(db, token=invite['token'], requested_organization_id='acme', device_id='owned-device')
    enable(client, owner, pid)
    def reject_use(conn, organization, principal, action, details):
        if action == 'scim.user.unversioned_patch':
            raise RuntimeError('Owned use audit failure')
        return audit(conn, organization, principal, action, details)
    monkeypatch.setattr(service, 'audit', reject_use)
    with pytest.raises(RuntimeError):
        client.patch(made.headers['location'], headers=h, json=patch())
    assert client.get(made.headers['location'], headers=h).json() == made.json()
    assert db.authenticate(device['token']) is not None
    assert roster.peek_personal_invite(db, invite['token']) is not None


def test_noop_policy_change_does_not_write_or_advance_provider_revision(http):
    client, db, owner, h, base, pid = http
    enable(client, owner, pid)
    before = client.get(endpoint(pid), headers=owner).json()
    same = client.patch(endpoint(pid), headers=owner,
        json={'revision': 3, 'enabled': True, 'confirmUnversioned': True})
    assert same.json() == before
    with db.connect() as conn:
        assert roster._one(db, conn, 'SELECT count(*) AS n FROM audit_log WHERE action=?', ('scim.provider.client_policy',))['n'] == 1


@pytest.mark.parametrize('change,status', [('policy', 428), ('revoke', 401), ('disable', 401)])
def test_current_authority_rechecked_after_http_authentication(http, monkeypatch, change, status):
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    enable(client, owner, pid)
    service = client.app.state.scim_provisioning
    original = service.patch_user
    def changed(*args, **kwargs):
        if change == 'policy':
            Policies(service).set('acme', pid, {'revision': 3, 'enabled': False}, 'fixture')
        elif change == 'revoke':
            service.revoke(pid, 3, 'fixture')
        else:
            service.set_enabled(pid, 3, False, 'fixture')
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'patch_user', changed)
    r = client.patch(made.headers['location'], headers=h, json=patch())
    assert r.status_code == status
    assert roster.get_employee(db, 'acme', made.json()['id'])['status'] == 'active'


def test_current_delta_preserves_unmentioned_human_fields_and_bad_later_operation_is_atomic(http):
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    enable(client, owner, pid)
    roster.upsert_employee(db, 'acme', email=user()['userName'], display_name='Human revision')
    bad = patch()
    bad['Operations'].append({'op': 'replace', 'path': 'userName', 'value': 'other@acme.test'})
    assert client.patch(made.headers['location'], headers=h, json=bad).status_code == 400
    current = client.get(made.headers['location'], headers=h)
    assert current.json()['active'] is True and current.json()['displayName'] == 'Human revision'
    r = client.patch(made.headers['location'], headers=h, json=patch())
    assert r.status_code == 200 and r.json()['displayName'] == 'Human revision' and r.json()['active'] is False
    with db.connect() as conn:
        assert roster._one(db, conn, 'SELECT count(*) AS n FROM audit_log WHERE action=?', ('scim.user.unversioned_patch',))['n'] == 1


def test_concurrent_policy_writes_have_one_current_revision_winner(http):
    from threading import Barrier
    client, db, owner, h, base, pid = http
    policy = Policies(client.app.state.scim_provisioning)
    gate = Barrier(2)
    def set_policy():
        gate.wait(timeout=10)
        try:
            policy.set('acme', pid, {'revision': 2, 'enabled': True, 'confirmUnversioned': True}, 'fixture')
            return 200
        except scim.Error as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(set_policy) for _ in range(2)]
        assert sorted(f.result(timeout=20) for f in futures) == [200, 412]
    assert policy.get('acme', pid)['revision'] == 3


@pytest.mark.parametrize('first', ['patch', 'disable'])
def test_policy_disable_and_patch_serialize_on_same_provider(http, monkeypatch, first):
    from threading import Event
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    enable(client, owner, pid)
    service = client.app.state.scim_provisioning
    policies = Policies(service)
    entered, release, attempted = Event(), Event(), Event()
    audit = service.audit
    def hold(conn, organization, principal, action, details):
        target = 'scim.user.unversioned_patch' if first == 'patch' else 'scim.provider.client_policy'
        if action == target:
            entered.set()
            assert release.wait(timeout=10)
        return audit(conn, organization, principal, action, details)
    monkeypatch.setattr(service, 'audit', hold)
    def update():
        if first == 'disable':
            attempted.set()
        try:
            service.patch_user(pid, h['Authorization'][7:], made.json()['id'], None, patch(), standalone_client=True)
            return 200
        except scim.Error as error:
            return error.status
    def disable():
        if first == 'patch':
            attempted.set()
        policies.set('acme', pid, {'revision': 3, 'enabled': False}, 'fixture')
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(update if first == 'patch' else disable)
        try:
            assert entered.wait(timeout=5)
            b = pool.submit(disable if first == 'patch' else update)
            assert attempted.wait(timeout=5)
        finally:
            release.set()
        first_result, second_result = a.result(timeout=20), b.result(timeout=20)
    assert (first_result if first == 'patch' else second_result) == (200 if first == 'patch' else 428)
    assert policies.get('acme', pid)['unversioned_user_patch'] is False
    assert client.patch(made.headers['location'], headers=h, json=patch(True)).status_code == 428


def test_additive_policy_upgrade_defaults_existing_provider_to_strict_without_identity_changes(http):
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    before = client.app.state.scim_provisioning.list_providers('acme')
    with db.connect() as conn:
        db._execute(conn, 'DROP TABLE gateway_scim_client_policies')
    scim.init_schema(db)
    scim.init_schema(db)
    assert client.app.state.scim_provisioning.list_providers('acme') == before
    assert client.get(endpoint(pid), headers=owner).json()['unversioned_user_patch'] is False
    assert client.patch(made.headers['location'], headers=h, json=patch()).status_code == 428
    assert client.get(made.headers['location'], headers=h).json() == made.json()


def test_current_sqlite_backup_preserves_policy_and_provider_revision(tmp_path):
    import sqlite3
    from test_scim_upgrade_restore import database
    db = database(tmp_path / 'source.db')
    scim.init_schema(db)
    service = scim.Provisioning(db)
    provider = service.create_provider('acme', 'Backup fixture', 'fixture')
    pid, token = provider['provider']['provider_id'], provider['token']
    service.set_enabled(pid, 1, True, 'fixture')
    made = service.create_user(pid, token, user())
    before = Policies(service).set('acme', pid, {'revision': 2, 'enabled': True, 'confirmUnversioned': True}, 'fixture')
    path = tmp_path / 'restore.db'
    with db.connect() as source, sqlite3.connect(path) as target:
        source.backup(target)
    restored = database(path)
    scim.init_schema(restored)
    copy = scim.Provisioning(restored)
    assert Policies(copy).get('acme', pid) == before
    assert copy.get_user(pid, token, made['id']) == made
    # The previous routing/service-call contract still refuses a missing version.
    with pytest.raises(scim.Error) as denied:
        copy.patch_user(pid, token, made['id'], None, patch())
    assert denied.value.status == 428
    assert copy.patch_user(pid, token, made['id'], None, patch(), standalone_client=True)['active'] is False


def test_preparing_policy_for_disabled_provider_never_enables_source_access(http):
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    service = client.app.state.scim_provisioning
    service.set_enabled(pid, 2, False, 'fixture')
    policy = enable(client, owner, pid, revision=3)
    assert policy['provider_enabled'] is False and policy['revision'] == 4
    assert client.patch(made.headers['location'], headers=h, json=patch()).status_code == 401
    service.set_enabled(pid, 4, True, 'fixture')
    assert client.patch(made.headers['location'], headers=h, json=patch()).status_code == 200


def test_policy_json_limits_and_query_options_are_explicit(http):
    client, db, owner, h, base, pid = http
    for raw, status in [(b'{"enabled":true,"enabled":false}', 400),
                        (b'{"revision":2,"Revision":2}', 400), (b'{"enabled":NaN}', 400),
                        (b'[]', 400), (b'x' * 65537, 413)]:
        r = client.patch(endpoint(pid), headers={**owner, 'Content-Type': 'application/json'}, content=raw)
        assert r.status_code == status and r.json()['schemas'] == [scim.ERROR], r.text
    assert client.get(endpoint(pid) + '?scope=all', headers=owner).status_code == 400
    assert client.patch(endpoint(pid) + '?scope=all', headers=owner,
        json={'revision': 2, 'enabled': True, 'confirmUnversioned': True}).status_code == 400
    assert client.get(endpoint(pid), headers=owner).json()['unversioned_user_patch'] is False
