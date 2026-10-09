"""Exercise OAuth through the real Streamable HTTP authentication middleware."""
import time
from types import SimpleNamespace

import pytest
jwt = pytest.importorskip('jwt')
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from starlette.testclient import TestClient
from gateway import public_plugin_mcp as plugin


@pytest.fixture(params=["ES256", "RS256"])
def public_client(tmp_path, monkeypatch, request):
    values = {
        'OWG_GATEWAY_DATABASE_URL': f'sqlite:///{tmp_path}/gateway.db',
        'OWG_PLUGIN_OAUTH_ISSUER': 'https://example.supabase.co/auth/v1',
        'OWG_PLUGIN_RESOURCE_URL': 'https://mcp.owg.kinvectum.com/mcp',
        'OWG_PLUGIN_OAUTH_JWKS_URL': 'https://example.supabase.co/auth/v1/.well-known/jwks.json',
        'OWG_PLUGIN_OAUTH_ALGORITHMS': request.param,
        'OWG_PLUGIN_TOKEN_AUDIENCE': 'authenticated',
        'OWG_PLUGIN_REQUIRED_SCOPE': 'openid',
        'OWG_PLUGIN_PROFILE_KEY': 'isolated-test-profile-key',
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    key = (ec.generate_private_key(ec.SECP256R1()) if request.param == "ES256"
           else rsa.generate_private_key(public_exponent=65537, key_size=2048))
    monkeypatch.setattr(jwt.PyJWKClient, 'get_signing_key_from_jwt', lambda self, token: SimpleNamespace(key=key.public_key()))
    def mint(**changes):
        claims = dict(iss=values['OWG_PLUGIN_OAUTH_ISSUER'], aud='authenticated',
                      sub='test-person', scope='openid', exp=int(time.time()) + 600, client_id='test-client')
        claims.update(changes)
        return jwt.encode(claims, key, algorithm=request.param)
    with TestClient(plugin.create_app(), base_url="https://mcp.owg.kinvectum.com") as client:
        yield client, mint


def rpc(client, token, method, params=None):
    headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
    if token is not None:
        headers['Authorization'] = f'Bearer {token}'
    return client.post('/mcp', headers=headers, json={
        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {},
    })


def test_supabase_audience_reaches_mcp_tools(public_client):
    client, mint = public_client
    response = rpc(client, mint(), 'tools/list')
    assert response.status_code == 200, response.text
    tools = response.json()['result']['tools']
    assert len(tools) == 7
    assert all(tool['annotations']['readOnlyHint'] for tool in tools)
    profile = rpc(client, mint(), 'tools/call', {'name': 'get_profile', 'arguments': {}})
    assert profile.status_code == 200, profile.text
    assert not profile.json()['result'].get('isError')
    assert profile.json()['result']['structuredContent']['id'].startswith('prf_')


@pytest.mark.parametrize('changes', [
    {'aud': 'another-project'}, {'iss': 'https://untrusted.example'},
    {'scope': 'email'}, {'exp': 1}, {'sub': ''},
])
def test_invalid_claims_still_rejected(public_client, changes):
    client, mint = public_client
    assert rpc(client, mint(**changes), 'tools/list').status_code == 401


def test_unauthenticated_requests_rejected(public_client):
    client, _ = public_client
    response = rpc(client, None, 'tools/list')
    assert response.status_code == 401
    assert 'oauth-protected-resource' in response.headers['www-authenticate']



def test_both_public_metadata_discovery_locations_return_same_document(public_client):
    client, _mint = public_client
    canonical = client.get('/.well-known/oauth-protected-resource/mcp')
    root_alias = client.get('/.well-known/oauth-protected-resource')
    assert canonical.status_code == 200, canonical.text
    assert root_alias.status_code == 200, root_alias.text
    assert canonical.headers['content-type'].startswith('application/json')
    assert root_alias.json() == canonical.json()
    metadata = root_alias.json()
    assert metadata['resource'] == 'https://mcp.owg.kinvectum.com/mcp'
    assert metadata['authorization_servers'] == ['https://example.supabase.co/auth/v1']
    assert 'openid' in metadata['scopes_supported']
    challenge = rpc(client, None, 'tools/list')
    assert challenge.status_code == 401
    assert 'resource_metadata=' in challenge.headers['www-authenticate']
    # Metadata is public; credentials and history remain behind OAuth.
    assert 'test-person' not in root_alias.text
    assert client.post('/.well-known/oauth-protected-resource').status_code != 200


def test_disconnected_chatgpt_has_optional_onboarding_without_device_enrollment(public_client):
    client, mint = public_client
    response = rpc(client, mint(), 'tools/call', {
        'name': 'get_current_work_context', 'arguments': {},
    })
    assert response.status_code == 200, response.text
    result = response.json()['result']['structuredContent']
    assert result['returned'] == 0
    assert result['orientation']['recent_canonical_evidence_available'] is False
    assert result['onboarding']['status'] == 'no_synced_evidence'
    assert result['onboarding']['desktop_recorder_optional'] is True
    assert result['onboarding']['standalone_local_use_available'] is True
    assert result['onboarding']['history_shared_automatically'] is False
    assert result['onboarding']['desktop_install_url'] == 'https://owg.kinvectum.com/connect'
    assert 'enrollment_token' not in response.text


def test_public_host_guard_remains_enabled(public_client):
    client, mint = public_client
    response = client.post('/mcp', headers={
        'Authorization': f'Bearer {mint()}', 'Host': 'attacker.example',
        'Accept': 'application/json, text/event-stream',
    }, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert response.status_code == 421


@pytest.mark.parametrize(('source', 'event_type', 'expected'), [
    ('agent', 'tool_call', True), ('desktop', 'agent_tool_call', True),
    ('desktop', 'window_focus', False),
])
def test_agent_orientation_uses_canonical_fields(public_client, source, event_type, expected):
    import os
    from gateway.auth import Principal
    from gateway.db import GatewayDB
    client, mint = public_client
    db = GatewayDB(os.environ['OWG_GATEWAY_DATABASE_URL'])
    principal = Principal('device', 'device', 'oauth-sub:test-person', 'oauth-sub:test-person', 'device', frozenset())
    db.insert_events(principal, [{'event_id': 'event-1', 'source': source, 'event_type': event_type,
                                  'session_id': 'session-1', 'metadata': {}}])
    response = rpc(client, mint(), 'tools/call', {'name': 'get_current_work_context', 'arguments': {}})
    assert response.status_code == 200, response.text
    assert response.json()['result']['structuredContent']['orientation']['nearby_agent_runs_available'] is expected
    assert response.json()['result']['structuredContent']['onboarding'] is None



def _insert_personal_events(events):
    import os
    from gateway.auth import Principal
    from gateway.db import GatewayDB
    db = GatewayDB(os.environ['OWG_GATEWAY_DATABASE_URL'])
    principal = Principal('device', 'device', 'oauth-sub:test-person', 'oauth-sub:test-person',
                          'device', frozenset())
    inserted, _ack = db.insert_events(principal, events)
    assert inserted == len(events)
    return db


def test_raw_evidence_is_returned_even_when_inference_finds_no_resource(public_client):
    from datetime import datetime, timedelta, timezone
    client, mint = public_client
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    events = [
        {
            'event_id': f'raw-no-ref-{i:02d}',
            'observed_at': (base + timedelta(seconds=i)).isoformat(),
            'session_id': 'uncategorized',
            'app': 'Browser', 'window_title': f'Unclassified operation {i}',
            'source': 'browser', 'event_type': 'click',
            'metadata': {'action': 'open', 'safe_context': f'opaque_{i}'},
        }
        for i in range(3)
    ]
    _insert_personal_events(events)
    response = rpc(client, mint(), 'tools/call', {
        'name': 'get_current_work_context', 'arguments': {'limit': 2},
    })
    assert response.status_code == 200, response.text
    data = response.json()['result']['structuredContent']
    assert data['returned'] == 2
    assert [row['event_id'] for row in data['rows']] == ['raw-no-ref-01', 'raw-no-ref-02']
    assert data['continuity_context']['resources'] == []
    assert data['continuity_context']['coverage']['recent_rows_scanned'] == 3
    assert data['orientation']['resource_candidates_available'] is False
    assert data['orientation']['recent_canonical_evidence_available'] is True
    assert data['raw_evidence_fallback']['no_inferred_task_required'] is True
    assert data['raw_evidence_fallback']['zero_matches_prove_absence'] is False
    assert data['onboarding'] is None
    assert data['rows'][-1]['metadata']['safe_context'] == 'opaque_2'

    no_match = rpc(client, mint(), 'tools/call', {
        'name': 'search_work', 'arguments': {'query': 'intentionally_no_match'},
    })
    assert no_match.status_code == 200
    assert no_match.json()['result']['structuredContent']['returned'] == 0
    assert no_match.json()['result']['structuredContent']['raw_evidence_fallback']['omit_query_to_include_unmatched_events'] is True
    repeated = rpc(client, mint(), 'tools/call', {
        'name': 'find_repeated_workflows', 'arguments': {},
    })
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()['result']['structuredContent']['raw_evidence_fallback']['no_inferred_family_required'] is True

    trace = rpc(client, mint(), 'tools/call', {
        'name': 'get_workflow_trace', 'arguments': {'limit': 10},
    })
    assert trace.status_code == 200, trace.text
    assert [row['event_id'] for row in trace.json()['result']['structuredContent']['rows']] == [
        item['event_id'] for item in events
    ]


def test_conservative_graph_keeps_unrelated_raw_events_and_pair_uncertainty(public_client):
    from datetime import datetime, timedelta, timezone
    client, mint = public_client
    base = datetime.now(timezone.utc) - timedelta(minutes=5)
    refs = ['owg:r:example-quote', 'owg:r:example-order']
    events = [
        {
            'event_id': f'graph-source-{i}',
            'observed_at': (base + timedelta(seconds=i * 10)).isoformat(),
            'source': 'browser', 'event_type': 'navigate',
            'app': 'Browser',
            'window_title': 'CaseNebula934 Alpha',
            'session_id': 'two-window-session',
            'metadata': {'resource_reference': {
                'resource_ref': refs[i], 'provider': 'test', 'resource_kind': 'record',
            }, 'tab_context_id': 'tab_21'},
        }
        for i in range(2)
    ]
    events.insert(1, {
        'event_id': 'unlinked-middle-event',
        'observed_at': (base + timedelta(seconds=5)).isoformat(),
        'source': 'desktop', 'event_type': 'click', 'app': 'Other App',
        'window_title': 'Unlinked operation', 'session_id': 'two-window-session',
        'metadata': {'action': 'click'},
    })
    _insert_personal_events(events)
    result = rpc(client, mint(), 'tools/call', {
        'name': 'get_current_work_context', 'arguments': {'limit': 1},
    })
    assert result.status_code == 200, result.text
    data = result.json()['result']['structuredContent']
    assert data['returned'] == 1
    assert data['rows'][0]['event_id'] == 'graph-source-1'
    graph = data['continuity_context']
    assert {r['resource_ref'] for r in graph['resources']} == set(refs)
    assert len(graph['coverage']) > 3
    assert graph['association_contract']['same_task_identity_is_observed'] is False
    assert graph['association_contract']['temporal_proximity_proves_same_work'] is False
    assert all(edge['proves_same_work'] is False for edge in graph['resource_relations'])
    assert all(candidate['task_identity_inferred'] is False for candidate in graph['candidates'])
    # The unlinked event does not vanish when a graph is found: canonical trace
    # still includes it, without an inference-based filter.
    trace = rpc(client, mint(), 'tools/call', {
        'name': 'get_workflow_trace', 'arguments': {'limit': 20},
    })
    assert trace.status_code == 200
    rows = trace.json()['result']['structuredContent']['rows']
    assert {row['event_id'] for row in rows} == {item['event_id'] for item in events}


def test_overview_is_bounded_but_all_synced_evidence_is_pageable(public_client):
    from datetime import datetime, timedelta, timezone
    client, mint = public_client
    base = datetime.now(timezone.utc) - timedelta(minutes=20)
    events = [
        {
            'event_id': f'long-history-{i:03d}',
            'observed_at': (base + timedelta(seconds=i)).isoformat(),
            'source': 'desktop', 'event_type': 'window_focus', 'app': 'Editor',
            'window_title': f'Record batch {i}', 'session_id': 'batch',
            'metadata': {'row_marker': f'proof_{i}'},
        }
        for i in range(88)
    ]
    events[0]['metadata']['resource_reference'] = {
        'resource_ref': 'owg:r:old-relevant-record',
        'provider': 'test', 'resource_kind': 'record',
    }
    _insert_personal_events(events)
    data = rpc(client, mint(), 'tools/call', {
        'name': 'get_current_work_context', 'arguments': {},
    }).json()['result']['structuredContent']
    assert data['returned'] == 50  # existing default response contract
    assert data['rows'][0]['event_id'] == 'long-history-038'
    assert data['continuity_context']['coverage']['recent_rows_scanned'] == 88
    assert data['continuity_context']['resources'][0]['resource_ref'] == 'owg:r:old-relevant-record'
    assert data['evidence_window']['older_gateway_evidence_not_scanned'] is True
    cursor = None
    seen = []
    for _ in range(20):
        args = {'limit': 11}
        if cursor:
            args['cursor'] = cursor
        page = rpc(client, mint(), 'tools/call', {'name': 'get_workflow_trace', 'arguments': args})
        assert page.status_code == 200, page.text
        result = page.json()['result']['structuredContent']
        seen.extend(row['event_id'] for row in result['rows'])
        cursor = result['next_cursor']
        if not result['has_more']:
            break
        assert cursor
    assert seen == [row['event_id'] for row in events]
    # Lexical search can select one anchor. A *separate unfiltered query*
    # through the same bounded chronology returns surrounding actions.
    search = rpc(client, mint(), 'tools/call', {
        'name': 'get_workflow_trace',
        'arguments': {'query': 'Record batch 40', 'limit': 10},
    })
    assert search.status_code == 200, search.text
    assert [row['event_id'] for row in search.json()['result']['structuredContent']['rows']] == ['long-history-040']
    window = rpc(client, mint(), 'tools/call', {
        'name': 'get_workflow_trace',
        'arguments': {
            'since': (base + timedelta(seconds=39)).isoformat(),
            'until': (base + timedelta(seconds=41)).isoformat(),
            'limit': 10,
        },
    })
    assert window.status_code == 200, window.text
    assert [row['event_id'] for row in window.json()['result']['structuredContent']['rows']] == [
        'long-history-039', 'long-history-040', 'long-history-041',
    ]


def test_evidence_graph_and_raw_trace_remain_actor_scoped(public_client):
    import os
    from datetime import datetime, timezone
    from gateway.auth import Principal
    from gateway.db import GatewayDB
    client, mint = public_client
    db = GatewayDB(os.environ['OWG_GATEWAY_DATABASE_URL'])
    foreign = Principal('device', 'device', 'oauth-sub:test-person', 'other-actor', 'device', frozenset())
    db.insert_events(foreign, [{
        'event_id': 'foreign-event', 'observed_at': datetime.now(timezone.utc).isoformat(),
        'source': 'browser', 'event_type': 'navigate',
        'metadata': {'resource_reference': {'resource_ref': 'owg:r:foreign-private',
                                              'provider': 'test', 'resource_kind': 'record'}},
    }])
    current = rpc(client, mint(), 'tools/call', {
        'name': 'get_current_work_context', 'arguments': {},
    })
    assert current.status_code == 200
    value = current.json()['result']['structuredContent']
    assert value['returned'] == 0
    assert value['continuity_context']['resources'] == []
    assert 'foreign-private' not in current.text
    trace = rpc(client, mint(), 'tools/call', {
        'name': 'get_workflow_trace', 'arguments': {'limit': 10},
    })
    assert trace.status_code == 200
    assert trace.json()['result']['structuredContent']['returned'] == 0



def test_invalid_signature_and_symmetric_tokens_rejected(public_client):
    client, mint = public_client
    token = mint()
    header, payload, signature = token.split('.')
    forged = header + '.' + payload + '.' + ('A' if signature[0] != 'A' else 'B') + signature[1:]
    assert rpc(client, forged, 'tools/list').status_code == 401
    symmetric = jwt.encode({'iss': 'https://example.supabase.co/auth/v1', 'aud': 'authenticated',
                            'scope': 'openid', 'sub': 'test-person', 'exp': int(time.time()) + 600},
                           'test-only-symmetric-signing-key-32-bytes', algorithm='HS256')
    assert rpc(client, symmetric, 'tools/list').status_code == 401
