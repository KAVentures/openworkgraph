"""Owned matching-client sequences; no live identity provider or credentials."""
import json

import pytest

from gateway import scim_provisioning as scim
from gateway.scim_filter import matching_where
from test_process_collaboration import store
from test_scim_routes import http, user
from test_scim_search import search
from test_scim_groups import group


@pytest.mark.parametrize('resource,field,column', [('User', 'userName', 'e.email'),
    ('User', 'externalId', 'l.external_id'), ('Group', 'displayName', 'display_name')])
def test_parser_keeps_literal_values_out_of_sql(resource, field, column):
    value = 'a\\b " and externalId eq \' OR 1=1 -- 雪'
    text = field + ' EQ ' + json.dumps(value, ensure_ascii=False) + ' AnD externalId eq "scope"'
    where, args = matching_where(text, resource)
    assert where == ' AND ' + column + '=? AND ' + ('l.external_id' if resource == 'User' else 'external_id') + '=?'
    assert args == ((value.strip().lower() if field == 'userName' else value), 'scope')
    assert value not in where


@pytest.mark.parametrize('text', [
    'externalId eq "x" or externalId eq "y"', '(externalId eq "x")',
    'externalId co "x"', 'active eq true', 'id eq "x"',
    'externalId eq "x" and password eq "y"', 'externalId eq "x"and externalId eq "x"',
    'externalId eq "x" andexternalId eq "x"', 'externalId eq "x" and ',
    'externalId eq "x" garbage', 'externalId eq "unterminated',
    'externalId eq "\\q"', 'externalId eq "\\ud800"', 'externalId eq "\\u0000"',
    'externalId eq "\\u0085"', 'externalId eq "x" and externalId eq "\\udfff"',
    'externalId eq null', 'externalId eq ["x"]', 'externalId eq 1',
    'externalId.value eq "x"', 'emails[type eq "work"].value eq "x"',
    ' ', None, False, 0, [], {},
])
def test_unsupported_or_malformed_filters_are_refused_whole(text):
    with pytest.raises(scim.Error) as denied:
        matching_where(text, 'User')
    assert denied.value.status == 400 and denied.value.scim_type == 'invalidFilter'


def test_filter_length_and_clause_budgets_are_exact():
    text = ' and '.join(['externalId eq "x"'] * 8)
    assert matching_where(text, 'User')[1] == ('x',) * 8
    with pytest.raises(scim.Error):
        matching_where(text + ' and externalId eq "x"', 'User')
    size = 1000 - len('externalId eq ""')
    exactly = 'externalId eq "' + 'x' * size + '"'
    assert len(exactly) == 1000 and matching_where(exactly, 'User')[1] == ('x' * size,)
    with pytest.raises(scim.Error):
        matching_where('externalId eq "' + 'x' * (size + 1) + '"', 'User')
    assert matching_where('', 'Group') == ('', ())


@pytest.mark.parametrize('collection,clauses', [
    ('Users', ['UsErNaMe EQ " PERSON@ACME.TEST "', 'externalId eq "native-person"']),
    ('Groups', ['displayName eq "Review"', 'externalId eq "group-review"'])])
def test_get_and_post_conjunction_have_same_complete_counts_pages_and_selection(http, collection, clauses):
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    assert made.status_code == 201
    g = client.post(base + '/Groups', headers=h, json=group(displayName='Review', externalId='group-review'))
    assert g.status_code == 201
    for start, count in [(1, 1), (1, 0), (2, 1)]:
        text = ' AnD '.join(clauses)
        opts = {'filter': text, 'startIndex': start, 'count': count}
        get = client.get(base + '/' + collection, headers=h, params={**opts, 'attributes': 'displayName,meta.version'})
        post = search(client, h, base, collection, **opts, attributes=['displayName', 'meta.version'])
        assert get.status_code == post.status_code == 200, post.text
        assert get.json() == post.json() and get.json()['totalResults'] == 1
        assert get.json()['itemsPerPage'] == (1 if start == count == 1 else 0)
        assert get.headers['cache-control'] == 'no-store'
    for text in [' and '.join([*clauses, 'externalId eq "not-this-resource"']),
                 ' and '.join([clauses[-1]] * 8)]:
        r = search(client, h, base, collection, filter=text)
        assert r.status_code == 200, r.text
        assert r.json()['totalResults'] == (0 if 'not-this-resource' in text else 1)
    # A read must leave both full resource versions and identity lifecycle intact.
    assert client.get(made.headers['location'], headers=h).json() == made.json()
    assert client.get(g.headers['location'], headers=h).json() == g.json()


@pytest.mark.parametrize('collection', ['Users', 'Groups'])
def test_literal_escapes_and_injection_shaped_external_id_match_exactly(http, collection):
    client, db, owner, h, base, pid = http
    value = 'quoted " and \\ 雪 \' OR 1=1 --'
    payload = {**user(), 'externalId': value} if collection == 'Users' else group(externalId=value)
    made = client.post(base + '/' + collection, headers=h, json=payload)
    assert made.status_code == 201, made.text
    text = 'externalId eq ' + json.dumps(value) + ' AND externalId eq ' + json.dumps(value)
    r = search(client, h, base, collection, filter=text)
    assert r.status_code == 200 and [x['id'] for x in r.json()['Resources']] == [made.json()['id']]
    mismatch = text + ' and externalId eq "other"'
    assert search(client, h, base, collection, filter=mismatch).json()['totalResults'] == 0


def test_inactive_users_stay_queryable_but_tombstones_do_not(http):
    client, db, owner, h, base, pid = http
    made = client.post(base + '/Users', headers=h, json=user())
    url = made.headers['location']
    r = client.patch(url, headers={**h, 'If-Match': made.headers['etag']},
        json={'schemas': [scim.PATCH], 'Operations': [{'op': 'replace', 'path': 'active', 'value': False}]})
    text = 'userName eq "person@acme.test" and externalId eq "native-person"'
    found = search(client, h, base, 'Users', filter=text)
    assert found.json()['totalResults'] == 1 and found.json()['Resources'][0]['active'] is False
    assert client.delete(url, headers={**h, 'If-Match': r.headers['etag']}).status_code == 204
    assert search(client, h, base, 'Users', filter=text).json()['totalResults'] == 0


def test_current_provider_authority_is_rechecked_after_query_authentication(http, monkeypatch):
    client, db, owner, h, base, pid = http
    client.post(base + '/Users', headers=h, json=user())
    service = client.app.state.scim_provisioning
    original = service.list_users
    def disable(*args, **kwargs):
        service.set_enabled(pid, 2, False, 'fixture')
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'list_users', disable)
    r = search(client, h, base, 'Users', filter='userName eq "person@acme.test" and externalId eq "native-person"')
    assert r.status_code == 401


@pytest.mark.parametrize('collection,field', [('Users', 'userName'), ('Groups', 'displayName')])
def test_http_bad_later_clause_returns_protocol_error_instead_of_partial_matches(http, collection, field):
    client, db, owner, h, base, pid = http
    value = 'person@acme.test' if collection == 'Users' else 'Analysts'
    payload = user() if collection == 'Users' else group()
    made = client.post(base + '/' + collection, headers=h, json=payload)
    for suffix in [' or externalId eq "x"', ' and password eq "x"', ' and ',
                   ' and externalId eq "\\ud800"', ' and externalId eq "\\u0000"']:
        text = field + ' eq ' + json.dumps(value) + suffix
        for r in [client.get(base + '/' + collection, headers=h, params={'filter': text}),
                  search(client, h, base, collection, filter=text)]:
            assert r.status_code == 400, r.text
            assert r.json()['schemas'] == [scim.ERROR] and r.json()['scimType'] == 'invalidFilter'
    assert client.get(made.headers['location'], headers=h).json() == made.json()


@pytest.mark.parametrize('collection', ['Users', 'Groups'])
def test_matching_is_provider_scoped_even_for_identical_external_ids(http, collection):
    client, db, owner, h, base, pid = http
    first = {**user(), 'externalId': 'same'} if collection == 'Users' else group(externalId='same')
    assert client.post(base + '/' + collection, headers=h, json=first).status_code == 201
    other = client.post('/v1/admin/scim-providers/acme', headers=owner, json={'label': 'Other'})
    p = other.json(); oid = p['provider']['provider_id']
    assert client.patch('/v1/admin/scim-providers/acme/' + oid, headers=owner, json={'revision': 1, 'enabled': True}).status_code == 200
    oh = {**h, 'Authorization': 'Bearer ' + p['token']}
    # The selected query can never return the first provider's rows.
    text = 'externalId eq "same" and externalId eq "same"'
    assert search(client, oh, '/scim/v2/' + oid, collection, filter=text).json()['totalResults'] == 0
    payload = {**user(), 'userName': 'other@acme.test', 'externalId': 'same'} if collection == 'Users' else group(externalId='same')
    assert client.post('/scim/v2/' + oid + '/' + collection, headers=oh, json=payload).status_code == 201
    assert search(client, oh, '/scim/v2/' + oid, collection, filter=text).json()['totalResults'] == 1
    assert client.post('/v1/admin/scim-providers/acme/' + oid + '/revoke', headers=owner, json={'revision': 2}).status_code == 200
    assert search(client, oh, '/scim/v2/' + oid, collection, filter=text).status_code == 401
