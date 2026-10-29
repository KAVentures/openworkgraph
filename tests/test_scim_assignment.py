"""Explicit admin assignment never silently adopts or rewrites roster identity."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from gateway import employees as roster, scim_provisioning as scim
from gateway.scim_assignment import Assignments
from gateway_identity_helpers import add_admin
from test_process_collaboration import store
from test_scim_routes import http, user


def manual(db, email='manual@acme.test', organization='acme'):
    return roster.upsert_employee(db, organization, email=email, display_name='Manual worker')[0]


def route(base, employee):
    return '/v1/admin/scim-providers/' + employee['organization_id'] + '/' + base.rsplit('/', 1)[-1] + '/assignments/' + employee['employee_id']


def consent(preview, **extra):
    return {'revision': preview['provider_revision'], 'employeeVersion': preview['employee_version'],
            'confirmAssignment': True, **extra}


def test_assignment_preserves_employee_and_automatic_matching_refusal(http):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    uid = employee['employee_id']
    before = roster.get_employee(db, 'acme', uid)
    assert client.post(base + '/Users', headers=h, json={**user(), 'userName': employee['email']}).status_code == 409
    url = route(base, employee)
    preview = client.get(url, headers=owner)
    assert preview.status_code == 200 and preview.headers['cache-control'] == 'no-store'
    value = preview.json()
    assert value['employee']['employee_id'] == uid and value['assigns_provider_lifecycle_authority'] is True
    assert 'sso_subject' not in preview.text and len(value['employee_version']) == 64
    made = client.post(url, headers=owner, json=consent(value, externalId='native-manual'))
    assert made.status_code == 201, made.text
    assert made.json()['id'] == uid and made.json()['userName'] == employee['email']
    assert made.json()['externalId'] == 'native-manual'
    assert made.headers['etag'] == made.json()['meta']['version']
    assert roster.get_employee(db, 'acme', uid) == before
    assert client.get(base + '/Users/' + uid, headers=h).json() == made.json()
    assert client.post(base + '/Users', headers=h, json={**user(), 'userName': employee['email']}).status_code == 409
    assert client.post(url, headers=owner, json=consent(value)).status_code == 409


@pytest.mark.parametrize('change', ['employee', 'epoch', 'provider', 'revoke'])
def test_assignment_refuses_stale_preview_and_changed_authority(http, change):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    url = route(base, employee)
    value = client.get(url, headers=owner).json()
    if change == 'employee':
        roster.upsert_employee(db, 'acme', email=employee['email'], display_name='Changed')
    elif change == 'epoch':
        roster.offboard_employee(db, 'acme', employee['employee_id'])
        roster.upsert_employee(db, 'acme', email=employee['email'], display_name='Manual worker')
    elif change == 'provider':
        assert client.patch('/v1/admin/scim-providers/acme/' + pid, headers=owner,
                            json={'revision': 2, 'enabled': False}).status_code == 200
    else:
        assert client.post('/v1/admin/scim-providers/acme/' + pid + '/revoke', headers=owner,
                           json={'revision': 2}).status_code == 200
    r = client.post(url, headers=owner, json=consent(value))
    assert r.status_code == (409 if change == 'revoke' else 412), r.text
    assert client.get(base + '/Users/' + employee['employee_id'], headers=h).status_code in (401, 404)


@pytest.mark.parametrize('options', [
    {'confirmAssignment': False}, {'confirmAssignment': 1}, {'confirmAssignment': 'true'},
    {'revision': True}, {'revision': 0}, {'revision': '2'}, {'employeeVersion': 'stale'}, {'employeeVersion': 'é' * 64},
    {'externalId': []}, {'externalId': 'x' * 201}, {'userName': 'different@acme.test'},
    {'active': True}, {'teams': ['admin']}, {'sso_subject': 'unknown'},
])
def test_explicit_confirmation_bounds_and_no_arbitrary_identity_attributes(http, options):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    url = route(base, employee)
    value = client.get(url, headers=owner).json()
    r = client.post(url, headers=owner, json=consent(value, **options))
    assert r.status_code == 400 and r.json()['schemas'] == [scim.ERROR], r.text
    assert client.get(base + '/Users/' + employee['employee_id'], headers=h).status_code == 404


def test_provider_viewer_and_other_organization_cannot_assign(http):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    url = route(base, employee)
    value = client.get(url, headers=owner).json()
    viewer, _, _ = add_admin(client, owner, 'viewer@acme.test', 'viewer')
    for wrong in ({}, h):
        assert client.get(url, headers=wrong).status_code == 401
        assert client.post(url, headers=wrong, json=consent(value)).status_code == 401
    assert client.post(url, headers=viewer, json=consent(value)).status_code == 403
    other = manual(db, organization='other')
    assert client.get(route(base, other), headers=owner).status_code == 404
    assert client.post(route(base, other), headers=owner, json=consent(value)).status_code == 404
    assert client.get(url + '?match=email', headers=owner).status_code == 400


def test_assignment_audit_failure_rolls_back_linkage_and_never_changes_roster(http, monkeypatch):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    service = client.app.state.scim_provisioning
    assignments = Assignments(service)
    value = assignments.preview('acme', pid, employee['employee_id'])
    def reject(*args):
        raise RuntimeError('Owned audit failure')
    monkeypatch.setattr(service, 'audit', reject)
    with pytest.raises(RuntimeError, match='Owned audit failure'):
        assignments.assign('acme', pid, employee['employee_id'], consent(value), 'owned-admin')
    assert roster.get_employee(db, 'acme', employee['employee_id']) == employee
    assert client.get(base + '/Users/' + employee['employee_id'], headers=h).status_code == 404


def test_two_providers_race_has_one_winner_and_no_transfer(http):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    assignments = Assignments(client.app.state.scim_provisioning)
    other = client.post('/v1/admin/scim-providers/acme', headers=owner, json={'label': 'Other'}).json()['provider']['provider_id']
    previews = [assignments.preview('acme', p, employee['employee_id']) for p in (pid, other)]
    def assign(item):
        try:
            assignments.assign('acme', item['provider_id'], employee['employee_id'], consent(item), 'owned-admin')
            return 201
        except scim.Error as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(assign, previews)) == [201, 409]
    with db.connect() as conn:
        rows = roster._all(db, conn, 'SELECT provider_id FROM gateway_scim_users WHERE employee_id=?', (employee['employee_id'],))
    assert len(rows) == 1


def test_assignment_preserves_sso_manual_teams_devices_invites_and_epoch_exactly(http):
    from gateway.human_access import actor_teams
    client, db, owner, h, base, pid = http
    employee, _ = roster.upsert_employee(db, 'acme', email='manual@acme.test', display_name='Worker', teams=['ops'])
    uid = employee['employee_id']
    with db.connect() as conn:
        db._execute(conn, 'UPDATE gateway_employees SET sso_subject=? WHERE employee_id=?', ('owned-secret-subject', uid))
    invite = roster.create_personal_invite(db, 'acme', uid, max_devices=2)
    device = roster.enroll_with_personal_invite(db, token=invite['token'], requested_organization_id='acme', device_id='owned-device')
    def snapshot():
        with db.connect() as conn:
            return {table: sorted(json.dumps(dict(row), sort_keys=True) for row in
                roster._all(db, conn, 'SELECT * FROM ' + table, ())) for table in [
                    'gateway_employees', 'gateway_employee_access_epochs', 'gateway_personal_invites',
                    'gateway_device_identity', 'gateway_actor_teams', 'access_tokens']}
    before = snapshot()
    url = route(base, employee)
    preview = client.get(url, headers=owner)
    assert 'owned-secret-subject' not in preview.text and device['token'] not in preview.text
    assert preview.json()['employee']['sso_linked'] is True
    made = client.post(url, headers=owner, json=consent(preview.json(), externalId='private-external'))
    assert made.status_code == 201, made.text
    assert snapshot() == before
    assert actor_teams(db, 'acme', employee['actor_id']) == {'ops'}
    assert db.authenticate(device['token']) is not None
    assert roster.peek_personal_invite(db, invite['token']) is not None
    with db.connect() as conn:
        audit = roster._all(db, conn, 'SELECT * FROM audit_log WHERE action=?', ('scim.user.assigned',))
    assert len(audit) == 1 and 'private-external' not in json.dumps(audit)


def test_disabled_provider_can_be_prepared_without_enabling_or_rehiring(http):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    roster.offboard_employee(db, 'acme', employee['employee_id'])
    paused = client.patch('/v1/admin/scim-providers/acme/' + pid, headers=owner, json={'revision': 2, 'enabled': False})
    assert paused.status_code == 200
    url = route(base, employee)
    preview = client.get(url, headers=owner).json()
    assert preview['provider_enabled'] is False and preview['employee']['status'] == 'offboarded'
    made = client.post(url, headers=owner, json=consent(preview))
    assert made.status_code == 201 and made.json()['active'] is False
    assert roster.get_employee(db, 'acme', employee['employee_id'])['status'] == 'offboarded'
    assert client.get(base + '/Users/' + employee['employee_id'], headers=h).status_code == 401
    enabled = client.patch('/v1/admin/scim-providers/acme/' + pid, headers=owner, json={'revision': 3, 'enabled': True})
    assert enabled.status_code == 200
    assert client.get(base + '/Users/' + employee['employee_id'], headers=h).json()['active'] is False


def test_external_id_conflict_and_tombstones_cannot_be_assigned(http):
    client, db, owner, h, base, pid = http
    created = client.post(base + '/Users', headers=h, json=user())
    assert created.status_code == 201
    employee = manual(db)
    url = route(base, employee)
    preview = client.get(url, headers=owner).json()
    conflict = client.post(url, headers=owner, json=consent(preview, externalId=user()['externalId']))
    assert conflict.status_code == 409
    assert client.get(url, headers=owner).json() == preview
    assert client.get(base + '/Users/' + employee['employee_id'], headers=h).status_code == 404
    assert client.delete(created.headers['location'], headers={**h, 'If-Match': created.headers['etag']}).status_code == 204
    tombstone = {**employee, 'employee_id': created.json()['id']}
    assert client.get(route(base, tombstone), headers=owner).status_code == 409
    other = client.post('/v1/admin/scim-providers/acme', headers=owner, json={'label': 'Other'}).json()['provider']['provider_id']
    assert client.get(route('/scim/v2/' + other, tombstone), headers=owner).status_code == 409


def test_epoch_invalidates_preview_even_if_roster_fields_and_clock_repeat(http):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    uid = employee['employee_id']
    url = route(base, employee)
    before = client.get(url, headers=owner).json()
    with db.connect() as conn:
        original = roster._one(db, conn, 'SELECT * FROM gateway_employees WHERE employee_id=?', (uid,))
    roster.offboard_employee(db, 'acme', uid)
    roster.upsert_employee(db, 'acme', email=employee['email'], display_name=employee['display_name'])
    with db.connect() as conn:
        db._execute(conn, 'UPDATE gateway_employees SET updated_at=? WHERE employee_id=?', (original['updated_at'], uid))
        current = roster._one(db, conn, 'SELECT * FROM gateway_employees WHERE employee_id=?', (uid,))
    assert current == original
    assert client.get(url, headers=owner).json()['employee_version'] != before['employee_version']
    assert client.post(url, headers=owner, json=consent(before)).status_code == 412


def test_version_cannot_be_used_for_another_provider_and_lost_ack_requires_reconciliation(http, monkeypatch):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    preview = client.get(route(base, employee), headers=owner).json()
    other = client.post('/v1/admin/scim-providers/acme', headers=owner, json={'label': 'Other'}).json()['provider']['provider_id']
    assert client.post(route('/scim/v2/' + other, employee), headers=owner,
                       json={**consent(preview), 'revision': 1}).status_code == 412
    original = Assignments.assign
    def lost_ack(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise scim.Error(503, 'Owned lost acknowledgement')
    monkeypatch.setattr(Assignments, 'assign', lost_ack)
    assert client.post(route(base, employee), headers=owner, json=consent(preview)).status_code == 503
    monkeypatch.setattr(Assignments, 'assign', original)
    assert client.post(route(base, employee), headers=owner, json=consent(preview)).status_code == 409
    current = client.get(base + '/Users/' + employee['employee_id'], headers=h)
    assert current.status_code == 200 and current.json()['id'] == employee['employee_id']
    with db.connect() as conn:
        assert roster._one(db, conn, 'SELECT count(*) AS n FROM audit_log WHERE action=?', ('scim.user.assigned',))['n'] == 1


def test_assignment_input_body_and_missing_confirmation_do_not_write(http):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    url = route(base, employee)
    preview = client.get(url, headers=owner).json()
    for raw, status in [(b'{"revision":2,"revision":2}', 400), (b'[]', 400),
                        (b'{"externalId":NaN}', 400), (b'x' * 65537, 413)]:
        assert client.post(url, headers={**owner, 'Content-Type': 'application/json'}, content=raw).status_code == status
        assert client.post(url, headers=h, content=raw).status_code == 401
    for key in ('confirmAssignment', 'employeeVersion', 'revision'):
        body = consent(preview)
        del body[key]
        assert client.post(url, headers=owner, json=body).status_code == 400
    assert client.get(base + '/Users/' + employee['employee_id'], headers=h).status_code == 404


def test_exact_email_preview_resolves_same_stable_id_and_inactive_without_inventory(http):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    endpoint = '/v1/admin/scim-providers/acme/' + pid + '/assignment-preview'
    by_id = client.get(route(base, employee), headers=owner).json()
    by_email = client.post(endpoint, headers=owner, json={'userName': 'MANUAL@ACME.TEST'})
    assert by_email.status_code == 200 and by_email.json() == by_id
    roster.offboard_employee(db, 'acme', employee['employee_id'])
    inactive = client.post(endpoint, headers=owner, json={'userName': employee['email']})
    assert inactive.status_code == 200 and inactive.json()['employee']['status'] == 'offboarded'
    assert inactive.json()['employee_version'] != by_id['employee_version']
    assert client.post(endpoint, headers=h, json={'userName': employee['email']}).status_code == 401
    assert client.post(endpoint.replace('/acme/', '/other/'), headers=owner,
                      json={'userName': employee['email']}).status_code == 404
    for params in ({}, {'userName': 'absent@acme.test'}, {'userName': 'manual'},
                   {'userName': employee['email'], 'match': 'email'}):
        r = client.post(endpoint, headers=owner, json=params)
        assert r.status_code == (404 if params == {'userName': 'absent@acme.test'} else 400), r.text


def test_assignment_and_offboarding_race_never_restore_employee_access(http):
    from threading import Barrier
    client, db, owner, h, base, pid = http
    employee = manual(db)
    uid = employee['employee_id']
    invitation = roster.create_personal_invite(db, 'acme', uid)
    device = roster.enroll_with_personal_invite(db, token=invitation['token'], requested_organization_id='acme', device_id='race-device')
    assignments = Assignments(client.app.state.scim_provisioning)
    preview = assignments.preview('acme', pid, uid)
    gate = Barrier(2)
    def assign():
        gate.wait(timeout=10)
        try:
            assignments.assign('acme', pid, uid, consent(preview), 'owned-admin')
            return 201
        except scim.Error as error:
            return error.status
    def offboard():
        gate.wait(timeout=10)
        roster.offboard_employee(db, 'acme', uid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(assign), pool.submit(offboard)
        assert a.result(timeout=20) in (201, 412)
        b.result(timeout=20)
    assert roster.get_employee(db, 'acme', uid)['status'] == 'offboarded'
    assert db.authenticate(device['token']) is None
    assert roster.peek_personal_invite(db, invitation['token']) is None
    read = client.get(base + '/Users/' + uid, headers=h)
    assert read.status_code == 404 or (read.status_code == 200 and read.json()['active'] is False)


def test_assignment_preserves_legacy_actor_without_email_based_reattribution(http):
    from gateway.human_access import set_actor_teams, actor_teams
    client, db, owner, h, base, pid = http
    employee = manual(db)
    with db.connect() as conn:
        db._execute(conn, 'UPDATE gateway_employees SET actor_id=? WHERE employee_id=?', ('legacy-stable-actor', employee['employee_id']))
    set_actor_teams(db, 'acme', 'legacy-stable-actor', ['ops'])
    before = roster.get_employee(db, 'acme', employee['employee_id'])
    preview = client.get(route(base, employee), headers=owner).json()
    r = client.post(route(base, employee), headers=owner, json=consent(preview))
    assert r.status_code == 201, r.text
    assert r.json()['userName'] == employee['email']
    assert roster.get_employee(db, 'acme', employee['employee_id']) == before
    assert actor_teams(db, 'acme', 'legacy-stable-actor') == {'ops'}


def test_preview_rejects_invalid_identifiers_and_duplicate_body_fields(http):
    client, db, owner, h, base, pid = http
    employee = manual(db)
    url = route(base, employee)
    assert client.get(url.rsplit('/', 1)[0] + '/%00', headers=owner).status_code == 400
    assert client.get(url.replace(pid, '%00'), headers=owner).status_code == 400
    endpoint = '/v1/admin/scim-providers/acme/' + pid + '/assignment-preview'
    assert client.post(endpoint, headers={**owner, 'Content-Type': 'application/json'},
                       content=b'{"userName":"manual@acme.test","userName":"other@acme.test"}').status_code == 400
    assert client.post(endpoint + '?userName=manual@acme.test', headers=owner,
                       json={'userName': employee['email']}).status_code == 400
