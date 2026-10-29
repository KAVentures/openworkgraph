"""Owned HTTP oracles for bounded search and identity-field minimization."""
from copy import deepcopy

import pytest

from gateway import scim_provisioning as scim, scim_groups, scim_query as query
from test_scim_routes import http, user
from test_process_collaboration import store


def seed(client, headers, base):
    users = []
    for i in range(3):
        response = client.post(base + '/Users', headers=headers,
            json={**user(), 'userName': f'person{i}@acme.test', 'externalId': f'native-{i}'})
        assert response.status_code == 201, response.text
        users.append(response.json())
    groups = []
    for i in range(2):
        response = client.post(base + '/Groups', headers=headers, json={
            'schemas': [scim_groups.CORE], 'displayName': f'Team {i}', 'externalId': f'team-{i}',
            'members': [{'value': u['id'], 'type': 'User'} for u in users[:i + 1]]})
        assert response.status_code == 201, response.text
        groups.append(response.json())
    return users, groups


def search(client, h, base, collection, **options):
    return client.post(base + '/' + collection + '/.search', headers=h,
                       json={'schemas': [query.SEARCH], **options})


@pytest.mark.parametrize('collection,filter_text', [
    ('Users', ''), ('Groups', ''), ('Users', 'userName eq "PERSON1@ACME.TEST"'),
    ('Users', 'externalId eq "native-2"'), ('Groups', 'displayName eq "Team 1"'),
    ('Groups', 'externalId eq "team-0"')])
def test_post_search_matches_get_with_stable_page_envelope(http, collection, filter_text):
    client, db, owner, h, base, pid = http
    seed(client, h, base)
    for start, count in [(1, 1), (2, 2), (9, 1), (1, 0)]:
        opts = {'filter': filter_text, 'startIndex': start, 'count': count}
        expected = client.get(base + '/' + collection, headers=h, params=opts)
        actual = search(client, h, base, collection, **opts)
        assert actual.status_code == expected.status_code == 200, actual.text
        assert actual.json() == expected.json()
        assert actual.headers['cache-control'] == 'no-store'
        assert actual.headers['content-type'].startswith('application/scim+json')
        assert actual.json()['itemsPerPage'] == len(actual.json()['Resources'])
        if count == 0:
            assert actual.json()['Resources'] == [] and actual.json()['totalResults'] > 0


@pytest.mark.parametrize('collection,core,complex_name', [
    ('Users', scim.CORE, 'groups'), ('Groups', scim_groups.CORE, 'members')])
def test_projection_equivalence_subattributes_headers_and_unchanged_versions(http, collection, core, complex_name):
    client, db, owner, h, base, pid = http
    users, groups = seed(client, h, base)
    uid = (users if collection == 'Users' else groups)[0]['id']
    url = base + '/' + collection + '/' + uid
    original = client.get(url, headers=h)
    full = original.json()
    includes = ['DISPLAYNAME', core + ':' + complex_name.upper() + '.VALUE', 'meta.version']
    single = client.get(url, headers=h, params={'attributes': ','.join(includes)})
    assert single.status_code == 200, single.text
    selected = single.json()
    assert set(selected) == {'schemas', 'id', 'displayName', complex_name, 'meta'}
    assert selected['meta'] == {'version': full['meta']['version']}
    assert selected[complex_name] == [{'value': item['value']} for item in full[complex_name]]
    assert single.headers['etag'] == original.headers['etag']
    assert single.headers['location'] == original.headers['location']
    listed = client.get(base + '/' + collection, headers=h, params={'attributes': ','.join(includes)})
    posted = search(client, h, base, collection, attributes=includes)
    assert posted.json() == listed.json()
    assert next(x for x in posted.json()['Resources'] if x['id'] == uid) == selected
    # Exclusion cannot strip always-returned schema/id; it can remove metadata.
    excludes = ['SCHEMAS', 'id', 'meta', complex_name + '.type', 'externalId']
    excluded = client.get(url, headers=h, params={'excludedAttributes': ','.join(excludes)})
    assert excluded.status_code == 200, excluded.text
    value = excluded.json()
    assert value['id'] == full['id'] and value['schemas'] == full['schemas']
    assert 'externalId' not in value and 'meta' not in value
    assert all('type' not in x for x in value[complex_name])
    assert excluded.headers['etag'] == original.headers['etag']
    assert client.get(url, headers=h).json() == full
    # The header version remains the full-resource CAS token after minimization.
    response = client.patch(url, headers={**h, 'If-Match': excluded.headers['etag']},
        json={'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'displayName', 'value': 'Renamed'}]})
    assert response.status_code == 200, response.text
    assert response.headers['etag'] != original.headers['etag']


def test_user_readonly_group_projection_tracks_current_membership_without_search_writes(http):
    client, db, owner, h, base, pid = http
    users, groups = seed(client, h, base)
    uid = users[0]['id']
    before = client.get(base + '/Users/' + uid, headers=h).json()
    assert len(before['groups']) == 2
    for _ in range(2):
        r = search(client, h, base, 'Users', filter='externalId eq "native-0"', attributes=['groups.value'])
        assert r.json()['Resources'] == [{'schemas': [scim.CORE], 'id': uid,
            'groups': [{'value': g['value']} for g in before['groups']]}]
    assert client.get(base + '/Users/' + uid, headers=h).json() == before
    # Existing epoch-aware offboarding projection is reused, never reconstructed.
    off = client.patch(base + '/Users/' + uid, headers={**h, 'If-Match': before['meta']['version']},
        json={'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'active', 'value': False}]})
    assert off.status_code == 200, off.text
    for g in groups:
        r = search(client, h, base, 'Groups', filter='externalId eq "' + g['externalId'] + '"', attributes=['members'])
        assert all(m['value'] != uid for m in r.json()['Resources'][0]['members'])


@pytest.mark.parametrize('options', [
    {'schemas': []}, {'schemas': [scim.CORE]}, {'sortBy': 'userName'}, {'sortOrder': 'ascending'},
    {'filter': False}, {'filter': None}, {'filter': []}, {'filter': 'x' * 1001},
    {'filter': 'active eq true'}, {'filter': 'userName pr'},
    {'startIndex': False}, {'startIndex': '1'}, {'startIndex': 0}, {'startIndex': -1},
    {'startIndex': 2**63}, {'count': True}, {'count': 1.0}, {'count': '1'}, {'count': None}, {'count': -1},
    {'count': 201}, {'attributes': 'userName'}, {'attributes': []}, {'attributes': [1]},
    {'attributes': ['password']}, {'attributes': ['userName'], 'excludedAttributes': ['id']},
    {'attributes': ['userName'] * 33}, {'attributes': ['x' * 4097]}, {'attributes': ['members[value eq "x"]']},
    {'attributes': ['meta.password']}, {'attributes': ['userName.value']}, {'attributes': ['name.familyName']},
    {'attributes': [scim_groups.CORE + ':displayName']}, {'attributes': ['meta.version.extra']},
    {'attributes': ['']}, {'ATTRIBUTES': ['id'], 'attributes': ['userName']},
])
def test_search_refuses_unsupported_or_malformed_options_without_writes(http, options):
    client, db, owner, h, base, pid = http
    response = search(client, h, base, 'Users', **options)
    assert response.status_code == 400, response.text
    assert response.json()['schemas'] == [scim.ERROR]
    assert client.get(base + '/Users', headers=h).json()['totalResults'] == 0
    assert client.get(base + '/Groups', headers=h).json()['totalResults'] == 0


@pytest.mark.parametrize('params', [
    [('attributes', 'id'), ('ATTRIBUTES', 'userName')], [('count', '1'), ('count', '2')],
    {'attributes': ''}, {'attributes': ','}, {'attributes': 'userName', 'excludedAttributes': 'id'},
    {'attributes': 'groups.$ref'}, {'attributes': 'groups.value'} | {'filter': 'bad'},
    {'count': '1.0'}, {'count': '+1'}, {'startIndex': '999999999999999999999'},
    {'startIndex': str(2**31)}, {'count': 'true'},
])
def test_get_selection_and_query_errors_are_protocol_errors(http, params):
    client, db, owner, h, base, pid = http
    r = client.get(base + '/Users', headers=h, params=params)
    assert r.status_code == 400 and r.json()['schemas'] == [scim.ERROR], r.text


def test_search_authentication_precedes_body_and_is_rechecked_after_parsing(http, monkeypatch):
    client, db, owner, h, base, pid = http
    for collection in ('Users', 'Groups'):
        for wrong in ({}, owner, {**h, 'Authorization': 'Bearer wrong'}):
            r = client.post(base + '/' + collection + '/.search', headers=wrong, content=b'not json')
            assert r.status_code == 401 and r.json()['schemas'] == [scim.ERROR]
        assert search(client, h, '/scim/v2/other', collection).status_code == 401
    # Invalidate current authority between initial HTTP auth and the service read.
    original = query.parse
    def revoke_during_parse(*args, **kwargs):
        result = original(*args, **kwargs)
        response = client.post('/v1/admin/scim-providers/acme/' + pid + '/revoke', headers=owner, json={'revision': 2})
        assert response.status_code == 200
        return result
    monkeypatch.setattr(query, 'parse', revoke_during_parse)
    r = search(client, h, base, 'Users')
    assert r.status_code == 401 and r.json()['schemas'] == [scim.ERROR]
    monkeypatch.setattr(query, 'parse', original)
    for collection in ('Users', 'Groups'):
        assert search(client, h, base, collection).status_code == 401
        assert client.get(base + '/' + collection, headers=h, params={'attributes': 'id'}).status_code == 401
    assert client.get('/v1/human/processes', headers=h).status_code == 401


def test_search_strict_body_and_no_mutation_or_cross_resource_or_url_options(http):
    client, db, owner, h, base, pid = http
    for raw, code in [(b'[]', 400), (b'{"schemas":[],"schemas":[]}', 400),
            (b'{"schemas":NaN}', 400), (b'{', 400), (b'x' * 65537, 413),
            (b'{"a":' + b'[' * 1100 + b'0' + b']' * 1100 + b'}', 400)]:
        r = client.post(base + '/Users/.search', headers=h, content=raw)
        assert r.status_code == code and r.json()['schemas'] == [scim.ERROR]
    assert client.post(base + '/Users/.search', headers={**h, 'Content-Type': 'text/plain'}, content=b'{}').status_code == 415
    assert client.post(base + '/Users/.search?count=1', headers=h, json={'schemas': [query.SEARCH]}).status_code == 400
    assert client.post(base + '/.search', headers=h, json={'schemas': [query.SEARCH]}).status_code == 404
    assert search(client, h, base, 'Groups', count=101).status_code == 400
    assert search(client, h, base, 'Users', count=200).status_code == 200
    assert search(client, h, base, 'Groups', count=100).status_code == 200
    assert search(client, h, base, 'Users', startIndex=query.MAX_START).status_code == 200
    assert search(client, h, base, 'Users', SCHEMAS=[query.SEARCH]).status_code == 400  # duplicate case alias
    for collection in ('Users', 'Groups'):
        assert client.get(base + '/' + collection, headers=h).json()['totalResults'] == 0


def test_pure_selection_parent_dominates_children_no_aliases_or_value_rewriting():
    source = {'schemas': [scim.CORE], 'id': 'u', 'userName': 'literal@example.test',
        'groups': [{'value': 'g1', 'type': 'direct', 'display': 'meta.version'}, {'value': 'g2'}],
        'meta': {'version': 'W/"full"', 'location': 'https://owned.test/Users/u'}}
    original = deepcopy(source)
    _, sel = query.parse(
        {'schemas': [query.SEARCH], 'attributes': ['groups', 'groups.value', 'meta.version']}, scim.CORE, posted=True)
    selected = query.project(source, sel)
    assert selected == {'schemas': [scim.CORE], 'id': 'u', 'groups': source['groups'], 'meta': {'version': 'W/"full"'}}
    selected['groups'][0]['value'] = 'changed'
    selected['schemas'].append('changed')
    assert source == original
    _, sel = query.parse({'excludedAttributes': 'groups,groups.value,meta.version'}, scim.CORE)
    assert query.project(source, sel) == {'schemas': [scim.CORE], 'id': 'u', 'userName': 'literal@example.test',
                                        'meta': {'location': source['meta']['location']}}


def test_all_search_reads_leave_identity_lifecycle_and_audits_byte_exact(http):
    from gateway import employees as roster
    import json
    client, db, owner, h, base, pid = http
    users, groups = seed(client, h, base)
    tables = ['gateway_scim_providers', 'gateway_scim_users', 'gateway_scim_groups',
        'gateway_employees', 'gateway_employee_access_epochs', 'gateway_personal_invites',
        'gateway_device_identity', 'audit_log']
    def snapshot():
        with db.connect() as conn:
            return {name: sorted(json.dumps(dict(row), sort_keys=True) for row in
                roster._all(db, conn, 'SELECT * FROM ' + name, ())) for name in tables}
    before = snapshot()
    for collection, values in [('Users', users), ('Groups', groups)]:
        for selector in ({'attributes': ['id']}, {'excludedAttributes': ['meta', 'externalId']}, {}):
            assert search(client, h, base, collection, **selector).status_code == 200
        for item in values:
            assert client.get(base + '/' + collection + '/' + item['id'], headers=h,
                              params={'excludedAttributes': 'displayName,meta'}).status_code == 200
    assert snapshot() == before


def test_second_provider_cannot_search_or_select_first_provider_resources(http):
    client, db, owner, h, base, pid = http
    users, groups = seed(client, h, base)
    made = client.post('/v1/admin/scim-providers/acme', headers=owner, json={'label': 'Other'})
    payload = made.json()
    other = '/scim/v2/' + payload['provider']['provider_id']
    other_h = {**h, 'Authorization': 'Bearer ' + payload['token']}
    enabled = client.patch('/v1/admin/scim-providers/acme/' + payload['provider']['provider_id'],
                           headers=owner, json={'revision': 1, 'enabled': True})
    assert enabled.status_code == 200
    for collection, resource in [('Users', users[0]), ('Groups', groups[0])]:
        assert search(client, other_h, base, collection, attributes=['id']).status_code == 401
        response = search(client, other_h, other, collection, attributes=['id'])
        assert response.json()['Resources'] == [] and response.json()['totalResults'] == 0
        r = client.get(other + '/' + collection + '/' + resource['id'], headers=other_h, params={'attributes': 'id'})
        assert r.status_code == 404 and r.json()['schemas'] == [scim.ERROR]


def test_no_resource_or_write_options_are_misinterpreted_as_search(http):
    client, db, owner, h, base, pid = http
    users, groups = seed(client, h, base)
    for collection, item in [('Users', users[0]), ('Groups', groups[0])]:
        url = base + '/' + collection + '/' + item['id']
        current = client.get(url, headers=h)
        for params in ({'count': 1}, {'filter': ''}, {'attributes': 'meta.bad'}, {'attributes': '$ref'}):
            assert client.get(url, headers=h, params=params).status_code == 400
        assert client.patch(url + '?attributes=id', headers={**h, 'If-Match': current.headers['etag']},
            json={'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'displayName', 'value': 'Refused'}]}).status_code == 400
        assert client.get(url, headers=h).json() == current.json()


@pytest.mark.parametrize('collection,attribute', [('Users', 'userName'), ('Users', 'externalId'),
                                                   ('Groups', 'displayName'), ('Groups', 'externalId')])
@pytest.mark.parametrize('escaped', [r'\ud800', r'\u0000'])
def test_filter_decoded_unicode_and_controls_fail_as_standard_errors(http, collection, attribute, escaped):
    client, db, owner, h, base, pid = http
    r = search(client, h, base, collection, filter=attribute + ' eq "' + escaped + '"')
    assert r.status_code == 400 and r.json()['scimType'] == 'invalidFilter', r.text
