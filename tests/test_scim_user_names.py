"""Owned SCIM name fixtures, never identity or consent adoption claims."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from gateway import employees as roster, scim_provisioning as scim
from gateway.scim_user_names import FIELDS, normalize
from test_process_collaboration import store
from test_scim_routes import http, user
from test_scim_search import search


NAME = {'formatted': 'Dr. Zoë  雪', 'givenName': 'Zoë', 'familyName': '雪',
        'middleName': 'A', 'honorificPrefix': 'Dr.', 'honorificSuffix': 'III'}


def made(http):
    client, db, owner, headers, base, pid = http
    response = client.post(base + '/Users', headers=headers, json={**user(), 'name': NAME})
    assert response.status_code == 201, response.text
    return response


def update(http, response, *operations, version=None):
    client, db, owner, headers, base, pid = http
    return client.patch(response.headers['location'],
        headers={**headers, 'If-Match': version or response.headers['etag']},
        json={'schemas': [scim.PATCH], 'Operations': list(operations)})


def test_name_roundtrip_discovery_and_minimized_get_post_selection(http):
    client, db, owner, h, base, pid = http
    response = made(http)
    value = response.json()
    assert value['name'] == NAME and value['displayName'] == user()['displayName']
    assert value['userName'] == user()['userName']
    # Collection schema discovery uses the same exact advertised User shape.
    schemas = client.get(base + '/Schemas', headers=h).json()['Resources']
    core = next(x for x in schemas if x['id'] == scim.CORE)
    name = next(x for x in core['attributes'] if x['name'] == 'name')
    assert name['type'] == 'complex' and name['multiValued'] is False
    assert {x['name'] for x in name['subAttributes']} == set(FIELDS)
    get = client.get(base + '/Users', headers=h, params={'attributes': 'NAME.GIVENNAME,name.familyName'})
    post = search(client, h, base, 'Users', attributes=['name.givenName', 'name.familyName'])
    assert post.status_code == get.status_code == 200 and post.json() == get.json()
    assert post.json()['Resources'][0] == {'schemas': [scim.CORE], 'id': value['id'],
        'name': {'givenName': 'Zoë', 'familyName': '雪'}}
    limited = client.get(response.headers['location'], headers=h, params={'excludedAttributes': 'name'})
    assert 'name' not in limited.json() and limited.headers['etag'] == response.headers['etag']
    assert client.get(base + '/Users', headers=h, params={'filter': 'name.givenName eq "Zoë"'}).status_code == 400


@pytest.mark.parametrize('verb', ['add', 'replace'])
def test_partial_complex_and_subattribute_patch_preserves_unspecified_name_fields(http, verb):
    response = made(http)
    r = update(http, response, {'op': verb.upper(), 'path': 'NaMe', 'value': {'GIVENNAME': 'Jane'}})
    assert r.status_code == 200 and r.json()['name'] == {**NAME, 'givenName': 'Jane'}, r.text
    r = update(http, r, {'op': verb, 'path': 'NAME.FAMILYNAME', 'value': 'Edited'},
        {'op': verb, 'value': {'NaMe': {'middleName': None, 'honorificPrefix': 'Prof.'}}})
    expected = {**NAME, 'givenName': 'Jane', 'familyName': 'Edited', 'honorificPrefix': 'Prof.'}
    del expected['middleName']
    assert r.status_code == 200 and r.json()['name'] == expected, r.text
    r = update(http, r, {'op': 'remove', 'path': 'name.honorificSuffix'})
    assert 'honorificSuffix' not in r.json()['name'] and r.json()['displayName'] == user()['displayName']
    r = update(http, r, {'op': 'remove', 'path': 'name'})
    assert r.status_code == 200 and 'name' not in r.json()
    assert update(http, r, {'op': 'remove', 'path': 'name'}).json() == r.json()


def test_put_replaces_complete_name_and_omission_unassigns_without_implicit_rehire(http):
    client, db, owner, h, base, pid = http
    response = made(http)
    body = {**user(), 'name': {'givenName': 'Only'}}
    r = client.put(response.headers['location'], headers={**h, 'If-Match': response.headers['etag']}, json=body)
    assert r.status_code == 200 and r.json()['name'] == {'givenName': 'Only'}
    r = update(http, r, {'op': 'replace', 'path': 'active', 'value': False})
    body = user();body.pop('active')
    r = client.put(response.headers['location'], headers={**h, 'If-Match': r.headers['etag']}, json=body)
    assert r.status_code == 200 and 'name' not in r.json() and r.json()['active'] is False


def test_name_only_update_never_changes_roster_identity_credentials_or_audit_values(http):
    client, db, owner, h, base, pid = http
    response = made(http);uid = response.json()['id']
    invite = roster.create_personal_invite(db, 'acme', uid)
    device = roster.enroll_with_personal_invite(db, token=invite['token'], requested_organization_id='acme', device_id='names-fixture')
    before = roster.get_employee(db, 'acme', uid)
    r = update(http, response, {'op': 'replace', 'path': 'name.formatted', 'value': 'Private new full name'})
    assert r.status_code == 200 and r.headers['etag'] != response.headers['etag']
    assert roster.get_employee(db, 'acme', uid) == before
    assert db.authenticate(device['token']) is not None and roster.peek_personal_invite(db, invite['token']) is not None
    with db.connect() as conn:
        rows = roster._all(db, conn, 'SELECT details_json FROM audit_log WHERE action=?', ('scim.user.updated',))
    assert rows and all('Private new full name' not in x['details_json'] for x in rows)


def test_name_noop_preserves_entire_projection_timestamp_and_audits(http):
    client, db, owner, h, base, pid = http
    response = made(http)
    def audit_count():
        with db.connect() as conn:
            return roster._one(db, conn, 'SELECT count(*) AS n FROM audit_log')['n']
    before = audit_count()
    for operation in [{'op': 'replace', 'path': 'name', 'value': {'givenName': 'Zoë'}},
                      {'op': 'add', 'path': 'name', 'value': {}},
                      {'op': 'replace', 'path': 'name.givenName', 'value': 'Zoë'}]:
        r = update(http, response, operation)
        assert r.status_code == 200 and r.json() == response.json() and r.headers['etag'] == response.headers['etag']
    assert audit_count() == before


def test_nulls_unassign_subattributes_and_whole_name_without_synthesizing_display(http):
    response = made(http)
    r = update(http, response, {'op': 'replace', 'path': 'name.givenName', 'value': None})
    assert r.status_code == 200 and 'givenName' not in r.json()['name']
    r = update(http, r, {'op': 'add', 'value': {'name': None}})
    assert r.status_code == 200 and 'name' not in r.json() and r.json()['displayName'] == user()['displayName']


def test_creation_audit_failure_rolls_back_name_and_identity_rows(http, monkeypatch):
    client, db, owner, h, base, pid = http
    def fail(*args):raise RuntimeError('Owned create audit failure')
    monkeypatch.setattr(client.app.state.scim_provisioning, 'audit', fail)
    with pytest.raises(RuntimeError):
        client.post(base + '/Users', headers=h, json={**user(), 'name': NAME})
    assert client.get(base + '/Users', headers=h).json()['totalResults'] == 0
    with db.connect() as conn:
        assert roster._one(db, conn, 'SELECT count(*) AS n FROM gateway_scim_user_names')['n'] == 0


def test_human_roster_edit_invalidates_name_cas_and_fresh_delta_preserves_human_name(http):
    client, db, owner, h, base, pid = http
    response = made(http)
    roster.upsert_employee(db, 'acme', email=user()['userName'], display_name='Human chosen display')
    op = {'op': 'replace', 'path': 'name.givenName', 'value': 'Provider edit'}
    assert update(http, response, op).status_code == 412
    current = client.get(response.headers['location'], headers=h)
    r = update(http, current, op)
    assert r.status_code == 200 and r.json()['displayName'] == 'Human chosen display'
    assert r.json()['name'] == {**NAME, 'givenName': 'Provider edit'}


@pytest.mark.parametrize('bad', [
    {'op': 'replace', 'path': 'name.password', 'value': 'secret'},
    {'op': 'replace', 'path': 'name.givenName.child', 'value': 'bad'},
    {'op': 'replace', 'path': 'name', 'value': {'givenName': True}},
    {'op': 'replace', 'path': 'name', 'value': {'givenName': 'a', 'GivenName': 'b'}},
    {'op': 'replace', 'path': 'name'},
])
def test_invalid_later_name_operation_is_atomic_with_lifecycle(http, bad):
    client, db, owner, h, base, pid = http
    response = made(http)
    r = update(http, response, {'op': 'replace', 'path': 'name.formatted', 'value': 'Never committed'},
        {'op': 'replace', 'path': 'active', 'value': False}, bad)
    assert r.status_code == 400
    assert client.get(response.headers['location'], headers=h).json() == response.json()


@pytest.mark.parametrize('value', [True, 'raw', [], {'givenName': 1}, {'givenName': '\ud800'},
    {'givenName': '\u0000'}, {'givenName': '\u0085'}, {'givenName': 'x' * 257},
    {'formatted': 'x' * 513}, {'unknown': 'x'}])
def test_invalid_names_are_protocol_errors_with_no_employee_creation(http, value):
    client, db, owner, h, base, pid = http
    r = client.post(base + '/Users', headers=h, content=json.dumps({**user(), 'name': value}, ensure_ascii=True))
    assert r.status_code == 400 and r.json()['schemas'] == [scim.ERROR], r.text
    assert client.get(base + '/Users', headers=h).json()['totalResults'] == 0


def test_each_name_bound_preserves_unicode_text_and_explicit_nulls():
    for key, bound in FIELDS.items():
        value = '雪' * bound
        assert normalize({key.upper(): value}) == {key: value}
        with pytest.raises(scim.Error):
            normalize({key: value + 'x'})
    assert normalize({'formatted': '  literal  spacing  ', 'givenName': None}) == {'formatted': '  literal  spacing  '}
    assert normalize(None) == normalize({}) == {}


def test_audit_failure_rolls_back_names_offboarding_and_credentials(http, monkeypatch):
    client, db, owner, h, base, pid = http
    response = made(http);uid = response.json()['id']
    invite = roster.create_personal_invite(db, 'acme', uid)
    device = roster.enroll_with_personal_invite(db, token=invite['token'], requested_organization_id='acme', device_id='rollback-name')
    def fail(*args):raise RuntimeError('Owned audit failure')
    monkeypatch.setattr(client.app.state.scim_provisioning, 'audit', fail)
    with pytest.raises(RuntimeError):
        update(http, response, {'op': 'replace', 'path': 'active', 'value': False},
               {'op': 'replace', 'path': 'name.givenName', 'value': 'Rollback name'})
    assert client.get(response.headers['location'], headers=h).json() == response.json()
    assert db.authenticate(device['token']) is not None and roster.peek_personal_invite(db, invite['token']) is not None


def test_conditional_name_writers_have_one_winner(http):
    from threading import Barrier
    client, db, owner, h, base, pid = http
    response = made(http);service = client.app.state.scim_provisioning;gate = Barrier(2)
    def change(value):
        gate.wait(timeout=10)
        try:
            service.patch_user(pid, h['Authorization'][7:], response.json()['id'], response.headers['etag'],
                {'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'name.givenName', 'value': value}]})
            return 200
        except scim.Error as error:return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(change, 'One'), pool.submit(change, 'Two')
        assert sorted([a.result(timeout=20), b.result(timeout=20)]) == [200, 412]
    assert client.get(response.headers['location'], headers=h).json()['name']['givenName'] in {'One', 'Two'}


def test_additive_upgrade_keeps_unassigned_resources_versions_and_rejects_legacy_named_versions(http):
    client, db, owner, h, base, pid = http
    response = client.post(base + '/Users', headers=h, json=user())
    with db.connect() as conn:db._execute(conn, 'DROP TABLE gateway_scim_user_names')
    scim.init_schema(db);scim.init_schema(db)
    assert client.get(response.headers['location'], headers=h).json() == response.json()
    r = update(http, response, {'op': 'replace', 'path': 'name', 'value': NAME})
    service = client.app.state.scim_provisioning
    with service.transaction() as conn:
        provider = service.provider(conn, pid);row = service.load(conn, provider, response.json()['id'])
        legacy = scim._project({**row, 'provider_name_json': None})
    assert legacy['meta']['version'] != r.headers['etag']
    assert update(http, r, {'op': 'remove', 'path': 'name'}, version=legacy['meta']['version']).status_code == 412


def test_current_backup_preserves_names_and_their_full_resource_version(tmp_path):
    import sqlite3
    from test_scim_upgrade_restore import database
    db = database(tmp_path / 'names-source.db');scim.init_schema(db);service = scim.Provisioning(db)
    provider = service.create_provider('acme', 'Names backup', 'fixture')
    pid, token = provider['provider']['provider_id'], provider['token']
    service.set_enabled(pid, 1, True, 'fixture')
    response = service.create_user(pid, token, {**user(), 'name': NAME})
    path = tmp_path / 'names-restored.db'
    with db.connect() as source, sqlite3.connect(path) as target:source.backup(target)
    restored = database(path);scim.init_schema(restored);copy = scim.Provisioning(restored)
    assert copy.get_user(pid, token, response['id']) == response
    changed = copy.patch_user(pid, token, response['id'], response['meta']['version'],
        {'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'name.givenName', 'value': 'After restore'}]})
    assert changed['name'] == {**NAME, 'givenName': 'After restore'}


def test_name_metadata_stays_provider_scoped_and_tombstone_is_not_identity_adoption(http):
    client, db, owner, h, base, pid = http
    response = made(http)
    other = client.post('/v1/admin/scim-providers/acme', headers=owner, json={'label': 'Other fixture'}).json()
    oid = other['provider']['provider_id']
    assert client.patch('/v1/admin/scim-providers/acme/' + oid, headers=owner, json={'revision': 1, 'enabled': True}).status_code == 200
    oh = {**h, 'Authorization': 'Bearer ' + other['token']}
    assert client.get('/scim/v2/' + oid + '/Users/' + response.json()['id'], headers=oh).status_code == 404
    assert client.get(response.headers['location'], headers=oh).status_code == 401
    assert client.post('/scim/v2/' + oid + '/Users', headers=oh, json={**user(), 'name': {'givenName': 'Adoption refused'}}).status_code == 409
    assert client.delete(response.headers['location'], headers={**h, 'If-Match': response.headers['etag']}).status_code == 204
    assert client.get(response.headers['location'], headers=h).status_code == 404
    assert client.post(base + '/Users', headers=h, json={**user(), 'name': NAME}).status_code == 409
    assert client.post('/v1/admin/scim-providers/acme/' + pid + '/revoke', headers=owner, json={'revision': 2}).status_code == 200
    assert client.get(base + '/Users', headers=h, params={'attributes': 'name'}).status_code == 401


def test_invalid_restored_name_metadata_fails_without_disclosing_stored_value(http):
    client, db, owner, h, base, pid = http
    response = made(http)
    with db.connect() as conn:
        db._execute(conn, 'UPDATE gateway_scim_user_names SET name_json=? WHERE provider_id=?',
            ('{"unsupported":"owned-private-value"}', pid))
    r = client.get(response.headers['location'], headers=h)
    assert r.status_code == 503 and r.json()['schemas'] == [scim.ERROR]
    assert 'owned-private-value' not in r.text


def test_unversioned_policy_remains_route_specific_and_names_do_not_broaden_attributes(http):
    client, db, owner, h, base, pid = http
    response = made(http)
    from test_scim_client_policy import enable
    enable(client, owner, pid)
    body = {'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'name.givenName', 'value': 'Current delta'}]}
    assert client.patch(response.headers['location'], headers=h, json=body).status_code == 200
    assert client.patch(response.headers['location'], headers={**h, 'If-Match': response.headers['etag']}, json=body).status_code == 412
    result = client.post(base + '/Bulk', headers=h, json={'schemas': ['urn:ietf:params:scim:api:messages:2.0:BulkRequest'],
        'Operations': [{'method': 'PATCH', 'path': '/Users/' + response.json()['id'], 'data': body}]})
    assert result.json()['Operations'][0]['status'] == '428'
    current = client.get(response.headers['location'], headers=h)
    assert update(http, current, {'op': 'replace', 'value': {'emails': []}}).status_code == 400
