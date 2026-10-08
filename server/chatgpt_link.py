"""Personal OAuth linking. Credentials remain in memory until explicit enrollment.

This is a desktop OAuth client, not another identity provider. No capture or AI
access preference is changed. All remote destinations are fixed OWG endpoints.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

ISSUER = 'https://tohekeiamafjjfduzqzq.supabase.co/auth/v1'
PLUGIN = 'https://mcp.owg.kinvectum.com'
GATEWAY = 'https://gateway.owg.kinvectum.com'
RESOURCE = PLUGIN + '/mcp'
TTL = 600


class PersonalLink:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.pending = None
        self.clients = {}

    def _flow(self, owner):
        flow = self.pending
        if flow and flow['deadline'] <= time.monotonic():
            self.pending = None
            flow = None
        if not flow or not secrets.compare_digest(flow['owner'], owner):
            raise HTTPException(409, 'No active link for this dashboard session')
        return flow

    async def start(self, owner, redirect):
        async with self.lock:
            if self.pending and self.pending['deadline'] > time.monotonic():
                raise HTTPException(409, 'A link is already in progress; finish or cancel it first')
            self.pending = None
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                if redirect not in self.clients:
                    try:
                        response = await client.post(ISSUER + '/oauth/clients/register', json={
                            'client_name': 'OpenWorkGraph Desktop',
                            'redirect_uris': [redirect], 'grant_types': ['authorization_code'],
                            'response_types': ['code'], 'token_endpoint_auth_method': 'none',
                            'scope': 'openid',
                        })
                        response.raise_for_status()
                        client_id = response.json()['client_id']
                        if not isinstance(client_id, str) or not client_id:
                            raise ValueError('invalid registration')
                        self.clients[redirect] = client_id
                    except (httpx.HTTPError, ValueError, KeyError, TypeError):
                        raise HTTPException(502, 'OAuth registration failed; please try again') from None
            verifier = secrets.token_urlsafe(32)
            state = secrets.token_urlsafe(32)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            self.pending = dict(owner=owner, state=state, verifier=verifier, redirect=redirect,
                                client_id=self.clients[redirect], deadline=time.monotonic() + TTL,
                                status='waiting', grant=None)
            return {'authorization_url': ISSUER + '/oauth/authorize?' + urlencode({
                'response_type': 'code', 'client_id': self.clients[redirect],
                'redirect_uri': redirect, 'scope': 'openid', 'state': state,
                'code_challenge': challenge, 'code_challenge_method': 'S256',
            }), 'expires_in': TTL}

    async def callback(self, state, code, error):
        async with self.lock:
            flow = self.pending
            if (not flow or flow['deadline'] <= time.monotonic() or flow['status'] != 'waiting'
                    or not state or not secrets.compare_digest(flow['state'], state)):
                raise HTTPException(400, 'Invalid or expired link callback')
            # Consume state before any I/O, including denial and remote errors.
            flow['state'] = ''
            flow['status'] = 'error'
            verifier = flow.pop('verifier')
            if error or not code:
                return False
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                    response = await client.post(ISSUER + '/oauth/token', data={
                        'grant_type': 'authorization_code', 'client_id': flow['client_id'],
                        'code': code, 'redirect_uri': flow['redirect'], 'code_verifier': verifier,
                    })
                    response.raise_for_status()
                    access = response.json()['access_token']
                    if not isinstance(access, str) or not access:
                        raise ValueError('invalid access token')
                    response = await client.post(PLUGIN + '/v1/plugin/device-link',
                                                 headers={'Authorization': 'Bearer ' + access})
                    response.raise_for_status()
                    grant = response.json()
                    if not isinstance(grant, dict):
                        raise ValueError('invalid enrollment grant')
                    if (grant.get('gateway_url', '').rstrip('/') != GATEWAY
                            or not grant.get('single_use') or not isinstance(grant.get('enrollment_token'), str)
                            or not grant['enrollment_token']):
                        raise ValueError('invalid enrollment grant')
                flow['grant'] = grant['enrollment_token']
                flow['status'] = 'ready'
                return True
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                # Never return remote response bodies, tokens or authorization codes.
                return False

    async def status(self, owner):
        async with self.lock:
            return {'status': self._flow(owner)['status'], 'mcp_url': RESOURCE}

    async def cancel(self, owner):
        async with self.lock:
            self._flow(owner)
            self.pending = None
            return {'cancelled': True}

    async def complete(self, owner, enroll):
        async with self.lock:
            flow = self._flow(owner)
            if flow['status'] != 'ready':
                raise HTTPException(409, 'Finish sign-in before confirming sharing')
            grant = flow['grant']
            # Single use locally as well as on the Gateway, even on enrollment failure.
            self.pending = None
            try:
                await asyncio.to_thread(enroll, grant)
            except Exception:
                raise HTTPException(502, 'Enrollment failed; restart linking to try again') from None
            return {'connected': True, 'mcp_url': RESOURCE, 'history_sync_mode': 'from_enrollment_forward'}


link = PersonalLink()


def _owner(request):
    # secure_app has already authenticated this header. Bind flow to that capability.
    return hashlib.sha256(request.headers.get('authorization', '').encode()).hexdigest()


def create_router(*, ensure_available, enroll):
    router = APIRouter()
    @router.post('/v1/chatgpt-link/start')
    async def start(request: Request):
        ensure_available()
        # Ignore forwarded headers: callbacks may only target this loopback listener.
        origin = urlsplit(str(request.base_url))
        if origin.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise HTTPException(400, 'Open the dashboard through its loopback address')
        host = '[::1]' if origin.hostname == '::1' else origin.hostname
        redirect = f'http://{host}:{origin.port or 80}/chatgpt/callback'
        return await link.start(_owner(request), redirect)

    @router.get('/v1/chatgpt-link/status')
    async def status(request: Request):
        return await link.status(_owner(request))

    @router.post('/v1/chatgpt-link/cancel')
    async def cancel(request: Request):
        return await link.cancel(_owner(request))

    @router.post('/v1/chatgpt-link/complete')
    async def complete(request: Request):
        ensure_available()
        return await link.complete(_owner(request), enroll)

    @router.get('/chatgpt/callback')
    async def callback(state: str = '', code: str = '', error: str = ''):
        ok = await link.callback(state, code, error)
        message = ('Sign-in complete. Return to the OpenWorkGraph dashboard to confirm sharing.'
                   if ok else 'Sign-in did not complete. Return to the dashboard and try again.')
        return HTMLResponse('<!doctype html><html><title>OpenWorkGraph</title><p>' + message + '</p></html>',
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                     'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'"})

    return router
