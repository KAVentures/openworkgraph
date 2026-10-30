"""Owned contacts never establish a login, actor, invitation or consent grant."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from gateway import employees as roster, scim_provisioning as scim
from gateway.scim_user_emails import normalize
from test_process_collaboration import store
from test_scim_routes import http, user
from test_scim_search import search
from test_scim_user_names import NAME, update

EMAILS = [{'value': 'Personal@Example.test', 'type': 'home', 'display': 'Literal  contact'},
          {'value': 'Work@Example.test', 'type': 'work', 'primary': True}]


def made(http):
    client, db, owner, h, base, pid = http
    r = client.post(base + '/Users', headers=h, json={**user(), 'name': NAME, 'emails': EMAILS})
    assert r.status_code == 201, r.text
    return r


def test_roundtrip_schema_and_minimized_get_post_never_change_roster_identity(http):
    client, db, owner, h, base, pid = http
    r = made(http)
    assert r.json()['emails'] == normalize(EMAILS) and r.json()['name'] == NAME
    assert r.json()['userName'] == user()['userName'] and r.json()['displayName'] == user()['displayName']
    schemas = client.get(base + '/Schemas', headers=h).json()['Resources']
    core = next(x for x in schemas if x['id'] == scim.CORE)
    emails = next(x for x in core['attributes'] if x['name'] == 'emails')
    assert emails['multiValued'] is True and emails['type'] == 'complex'
    assert {x['name'] for x in emails['subAttributes']} == {'value', 'display', 'type', 'primary'}
    assert next(x for x in emails['subAttributes'] if x['name'] == 'value')['required'] is True
    assert next(x for x in emails['subAttributes'] if x['name'] == 'display')['mutability'] == 'readWrite'
    get = client.get(base + '/Users', headers=h, params={'attributes': 'EMAILS.VALUE,emails.PRIMARY'})
    post = search(client, h, base, 'Users', attributes=['emails.value', 'emails.primary'])
    assert get.status_code == post.status_code == 200 and get.json() == post.json()
    assert get.json()['Resources'][0] == {'schemas': [scim.CORE], 'id': r.json()['id'],
        'emails': [{'value': 'Personal@Example.test', 'primary': False}, {'value': 'Work@Example.test', 'primary': True}]}
    excluded = client.get(r.headers['location'], headers=h, params={'excludedAttributes': 'emails'})
    assert 'emails' not in excluded.json() and excluded.headers['etag'] == r.headers['etag']
    assert client.get(base + '/Users', headers=h, params={'filter': 'emails.value eq "Work@Example.test"'}).status_code == 400


def test_root_and_pathless_add_preserve_unsupplied_children_and_switch_primary(http):
    r = made(http)
    r = update(http, r, {'op': 'add', 'path': 'EMAILS', 'value': [{'value': 'Work@Example.test', 'type': 'work'}]})
    assert r.status_code == 200 and r.json()['emails'] == normalize(EMAILS)
    r = update(http, r, {'op': 'ADD', 'value': {'EMAILS': [{'value': 'New@Example.test', 'primary': True}]}})
    assert r.status_code == 200 and [x['value'] for x in r.json()['emails'] if x['primary']] == ['New@Example.test']
    assert next(x for x in r.json()['emails'] if x.get('type') == 'home')['display'] == 'Literal  contact'
    assert sum(x['primary'] for x in r.json()['emails']) == 1
    r = update(http, r, {'op': 'replace', 'value': {'emails': [{'value': 'Only@Example.test'}]}})
    assert r.status_code == 200 and r.json()['emails'] == [{'value': 'Only@Example.test', 'primary': False}]


def test_selectors_preserve_original_escaped_literals_and_support_explicit_zero_match_add(http):
    client, db, owner, h, base, pid = http
    r = made(http)
    literal = 'MiXeD "quoted" \\ Case 雪'
    r = update(http, r, {'op': 'add', 'path': 'EmAiLs[TyPe EQ ' + json.dumps(literal) + '].VaLuE',
                         'value': 'UPPER@Example.test'})
    assert r.status_code == 200, r.text
    assert {'value': 'UPPER@Example.test', 'type': literal, 'primary': False} in r.json()['emails']
    path = scim.CORE + ':emails[type eq ' + json.dumps(literal.swapcase()) + '].display'
    r = update(http, r, {'op': 'replace', 'path': path, 'value': '  retained text  '})
    assert r.status_code == 200 and next(x for x in r.json()['emails'] if x['type'] == literal)['display'] == '  retained text  '
    r = update(http, r, {'op': 'replace', 'path': 'emails[value eq "upper@example.test"].primary', 'value': True})
    assert r.status_code == 200 and [x['value'] for x in r.json()['emails'] if x['primary']] == ['UPPER@Example.test']
    r = update(http, r, {'op': 'remove', 'path': 'emails[value eq "UPPER@Example.test"].value'})
    assert r.status_code == 200 and all(x['value'] != 'UPPER@Example.test' for x in r.json()['emails'])


def test_multi_match_updates_remove_and_no_target_semantics(http):
    r = made(http)
    r = update(http, r, {'op': 'add', 'path': 'emails', 'value': [{'value': 'Another@Example.test', 'type': 'work'}]})
    assert r.status_code == 200
    invalid = update(http, r, {'op': 'replace', 'path': 'emails[type eq "work"].primary', 'value': True})
    assert invalid.status_code == 400 and invalid.json()['scimType'] == 'invalidValue'
    r = update(http, r, {'op': 'replace', 'path': 'emails[type eq "work"]', 'value': {'display': 'Shared label'}})
    assert r.status_code == 200 and all(x['display'] == 'Shared label' for x in r.json()['emails'] if x['type'] == 'work')
    missing = update(http, r, {'op': 'replace', 'path': 'emails[type eq "absent"].value', 'value': 'never@example.test'})
    assert missing.status_code == 400 and missing.json()['scimType'] == 'noTarget'
    assert update(http, r, {'op': 'remove', 'path': 'emails[type eq "absent"]'}).json() == r.json()
    r = update(http, r, {'op': 'remove', 'path': 'emails[type eq "work"]'})
    assert r.status_code == 200 and len(r.json()['emails']) == 1 and r.json()['emails'][0]['type'] == 'home'
    r = update(http, r, {'op': 'replace', 'path': 'emails[type eq "home"].display', 'value': None},
                       {'op': 'remove', 'path': 'emails[type eq "home"].type'})
    assert r.json()['emails'] == [{'value': 'Personal@Example.test', 'primary': False}]


def test_order_false_normalization_and_absent_contacts_are_version_audit_noops(http):
    client, db, owner, h, base, pid = http
    r = made(http)
    with db.connect() as conn:
        count = roster._one(db, conn, 'SELECT count(*) AS n FROM audit_log')['n']
    unchanged = update(http, r, {'op': 'replace', 'path': 'emails', 'value': list(reversed(normalize(EMAILS)))})
    assert unchanged.json() == r.json() and unchanged.headers['etag'] == r.headers['etag']
    with db.connect() as conn:assert roster._one(db, conn, 'SELECT count(*) AS n FROM audit_log')['n'] == count
    r = update(http, r, {'op': 'remove', 'path': 'emails'})
    assert 'emails' not in r.json()
    assert update(http, r, {'op': 'replace', 'path': 'emails', 'value': None}).json() == r.json()


def test_put_complete_replacement_and_omission_never_implicitly_rehires(http):
    client, db, owner, h, base, pid = http
    r = made(http)
    r = update(http, r, {'op': 'replace', 'path': 'active', 'value': False})
    body = user();body.pop('active');body['name'] = NAME;body['emails'] = [{'value': 'Only@Example.test'}]
    r = client.put(r.headers['location'], headers={**h, 'If-Match': r.headers['etag']}, json=body)
    assert r.status_code == 200 and r.json()['emails'] == normalize(body['emails']) and r.json()['active'] is False
    body.pop('emails')
    r = client.put(r.headers['location'], headers={**h, 'If-Match': r.headers['etag']}, json=body)
    assert r.status_code == 200 and 'emails' not in r.json() and r.json()['name'] == NAME and r.json()['active'] is False


@pytest.mark.parametrize('value', [True, {}, 'raw', [None], [{}], [{'value': None}], [{'value': 'missing-at'}],
    [{'value': 'space here@example.test'}], [{'value': '\ud800@example.test'}],
    [{'value': 'x@example.test', 'primary': 1}], [{'value': 'x@example.test', 'VALUE': 'y@example.test'}],
    [{'value': 'x@example.test', 'password': 'private'}], [{'value': 'x' * 310 + '@example.test'}],
    [{'value': 'x@example.test', 'type': 'x' * 65}], [{'value': 'x@example.test', 'display': 'x' * 513}],
    [{'value': 'x@example.test', 'primary': True}, {'value': 'y@example.test', 'primary': True}],
    [{'value': 'x@example.test', 'type': 'work'}, {'value': 'X@EXAMPLE.TEST', 'type': 'WORK'}],
    [{'value': str(i) + '@example.test'} for i in range(17)]])
def test_invalid_contact_payload_cannot_create_employee(http, value):
    client, db, owner, h, base, pid = http
    r = client.post(base + '/Users', headers=h, content=json.dumps({**user(), 'emails': value}, ensure_ascii=True))
    assert r.status_code == 400 and r.json()['schemas'] == [scim.ERROR], r.text
    assert client.get(base + '/Users', headers=h).json()['totalResults'] == 0


@pytest.mark.parametrize('path', ['emails[type co "work"].value', 'emails[type eq "work" and value eq "x"]',
    'emails[type eq "work"].value.nested', 'emails[primary eq true]', 'emails[type eq "bad\\q"].value',
    'emails[TYPE eq "work"] extra', 'emails.unknown', 'emails.' + 'x' * 1024])
def test_unsupported_later_selector_rolls_back_contact_and_offboarding(http, path):
    client, db, owner, h, base, pid = http
    r = made(http)
    invalid = update(http, r, {'op': 'replace', 'path': 'emails[type eq "home"].display', 'value': 'Never committed'},
                            {'op': 'replace', 'path': 'active', 'value': False},
                            {'op': 'replace', 'path': path, 'value': 'x@example.test'})
    assert invalid.status_code == 400
    assert client.get(r.headers['location'], headers=h).json() == r.json()


def test_contact_only_changes_preserve_roster_credentials_and_private_values_not_in_audits(http):
    client, db, owner, h, base, pid = http
    r = made(http);uid = r.json()['id']
    invite = roster.create_personal_invite(db, 'acme', uid)
    device = roster.enroll_with_personal_invite(db, token=invite['token'], requested_organization_id='acme', device_id='contacts')
    before = roster.get_employee(db, 'acme', uid)
    changed = update(http, r, {'op': 'replace', 'path': 'emails[type eq "work"].value', 'value': 'private-new@example.test'})
    assert changed.status_code == 200 and changed.headers['etag'] != r.headers['etag']
    assert roster.get_employee(db, 'acme', uid) == before and db.authenticate(device['token']) is not None
    pending = roster.create_personal_invite(db, 'acme', uid)
    assert roster.peek_personal_invite(db, pending['token']) is not None
    with db.connect() as conn:
        audits = roster._all(db, conn, 'SELECT details_json FROM audit_log')
    assert 'private-new@example.test' not in json.dumps(audits)
    assert update(http, r, {'op': 'remove', 'path': 'emails'}).status_code == 412


def test_audit_failure_rolls_back_contacts_and_offboarding_credentials(http, monkeypatch):
    client, db, owner, h, base, pid = http
    r = made(http);uid = r.json()['id']
    invite = roster.create_personal_invite(db, 'acme', uid)
    device = roster.enroll_with_personal_invite(db, token=invite['token'], requested_organization_id='acme', device_id='contacts-rollback')
    def fail(*args):raise RuntimeError('Owned audit failure')
    monkeypatch.setattr(client.app.state.scim_provisioning, 'audit', fail)
    with pytest.raises(RuntimeError):
        update(http, r, {'op': 'replace', 'path': 'emails', 'value': []}, {'op': 'replace', 'path': 'active', 'value': False})
    assert client.get(r.headers['location'], headers=h).json() == r.json() and db.authenticate(device['token']) is not None


def test_two_versioned_contact_writers_have_one_winner(http):
    client, db, owner, h, base, pid = http
    r = made(http);gate = Barrier(2);service = client.app.state.scim_provisioning
    def change(value):
        gate.wait(timeout=10)
        try:
            service.patch_user(pid, h['Authorization'][7:], r.json()['id'], r.headers['etag'],
                {'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'emails[type eq "work"].display', 'value': value}]})
            return 200
        except scim.Error as error:return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        a,b = pool.submit(change, 'One'),pool.submit(change, 'Two')
        assert sorted([a.result(timeout=20),b.result(timeout=20)]) == [200,412]


def test_alias_is_not_login_adoption_or_provider_ownership(http):
    client, db, owner, h, base, pid = http
    r = made(http)
    body = {**user(), 'userName': 'second@acme.test', 'externalId': 'second', 'emails': EMAILS}
    second = client.post(base + '/Users', headers=h, json=body)
    assert second.status_code == 201 and second.json()['id'] != r.json()['id']
    other = client.post('/v1/admin/scim-providers/acme', headers=owner, json={'label': 'Other contacts'}).json()
    oid = other['provider']['provider_id']
    assert client.patch('/v1/admin/scim-providers/acme/' + oid, headers=owner, json={'revision': 1, 'enabled': True}).status_code == 200
    oh = {**h, 'Authorization': 'Bearer ' + other['token']}
    assert client.get('/scim/v2/' + oid + '/Users/' + r.json()['id'], headers=oh).status_code == 404
    assert client.post('/scim/v2/' + oid + '/Users', headers=oh, json={**user(), 'emails': EMAILS}).status_code == 409
    assert client.delete(r.headers['location'], headers={**h, 'If-Match': r.headers['etag']}).status_code == 204
    assert client.post(base + '/Users', headers=h, json={**user(), 'emails': EMAILS}).status_code == 409


def test_corrupt_restored_contacts_fail_without_leaking_values(http):
    client, db, owner, h, base, pid = http
    r = made(http)
    with db.connect() as conn:db._execute(conn, 'UPDATE gateway_scim_user_emails SET emails_json=? WHERE provider_id=?',
        ('[{"private":"never-disclose@example.test"}]', pid))
    result = client.get(r.headers['location'], headers=h)
    assert result.status_code == 503 and 'never-disclose' not in result.text


def test_current_backup_preserves_contacts_names_and_complete_resource_version(tmp_path):
    import sqlite3
    from test_scim_upgrade_restore import database
    db = database(tmp_path / 'contacts-source.db');scim.init_schema(db);service = scim.Provisioning(db)
    provider = service.create_provider('acme', 'Contacts backup', 'fixture')
    pid, token = provider['provider']['provider_id'], provider['token'];service.set_enabled(pid,1,True,'fixture')
    r = service.create_user(pid, token, {**user(), 'name': NAME, 'emails': EMAILS})
    path = tmp_path / 'contacts-restored.db'
    with db.connect() as source, sqlite3.connect(path) as target:source.backup(target)
    restored = database(path);scim.init_schema(restored);copy = scim.Provisioning(restored)
    assert copy.get_user(pid,token,r['id']) == r
    changed = copy.patch_user(pid,token,r['id'],r['meta']['version'], {'schemas':[scim.PATCH],
        'Operations':[{'op':'replace','path':'emails[type eq "home"].display','value':'After restore'}]})
    assert changed['name'] == NAME and changed['meta']['version'] != r['meta']['version']


def test_literal_unicode_bounds_and_same_address_with_distinct_types():
    address = '雪' * 307 + '@example.test'
    assert len(address) == 320
    value = [{'value': address, 'type': '雪' * 64, 'display': '雪' * 512},
             {'value': address, 'type': 'home', 'primary': None}]
    result = normalize(value)
    assert len(result) == 2 and all(x['value'] == address and x['primary'] is False for x in result)
    for field, oversized in [('value', '雪' + address), ('type', '雪' * 65), ('display', '雪' * 513)]:
        with pytest.raises(scim.Error):normalize([{**value[0], field: oversized}])


def test_old_resource_upgrade_and_contact_blind_etags_are_not_silently_accepted(http):
    client, db, owner, h, base, pid = http
    r = client.post(base + '/Users', headers=h, json={**user(), 'name': NAME})
    with db.connect() as conn:db._execute(conn, 'DROP TABLE gateway_scim_user_emails')
    scim.init_schema(db);scim.init_schema(db)
    assert client.get(r.headers['location'], headers=h).json() == r.json()
    changed = update(http, r, {'op': 'replace', 'path': 'emails', 'value': EMAILS})
    assert changed.status_code == 200
    service = client.app.state.scim_provisioning
    with service.transaction() as conn:
        provider = service.provider(conn,pid);row = service.load(conn,provider,r.json()['id'])
        legacy = scim._project({**row,'provider_emails_json':None})
    assert legacy['meta']['version'] != changed.headers['etag']
    assert update(http, changed, {'op':'remove','path':'emails'},version=legacy['meta']['version']).status_code == 412


def test_human_edit_invalidates_contact_cas_and_fresh_delta_preserves_identity(http):
    client, db, owner, h, base, pid = http
    r = made(http)
    roster.upsert_employee(db,'acme',email=user()['userName'],display_name='Human chosen name')
    op = {'op':'replace','path':'emails[type eq "work"].display','value':'Provider contact label'}
    assert update(http,r,op).status_code == 412
    current = client.get(r.headers['location'],headers=h)
    changed = update(http,current,op)
    assert changed.status_code == 200 and changed.json()['displayName'] == 'Human chosen name'
    assert changed.json()['userName'] == user()['userName'] and changed.json()['name'] == NAME


def test_creation_audit_failure_rolls_back_contacts_and_roster(http,monkeypatch):
    client, db, owner, h, base, pid = http
    def fail(*args):raise RuntimeError('Owned create audit failure')
    monkeypatch.setattr(client.app.state.scim_provisioning,'audit',fail)
    with pytest.raises(RuntimeError):made(http)
    assert client.get(base+'/Users',headers=h).json()['totalResults'] == 0
    with db.connect() as conn:assert roster._one(db,conn,'SELECT count(*) AS n FROM gateway_scim_user_emails')['n'] == 0


def test_missing_version_policy_does_not_relax_supplied_versions_direct_service_or_bulk(http):
    client, db, owner, h, base, pid = http
    r = made(http)
    body = {'schemas':[scim.PATCH],'Operations':[{'op':'replace','path':'emails[type eq "work"].display','value':'Current contact delta'}]}
    assert client.patch(r.headers['location'],headers=h,json=body).status_code == 428
    from test_scim_client_policy import enable
    enable(client,owner,pid)
    changed = client.patch(r.headers['location'],headers=h,json=body)
    assert changed.status_code == 200
    assert client.patch(r.headers['location'],headers={**h,'If-Match':r.headers['etag']},json=body).status_code == 412
    with pytest.raises(scim.Error) as exc:
        client.app.state.scim_provisioning.patch_user(pid,h['Authorization'][7:],r.json()['id'],None,body)
    assert exc.value.status == 428
    bulk = client.post(base+'/Bulk',headers=h,json={'schemas':['urn:ietf:params:scim:api:messages:2.0:BulkRequest'],
        'Operations':[{'method':'PATCH','path':'/Users/'+r.json()['id'],'data':body}]})
    assert bulk.json()['Operations'][0]['status'] == '428'
    assert client.post('/v1/admin/scim-providers/acme/'+pid+'/revoke',headers=owner,json={'revision':3}).status_code == 200
    assert client.get(r.headers['location'],headers=h,params={'attributes':'emails'}).status_code == 401
