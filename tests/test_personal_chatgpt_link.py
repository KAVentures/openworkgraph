import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import HTTPException
from server import chatgpt_link as linking


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def flow(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/register'):
            return httpx.Response(201, json={'client_id': 'public-desktop-client'})
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={'access_token': 'test-access', 'refresh_token': 'never-stored'})
        assert str(request.url) == linking.PLUGIN + '/v1/plugin/device-link'
        assert request.headers['authorization'] == 'Bearer test-access'
        return httpx.Response(200, json={'enrollment_token': 'single-use-grant',
                                        'gateway_url': linking.GATEWAY, 'single_use': True})
    actual = httpx.AsyncClient
    monkeypatch.setattr(linking.httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    return linking.PersonalLink(), calls


@pytest.mark.anyio
async def test_pkce_callback_does_not_enroll_until_confirmation(flow):
    manager, calls = flow
    redirect = 'http://127.0.0.1:8787/chatgpt/callback'
    start = await manager.start('session-a', redirect)
    query = parse_qs(urlsplit(start['authorization_url']).query)
    assert query['scope'] == ['openid']
    assert query['code_challenge_method'] == ['S256']
    assert len(query['state'][0]) >= 43
    assert 'code_verifier' not in query
    assert await manager.callback(query['state'][0], 'auth-code', '')
    form = parse_qs(calls[1].content.decode())
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
    assert challenge == query['code_challenge'][0]
    assert form['redirect_uri'] == [redirect]
    status = await manager.status('session-a')
    assert status == {'status': 'ready', 'mcp_url': linking.RESOURCE}
    assert 'test-access' not in repr(manager.pending)
    assert 'never-stored' not in repr(manager.pending)
    enrolled = []
    with pytest.raises(HTTPException):
        await manager.complete('session-b', enrolled.append)
    assert enrolled == []
    result = await manager.complete('session-a', enrolled.append)
    assert enrolled == ['single-use-grant']
    assert result['connected']
    assert 'single-use-grant' not in repr(result)
    with pytest.raises(HTTPException):
        await manager.complete('session-a', enrolled.append)
    assert len(enrolled) == 1


@pytest.mark.anyio
async def test_state_denial_and_replay(flow):
    manager, calls = flow
    start = await manager.start('a', 'http://localhost:8787/chatgpt/callback')
    state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
    with pytest.raises(HTTPException):
        await manager.callback('wrong-state', 'code', '')
    assert len(calls) == 1
    assert not await manager.callback(state, '', 'access_denied')
    assert (await manager.status('a'))['status'] == 'error'
    with pytest.raises(HTTPException):
        await manager.callback(state, 'code', '')
    assert len(calls) == 1


@pytest.mark.anyio
async def test_expiry_session_binding_and_cancel(flow):
    manager, calls = flow
    await manager.start('a', 'http://localhost:8787/chatgpt/callback')
    for action in [manager.status, manager.cancel]:
        with pytest.raises(HTTPException):
            await action('b')
    with pytest.raises(HTTPException):
        await manager.start('b', 'http://localhost:8787/chatgpt/callback')
    await manager.cancel('a')
    await manager.start('a', 'http://localhost:8787/chatgpt/callback')
    assert len(calls) == 1  # registration reused; no credential persisted
    manager.pending['deadline'] = 0
    with pytest.raises(HTTPException):
        await manager.status('a')
    assert manager.pending is None


@pytest.mark.anyio
async def test_early_confirmation_rejected(flow):
    manager, _ = flow
    await manager.start('a', 'http://localhost:8787/chatgpt/callback')
    with pytest.raises(HTTPException):
        await manager.complete('a', lambda grant: pytest.fail('must not enroll'))


@pytest.mark.anyio
async def test_untrusted_gateway_rejected(flow, monkeypatch):
    manager, _ = flow
    start = await manager.start('a', 'http://localhost:8787/chatgpt/callback')
    state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
    def transport(request):
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={'access_token': 'secret'})
        return httpx.Response(200, json={'enrollment_token': 'secret-grant',
                                        'gateway_url': 'https://attacker.example', 'single_use': True})
    # The fixture factory already injects transport; replace via real class.
    monkeypatch.setattr(linking.httpx, 'AsyncClient', lambda **kw: httpx._client.AsyncClient(transport=httpx.MockTransport(transport), **kw))
    assert not await manager.callback(state, 'code', '')
    assert manager.pending['grant'] is None


def test_local_routes_require_capability_and_preserve_connection():
    # enterprise_app hardens the shared main.app at import time. Exercise it in
    # its own process so legacy base-app tests keep their intended composition.
    import os
    import subprocess
    import sys
    script = """
from starlette.testclient import TestClient
from server import enterprise_app
from server.local_auth import create_dashboard_session
import os
os.environ['WORKFLOW_OBSERVER_MODE'] = 'observe'
enterprise_app.gateway_status = lambda path: {'enrolled': True}
headers = {'Authorization': 'OWG-Session ' + create_dashboard_session()}
client = TestClient(enterprise_app.app, base_url='http://127.0.0.1:8787')
for path, method in [('start', 'post'), ('complete', 'post'), ('cancel', 'post'), ('status', 'get')]:
    assert getattr(client, method)('/v1/chatgpt-link/' + path).status_code == 401
assert client.post('/v1/chatgpt-link/start', headers=headers).status_code == 409
assert client.post('/v1/chatgpt-link/complete', headers=headers).status_code == 409
enterprise_app.gateway_status = lambda path: {'enrolled': False}
os.environ['WORKFLOW_OBSERVER_MODE'] = 'demo'
assert client.post('/v1/chatgpt-link/start', headers=headers).status_code == 409
assert client.get('/chatgpt/callback?state=invalid&code=not-a-secret').status_code == 400
assert client.get('/chatgpt/callback', headers={'Host': 'attacker.example'}).status_code == 400
"""
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                            timeout=30, env=dict(os.environ))
    assert result.returncode == 0, result.stderr


def test_callback_headers_and_ready_status_never_expose_credentials(monkeypatch):
    from starlette.testclient import TestClient
    from fastapi import FastAPI
    manager = linking.PersonalLink()
    manager.pending = {'state': 'test-state', 'status': 'waiting', 'owner': 'a',
                       'deadline': linking.time.monotonic() + 600, 'verifier': 'secret-verifier'}
    monkeypatch.setattr(linking, 'link', manager)
    app = FastAPI()
    app.include_router(linking.create_router(ensure_available=lambda: None, enroll=lambda grant: None))
    client = TestClient(app)
    response = client.get('/chatgpt/callback?state=test-state&error=access_denied')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'test-state' not in response.text
    assert 'secret-verifier' not in response.text
    assert client.get('/chatgpt/callback?state=test-state&code=replay').status_code == 400


@pytest.mark.anyio
async def test_remote_failure_is_redacted_and_consumes_callback(flow, monkeypatch):
    manager, _ = flow
    start = await manager.start('a', 'http://localhost:8787/chatgpt/callback')
    state = parse_qs(urlsplit(start['authorization_url']).query)['state'][0]
    def handler(request):
        return httpx.Response(500, text='secret tokens must never appear in status')
    monkeypatch.setattr(linking.httpx, 'AsyncClient', lambda **kw: httpx._client.AsyncClient(transport=httpx.MockTransport(handler), **kw))
    assert not await manager.callback(state, 'private-auth-code', '')
    assert await manager.status('a') == {'status': 'error', 'mcp_url': linking.RESOURCE}
    with pytest.raises(HTTPException):
        await manager.callback(state, 'private-auth-code', '')
