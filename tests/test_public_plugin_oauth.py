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
    assert len(tools) == 10
    assert all(tool['annotations']['readOnlyHint'] for tool in tools)
    profile = rpc(client, mint(), 'tools/call', {'name': 'get_profile', 'arguments': {}})
    assert profile.status_code == 200, profile.text
    assert not profile.json()['result'].get('isError')
    assert profile.json()['result']['structuredContent']['id'].startswith('prf_')


def test_public_tools_have_relevant_implicit_triggers_and_retain_read_only_boundary(public_client):
    client, mint = public_client
    response = rpc(client, mint(), 'tools/list')
    assert response.status_code == 200, response.text
    tools = {item['name']: item for item in response.json()['result']['tools']}
    assert set(tools) == {
        'get_profile', 'get_current_work_context', 'get_goal_context', 'search_work',
        'get_workflow_trace', 'find_repeated_workflows',
        'get_workflow_evidence', 'get_agent_runs',
        'search_work_text', 'get_work_text_excerpt',
    }
    for tool in tools.values():
        assert tool['annotations']['readOnlyHint'] is True
        assert tool['annotations']['destructiveHint'] is False
        assert tool['annotations']['openWorldHint'] is False
        assert tool['description'].strip()
    assert 'resume what I was doing' in tools['get_current_work_context']['description']
    assert 'without naming OpenWorkGraph' in tools['search_work']['description']
    assert 'previously observed human or agent work' in tools['get_goal_context']['description']
    assert 'usual or repeated real work' in tools['find_repeated_workflows']['description']
    assert 'not a verified task or completion status' in tools['get_current_work_context']['description']


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
    assert all('metadata' not in item for item in data['rows'])
    assert 'opaque_2' not in response.text

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
    assert data['returned'] == 15  # compact hosted default; caller can request up to 200
    assert data['rows'][0]['event_id'] == 'long-history-073'
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




def test_continuity_graph_and_raw_trace_respect_gateway_retention(public_client):
    from datetime import datetime, timedelta, timezone
    from gateway.lifecycle import set_retention_policy
    client, mint = public_client
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=5)
    db = _insert_personal_events([
        {
            'event_id': 'expired-reference',
            'observed_at': old.isoformat(), 'source': 'browser',
            'event_type': 'navigate',
            'metadata': {'resource_reference': {
                'resource_ref': 'owg:r:retention-expired', 'provider': 'test',
                'resource_kind': 'record',
            }},
        },
        {
            'event_id': 'retained-reference',
            'observed_at': (now - timedelta(minutes=2)).isoformat(),
            'source': 'browser', 'event_type': 'navigate',
            'metadata': {'resource_reference': {
                'resource_ref': 'owg:r:retention-visible', 'provider': 'test',
                'resource_kind': 'record',
            }},
        },
    ])
    set_retention_policy(db, 'oauth-sub:test-person', 1)
    current = rpc(client, mint(), 'tools/call', {
        'name': 'get_current_work_context', 'arguments': {'limit': 10},
    })
    assert current.status_code == 200, current.text
    data = current.json()['result']['structuredContent']
    assert data['returned'] == 1
    assert data['rows'][0]['event_id'] == 'retained-reference'
    assert {r['resource_ref'] for r in data['continuity_context']['resources']} == {'owg:r:retention-visible'}
    assert 'retention-expired' not in current.text
    full = rpc(client, mint(), 'tools/call', {
        'name': 'get_workflow_trace', 'arguments': {'limit': 10},
    })
    assert full.status_code == 200, full.text
    assert [r['event_id'] for r in full.json()['result']['structuredContent']['rows']] == ['retained-reference']




def test_hosted_poison_metadata_redacted_across_read_tools(public_client):
    """A paired device may send richer agent metadata; none must reach the AI."""
    from datetime import datetime, timezone
    client, mint = public_client
    secret_prompt = 'Internal agent prompt: answer using undisclosed patient billing information.'
    person = 'Anna Svensson'
    email = 'anna.svensson@customer.example'
    db = _insert_personal_events([{
        'event_id': 'poison-agent-1',
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'source': 'agent', 'event_type': 'agent_tool_call',
        'session_id': 'agent-run-secret',
        'app': 'Agent', 'window_title': f'Email to {person} <{email}>',
        'metadata': {
            'prompt': secret_prompt, 'input': secret_prompt,
            'tool_output': secret_prompt, 'customer_email': email,
            'person_name': person,
            'action': 'open', 'observation_level': 'structured',
            'target': {'role': 'button', 'label': f'Email {person} at {email}'},
            'page': {'hostname': 'example.com', 'pathname': '/customer/anna-svensson'},
            'resource_reference': {
                'resource_ref': 'owg:r:account-safe', 'provider': 'example',
                'resource_kind': 'record', 'resolver_locator': f'https://example.com/{email}',
            },
        },
    }])
    # The canonical Gateway store remains unchanged; only AI-facing copies
    # are sanitized. This also guards against rich metadata ingested in the past.
    stored = db.trace_rows(organization_id='oauth-sub:test-person', actor_id='oauth-sub:test-person')
    assert stored[0]['metadata_json'].find(secret_prompt) != -1

    calls = [
        ('get_current_work_context', {}),
        ('get_current_work_context', {'detail': 'rich'}),
        ('search_work', {'query': 'Email to'}),
        ('get_goal_context', {'goal': 'Find the previous email account workflow'}),
        ('get_workflow_trace', {'detail': 'rich'}),
        ('get_agent_runs', {}),
        ('find_repeated_workflows', {}),
    ]
    for tool, args in calls:
        response = rpc(client, mint(), 'tools/call', {'name': tool, 'arguments': args})
        assert response.status_code == 200, (tool, response.text)
        assert not response.json()['result'].get('isError'), (tool, response.text)
        assert secret_prompt not in response.text, tool
        assert email not in response.text, tool
        assert person not in response.text, tool
        assert 'anna-svensson' not in response.text, tool

    rich = rpc(client, mint(), 'tools/call', {
        'name': 'get_workflow_trace', 'arguments': {'detail': 'rich'},
    }).json()['result']['structuredContent']
    row = rich['rows'][0]
    assert row['event_id'] == 'poison-agent-1'
    assert row['metadata']['target']['role'] == 'button'
    assert row['metadata']['resource_reference']['resource_ref'] == 'owg:r:account-safe'
    assert 'prompt' not in row['metadata']
    assert 'resolver_locator' not in row['metadata']['resource_reference']
    assert 'pathname' not in row['metadata']['page']
    assert 'EMAIL_' in row['window_title']
    assert rich['privacy_representation']['level'] == 'hosted_redacted'
    assert rich['evidence_contract']['metadata_preserved'] is False



def test_workflow_evidence_projection_cannot_reveal_nested_poison_content():
    from gateway.plugin_redaction import project
    raw = {
        'selector': {'family_key': 'human:test', 'selected_execution_count': 1},
        'canonical_evidence': [{
            'execution_id': 'execution:abc123def4567890',
            'events': [{
                'event_id': 'nested-1', 'observed_at': '2026-10-09T10:00:00+00:00',
                'event_type': 'click', 'app': 'Browser',
                'window_title': 'Email Anna Svensson <anna.svensson@customer.example>',
                'metadata': {
                    'prompt': 'my sensitive private agent prompt',
                    'target': {'role': 'button', 'label': 'Open quote'},
                },
            }],
        }],
    }
    for detail in ('compact', 'rich'):
        projected = project(raw, secret='test-server-only-key', principal='user-a', detail=detail)
        serialized = __import__('json').dumps(projected)
        assert 'my sensitive private agent prompt' not in serialized
        assert 'anna.svensson@customer.example' not in serialized
        assert 'Anna Svensson' not in serialized
        assert projected['canonical_evidence'][0]['events'][0]['event_id'] == 'nested-1'



def test_default_compact_hosted_response_bounded_with_rich_opt_in(public_client):
    from datetime import datetime, timedelta, timezone
    client, mint = public_client
    base = datetime.now(timezone.utc) - timedelta(minutes=14)
    events = [{
        'event_id': f'compact-{i:03d}',
        'observed_at': (base + timedelta(seconds=i)).isoformat(),
        'source': 'browser', 'event_type': 'click',
        'app': 'Browser', 'window_title': 'Gmail compose customer quote',
        'session_id': 'compact-session',
        'metadata': {
            'action': 'open', 'target': {'role': 'button', 'label': 'Open quote'},
            'page': {'hostname': 'example.com', 'pathname': '/long/path'},
            'irrelevant_blob': 'c' * 2000,
            'click_count': 0, 'keypress_count': 0, 'scroll_count': 0,
        },
    } for i in range(114)]
    _insert_personal_events(events)
    compact = rpc(client, mint(), 'tools/call', {
        'name': 'get_current_work_context', 'arguments': {},
    })
    assert compact.status_code == 200, compact.text
    data = compact.json()['result']['structuredContent']
    assert data['returned'] == 15
    assert len(data['rows']) == 15
    assert all('metadata' not in x for x in data['rows'])
    assert all('irrelevant_blob' not in str(x) for x in data['rows'])
    assert all('click_count' not in str(x) for x in data['rows'])
    assert data['continuity_context']['coverage']['recent_rows_scanned'] == 114
    assert len(__import__('json').dumps(data)) < 18000

    compact_trace = rpc(client, mint(), 'tools/call', {
        'name': 'get_workflow_trace', 'arguments': {},
    })
    trace_data = compact_trace.json()['result']['structuredContent']
    assert trace_data['returned'] == 25
    assert 'metadata' not in trace_data['rows'][0]
    rich_trace = rpc(client, mint(), 'tools/call', {
        'name': 'get_workflow_trace', 'arguments': {'detail': 'rich', 'limit': 25},
    })
    assert rich_trace.status_code == 200
    rich_data = rich_trace.json()['result']['structuredContent']
    assert rich_data['rows'][0]['metadata']['target']['label'] == 'Open quote'
    assert 'irrelevant_blob' not in rich_trace.text
    assert 'pathname' not in rich_trace.text
    assert len(__import__('json').dumps(trace_data)) < len(__import__('json').dumps(rich_data))
    # Tool names, pagination and canonical event identity are unchanged.
    assert trace_data['has_more'] and trace_data['next_cursor']
    assert [row['event_id'] for row in trace_data['rows']] == [
        row['event_id'] for row in rich_data['rows']
    ]



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


def test_account_cloud_text_delete_is_oauth_scoped_and_blocks_stale_desktop_uploads(public_client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from gateway.db import GatewayDB
    from gateway import work_text_cloud as cloud
    import os

    client, mint = public_client
    monkeypatch.setenv("OWG_CLOUD_WORK_TEXT_ENABLED", "1")
    db = GatewayDB(os.environ["OWG_GATEWAY_DATABASE_URL"])
    db.init()
    cloud.init_schema(db)
    org = "oauth-sub:test-person"
    device_id = "this-person-desktop"
    db.put_token(token_id="text-device", token="cloud-test-device", token_type="device",
                 organization_id=org, actor_id=org, device_id=device_id,
                 scopes={"evidence:write"})
    credential = db.authenticate("cloud-test-device")
    now = datetime.now(timezone.utc)
    grant = (now - timedelta(minutes=2)).isoformat()
    payload = {
        "items": [{
            "ref": "owg:wt:1", "observed_at": now.isoformat(),
            "hostname": "docs.example.org", "page_title": "Planning",
            "kind": "page", "redacted_text": "Synthetic project planning meeting",
        }],
        "active_refs": ["owg:wt:1"], "granted_at": grant,
        "consent_version": cloud.CONSENT_VERSION,
    }
    # A linked device token cannot invent account consent by supplying the
    # expected version or an arbitrary timestamp. OAuth action is separate.
    with pytest.raises(PermissionError, match="OAuth cloud work-text approval required"):
        cloud.sync(db, credential, **payload)
    # The OAuth account cannot approve all linked devices implicitly.
    assert client.post("/v1/plugin/work-text-approve", headers={
        "Authorization": f"Bearer {mint()}",
        "X-OWG-Consent-Version": cloud.CONSENT_VERSION,
    }).status_code == 400
    assert client.post("/v1/plugin/work-text-approve", headers={
        "Authorization": f"Bearer {mint(sub='other-user')}",
        "X-OWG-Consent-Version": cloud.CONSENT_VERSION,
        "X-OWG-Device-ID": device_id,
    }).status_code == 403
    approved = client.post(
        "/v1/plugin/work-text-approve",
        headers={"Authorization": f"Bearer {mint()}",
                 "X-OWG-Consent-Version": cloud.CONSENT_VERSION,
                 "X-OWG-Device-ID": device_id},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["device_id"] == device_id
    assert cloud.sync(db, credential, **payload)["uploaded"] == 1
    assert len(cloud.search(db, organization_id=org, actor_id=org, query="planning")["results"]) == 1

    url = "/v1/plugin/work-text-delete"
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"Authorization": f"Bearer {mint(scope='email')}"}).status_code == 401
    assert client.post(url, headers={"Authorization": f"Bearer {mint(sub='different-person')}"}).json()["deleted"] == 0
    assert len(cloud.search(db, organization_id=org, actor_id=org, query="planning")["results"]) == 1
    mapped = mint(owg_org_id="company", owg_actor_id="employee")
    assert client.post(url, headers={"Authorization": f"Bearer {mapped}"}).status_code == 409

    deleted = client.post(url, headers={"Authorization": f"Bearer {mint()}"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] == 1
    assert deleted.json()["reconsent_required"] is True
    assert cloud.search(db, organization_id=org, actor_id=org, query="planning")["status"] == "not_enabled"

    with pytest.raises(PermissionError, match="deleted"):
        cloud.sync(db, credential, **payload)
    # Even a future-skewed device clock cannot bypass the account lock.
    later = {**payload, "items": [], "active_refs": [],
             "granted_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}
    with pytest.raises(PermissionError, match="OAuth reauthorization"):
        cloud.sync(db, credential, **later)
    reauth = "/v1/plugin/work-text-reauthorize"
    headers = {"Authorization": f"Bearer {mint()}", "X-OWG-Device-ID": device_id}
    assert client.post(reauth, headers=headers).status_code == 403
    headers["X-OWG-Consent-Version"] = cloud.CONSENT_VERSION
    assert client.post(reauth, headers=headers).json()["status"] == "device_approved"
    # An OAuth approval does not erase the hard deletion tombstone. Replaying
    # the pre-deletion desktop grant after reapproval must still fail.
    with pytest.raises(PermissionError, match="fresh local grant"):
        cloud.sync(db, credential, **payload)
    # A new local grant, with its own distinct epoch, is required.
    assert cloud.sync(db, credential, **later)["status"] == "synced"


def test_goal_context_mcp_scopes_goal_evidence_and_does_not_invent_task_success(public_client):
    from datetime import datetime, timedelta, timezone
    from gateway.auth import Principal
    from gateway.db import GatewayDB
    import os

    client, mint = public_client
    db = GatewayDB(os.environ["OWG_GATEWAY_DATABASE_URL"])
    now = datetime.now(timezone.utc)
    org = "oauth-sub:test-person"
    person = Principal("device", "device", org, org, "device", frozenset())
    foreign = Principal("device", "device", org, "not-this-actor", "device", frozenset())

    good = [
        {
            "event_id": "goal-inbox",
            "observed_at": (now - timedelta(minutes=9)).isoformat(),
            "session_id": "goal-session", "app": "Gmail", "source": "browser",
            "window_title": "Gmail inbox", "event_type": "screen_click",
        },
        {
            "event_id": "goal-quotation",
            "observed_at": (now - timedelta(minutes=8)).isoformat(),
            "session_id": "goal-session", "app": "Google Chrome",
            "source": "browser", "event_type": "screen_click",
            "window_title": "Customer quotation spreadsheet",
            "metadata": {
                "prompt": "DO_NOT_DISCLOSE_PROMPT",
                "resource_reference": {
                    "provider": "google_drive", "resource_kind": "spreadsheet",
                    "resource_ref": "owg:r:goal-quote",
                    "resolver_locator": "https://internal.example/secret-account",
                },
            },
        },
        {
            "event_id": "goal-crm",
            "observed_at": (now - timedelta(minutes=7)).isoformat(),
            "session_id": "goal-session", "app": "Salesforce",
            "source": "browser", "window_title": "Customer record",
            "event_type": "screen_click",
        },
    ]
    db.insert_events(person, good)
    db.insert_events(foreign, [{
        "event_id": "goal-foreign",
        "observed_at": (now - timedelta(minutes=8)).isoformat(),
        "source": "browser", "window_title": "Customer quotation secret",
        "event_type": "screen_click",
    }])
    reply = rpc(client, mint(), "tools/call", {
        "name": "get_goal_context",
        "arguments": {"goal": "Prepare customer quotation", "detail": "rich"},
    })
    assert reply.status_code == 200, reply.text
    assert not reply.json()["result"].get("isError"), reply.text
    data = reply.json()["result"]["structuredContent"]
    assert data["status"] == "possible_matches"
    ids = [x["anchor_event_id"] for x in data["examples"]]
    assert "goal-quotation" in ids
    assert "goal-foreign" not in reply.text
    assert "DO_NOT_DISCLOSE_PROMPT" not in reply.text
    assert "secret-account" not in reply.text
    assert data["examples"][0]["task_identity_confirmed"] is False
    assert data["examples"][0]["outcome_verified"] is False
    assert data["fallback"]["omit_query"] is True
    assert data["privacy_representation"]["level"] == "hosted_redacted"

    wrong_person = rpc(client, mint(sub="somebody-else"), "tools/call", {
        "name": "get_goal_context", "arguments": {"goal": "customer quotation"}
    })
    assert wrong_person.status_code == 200, wrong_person.text
    assert wrong_person.json()["result"]["structuredContent"]["status"] == "no_synced_evidence"
    assert "goal-quotation" not in wrong_person.text
    assert rpc(client, None, "tools/call", {
        "name": "get_goal_context", "arguments": {"goal": "customer quotation"}
    }).status_code == 401


def test_goal_context_empty_mcp_response_includes_examples_and_raw_fallback(public_client):
    client, mint = public_client
    reply = rpc(client, mint(), "tools/call", {
        "name": "get_goal_context",
        "arguments": {"goal": "Plan a quotation workflow from previous work"},
    })
    assert reply.status_code == 200
    data = reply.json()["result"]["structuredContent"]
    assert data["status"] == "no_synced_evidence"
    assert data["examples"] == []
    assert data["fallback"]["omit_query"] is True


def test_numeric_search_cannot_match_internal_ids_or_partial_pr_numbers(public_client):
    from datetime import datetime, timedelta, timezone
    from gateway.auth import Principal
    from gateway.db import GatewayDB
    import os
    client, mint = public_client
    db = GatewayDB(os.environ['OWG_GATEWAY_DATABASE_URL'])
    org = 'oauth-sub:test-person'
    p = Principal('device', 'device', org, org, 'device', frozenset())
    now = datetime.now(timezone.utc)
    db.insert_events(p, [dict(event_id=identity, observed_at=(now-timedelta(minutes=5-i)).isoformat(),
        app='Chrome', source='browser', event_type='screen_click', window_title=title,
        metadata={'internal_note':'183', 'target':{'label':'Open'}})
        for i,(identity,title) in enumerate([('uuid-183-internal','PR 180'),('second','PR 1830'),('real','PR 183')])])
    reply = rpc(client, mint(), 'tools/call', {'name':'search_work','arguments':{'query':'183','limit':1}})
    result = reply.json()['result']['structuredContent']
    assert [r['event_id'] for r in result['rows']] == ['real']
    assert result['rows'][0]['matched_fields'] == ['window_title']
    assert result['search_coverage']['candidate_rows_scanned'] == 3


def test_context_reports_server_catalog_and_unknown_local_capture_state(public_client):
    client,mint=public_client
    result=rpc(client,mint(),'tools/call',{'name':'get_current_work_context','arguments':{}}).json()['result']['structuredContent']
    assert result['server_capabilities']['tool_count'] == 10
    assert result['capture_status']['local_recorder_state'] == 'unknown_to_hosted_connector'
    assert result['capture_status']['latest_observed_at'] is None
    assert result['capture_status']['cloud_work_text']['status'] == 'operator_disabled'


def test_local_capture_to_authenticated_cloud_excerpt_keeps_page_and_coverage(public_client, monkeypatch, tmp_path):
    from gateway import work_text_cloud as cloud
    from gateway.db import GatewayDB
    from server import work_text_capture as wt, ai_access, capture_exclusions
    from shared import capture_control
    import os
    client,mint=public_client
    monkeypatch.setenv('OWG_CLOUD_WORK_TEXT_ENABLED','1')
    monkeypatch.setattr(wt,'_POLICY',tmp_path/'text-policy.json')
    monkeypatch.setattr(wt,'_DB',tmp_path/'local-text.db')
    monkeypatch.setattr(ai_access,'ai_access_enabled',lambda:True)
    monkeypatch.setattr(capture_control,'read_state',lambda:{'state':'recording'})
    monkeypatch.setattr(capture_exclusions,'current',lambda:{'hosts':[],'title_words':[],'apps':[],'defaults':{}})
    db=GatewayDB(os.environ['OWG_GATEWAY_DATABASE_URL'])
    org='oauth-sub:test-person'
    db.put_token(token_id='text-device', token='synthetic-secret',token_type='device',
        organization_id=org,actor_id=org,device_id='synthetic-device',scopes={'evidence:write'})
    wt.set_policy({'capture_enabled':True,'ai_read_enabled':True})
    wt.ingest({'hostname':'docs.example.org','pathname':'/private-project','title':'Planning',
        'kind':'page','text':'Old local-only planning'})
    wt.set_policy({'cloud_read_enabled':True,'cloud_ack_version':wt.CLOUD_ACK_VERSION})
    wt.ingest({'hostname':'docs.example.org','pathname':'/private-project','title':'Planning',
        'kind':'page','text':'Planning '+('bounded content '*220),
        'context':{'selection':'viewport_then_page','capture_truncated':True,'inspected_characters':9000}})
    snapshot=wt.cloud_sync_snapshot()
    assert len(snapshot['items']) == 1
    approved=client.post('/v1/plugin/work-text-approve',headers={'Authorization':'Bearer '+mint(),
        'X-OWG-Device-ID':'synthetic-device','X-OWG-Consent-Version':cloud.CONSENT_VERSION})
    assert approved.status_code == 200
    principal=db.authenticate('synthetic-secret')
    cloud.sync(db,principal,**snapshot,active_refs=[item['ref'] for item in snapshot['items']])
    def tool(name,arguments):
        result=rpc(client,mint(),'tools/call',{'name':name,'arguments':arguments}).json()['result']
        assert not result.get('isError')
        return result['structuredContent']
    result=tool('search_work_text',{'query':'planning'})
    assert len(result['results']) == 1
    context=result['results'][0]['context']
    assert context['capture_truncated'] is True and context['sync_truncated'] is True
    joined=tool('search_work_text',{'query':context['page_ref']})
    assert joined['results'][0]['reference'] == result['results'][0]['reference']
    excerpt=tool('get_work_text_excerpt',{'reference':joined['results'][0]['reference']})
    assert excerpt['excerpt']['context']['page_ref'] == context['page_ref']
    assert 'Old local-only' not in str(excerpt) and '/private-project' not in str(excerpt)
    assert len(excerpt['excerpt']['redacted_text']) <= 1220
    assert tool('get_current_work_context',{})['capture_status']['cloud_work_text']['status'] == 'ready'
    assert client.post('/v1/plugin/work-text-delete',headers={'Authorization':'Bearer '+mint()}).status_code == 200
    assert tool('get_work_text_excerpt',{'reference':joined['results'][0]['reference']})['status'] == 'not_enabled'


def test_cloud_account_screen_is_scoped_and_nonce_protected(public_client, monkeypatch):
    from gateway.db import GatewayDB
    import os
    client, mint = public_client
    shell=client.get('/work-text')
    assert shell.status_code == 200
    assert shell.headers['cache-control'] == 'no-store'
    assert "frame-ancestors 'none'" in shell.headers['content-security-policy']
    assert '__NONCE__' not in shell.text and '__CONFIG__' not in shell.text
    assert 'example.supabase.co/auth/v1' in shell.text
    path='/v1/plugin/work-text-status'
    assert client.get(path).status_code == 401
    assert client.get(path,headers={'Authorization':'Bearer '+mint(scope='email')}).status_code == 401
    db=GatewayDB(os.environ['OWG_GATEWAY_DATABASE_URL'])
    for actor,device in [('oauth-sub:test-person','my-computer'),('oauth-sub:foreign','foreign-computer')]:
        db.put_token(token_id=device,token=device,token_type='device',organization_id=actor,actor_id=actor,
            device_id=device,scopes={'evidence:write'})
    response=client.get(path,headers={'Authorization':'Bearer '+mint()})
    assert response.status_code == 200
    assert response.json()['devices'] == [{'device_id':'my-computer','approved':False}]
    assert 'foreign' not in response.text
    assert response.json()['operator_available'] is False


def test_authenticated_owner_profile_and_window_titles_share_redaction_policy(public_client):
    """Synthetic OAuth identity never leaks in either ChatGPT or Claude transport."""
    from datetime import datetime, timezone

    client, mint = public_client
    token = mint(name="Uniqueowner Surname", given_name="Uniqueowner",
                 email="uniqueowner.surname@example.invalid")
    profile = rpc(client, token, "tools/call", {
        "name": "get_profile", "arguments": {},
    })
    assert profile.status_code == 200, profile.text
    info = profile.json()["result"]["structuredContent"]
    assert info["id"].startswith("prf_")
    assert info["name"].startswith("OWNER_")
    assert info["email"] is None
    _insert_personal_events([{
        "event_id": "owner-title-1", "observed_at": datetime.now(timezone.utc).isoformat(),
        "app": "Chrome", "window_title": "Chrome – Uniqueowner",
        "event_type": "focus_span", "session_id": "owner-title-s1", "source": "desktop",
    }])
    trace = rpc(client, token, "tools/call", {
        "name": "get_workflow_trace", "arguments": {"limit": 10},
    })
    assert trace.status_code == 200, trace.text
    row = next(item for item in trace.json()["result"]["structuredContent"]["rows"]
               if item["event_id"] == "owner-title-1")
    assert row["window_title"] == "Chrome – " + info["name"]
    assert "Uniqueowner" not in trace.text


def test_minimal_detail_is_additive_and_preserves_cursor(public_client):
    from datetime import datetime, timezone
    client, mint = public_client
    token = mint()
    _insert_personal_events([{
        "event_id": f"minimal-{i}",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "app": "Editor", "window_title": "Synthetic test",
        "event_type": "focus_span", "source": "desktop",
    } for i in range(3)])
    response = rpc(client, token, "tools/call", {
        "name": "get_workflow_trace", "arguments": {"limit": 1, "detail": "minimal"},
    })
    assert response.status_code == 200, response.text
    data = response.json()["result"]["structuredContent"]
    assert data["returned"] == 1
    assert data["has_more"] is True
    assert data["next_cursor"]
    assert data["contract_version"] == "hosted-evidence-v3"
    assert "privacy_representation" not in data
    follow = rpc(client, token, "tools/call", {
        "name": "get_workflow_trace",
        "arguments": {"cursor": data["next_cursor"], "limit": 2, "detail": "minimal"},
    })
    assert follow.status_code == 200, follow.text
    assert follow.json()["result"]["structuredContent"]["rows"]
