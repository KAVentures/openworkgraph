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
