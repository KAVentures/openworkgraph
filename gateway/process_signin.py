"""Optional company sign-in scoped exclusively to the shared-process portal.

SSO ID tokens are never accepted as API access tokens. The dedicated callback
issues a short-lived server session, with no evidence/admin/agent permission.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from shared.time_utils import normalize_timestamp
from .auth import token_hash
from .human_access import HumanPrincipal, principal_from_claims
from .sso import SSOError

COOKIE='owg_process_login'
SCHEMA='''CREATE TABLE IF NOT EXISTS gateway_process_sessions (
    session_hash TEXT PRIMARY KEY, principal_json TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL)'''


class ProcessSessionStore:
    def __init__(self,db,settings,*,now=None):
        self.db=db;self.settings=settings
        self.now=now or (lambda:datetime.now(timezone.utc))

    def initialize(self):
        with self.db.connect() as conn:
            self.db._execute(conn,SCHEMA)
            self.db._execute(conn,'CREATE INDEX IF NOT EXISTS idx_process_session_expiry ON gateway_process_sessions(expires_at)')

    def _principal(self,claims):
        principal=principal_from_claims(self.settings,claims)
        if len(principal.groups)>100 or any(len(group)>200 for group in principal.groups):
            raise ValueError('Process identity group claims exceed their bound')
        principal=replace(principal,scopes=frozenset(s for s in principal.scopes if s.startswith('org:process:')))
        if 'org:process:read' not in principal.scopes:
            raise ValueError('Explicit organization process read permission is required')
        return principal

    def issue(self,claims):
        if not isinstance(claims.get('iss'),str) or claims['iss'].rstrip('/') != self.settings.issuer.rstrip('/'):
            raise ValueError('The company and process identity issuers must match')
        principal=self._principal(claims)
        if type(claims.get('exp')) not in {int,float}:
            raise ValueError('A verified identity expiry is required')
        now=self.now()
        expires=min(now+timedelta(minutes=15),datetime.fromtimestamp(claims['exp'],timezone.utc))
        if expires<=now:raise ValueError('The verified company identity expired')
        token='owg_process_session_'+secrets.token_urlsafe(32)
        # Keep only bounded identity fields; never persist ID/access tokens,
        # email, provider responses, designs or arbitrary identity claims.
        identity={'subject':principal.subject,'organization_id':principal.organization_id,
                  'actor_id':principal.actor_id,'groups':sorted(principal.groups),'issuer':principal.issuer}
        with self.db.connect() as conn:
            self.db._execute(conn,'DELETE FROM gateway_process_sessions WHERE expires_at <= ?',
                             (normalize_timestamp(now.isoformat()),))
            self.db._execute(conn,'INSERT INTO gateway_process_sessions VALUES (?, ?, ?, ?)',
                (token_hash(token),json.dumps(identity),normalize_timestamp(now.isoformat()),normalize_timestamp(expires.isoformat())))
            self.db._execute(conn,'''INSERT INTO audit_log(observed_at, organization_id, principal_id, action, details_json)
                VALUES (?, ?, ?, ?, ?)''',(normalize_timestamp(now.isoformat()),principal.organization_id,principal.audit_id,
                'process.session.created',json.dumps({'method':'sso','max_minutes':15})))
        return token,principal

    def read(self,token):
        if not isinstance(token,str) or not token.startswith('owg_process_session_') or len(token)>100:
            raise ValueError('A current process session is required')
        with self.db.connect() as conn:
            row=self.db._execute(conn,'SELECT principal_json, expires_at FROM gateway_process_sessions WHERE session_hash = ?',
                                 (token_hash(token),)).fetchone()
        if not row or row[1]<=normalize_timestamp(self.now().isoformat()):
            raise ValueError('Process session expired; sign in again')
        value=json.loads(row[0])
        if value['issuer']!=self.settings.issuer:
            raise ValueError('Process identity issuer changed')
        # Re-evaluate the current group map on every request. No stored scope
        # list can retain permissions after the configured mapping changes.
        scopes=set()
        for group in value['groups']:scopes.update((self.settings.group_scope_map or {}).get(group,()))
        scopes=frozenset(scope for scope in scopes if scope.startswith('org:process:'))
        if 'org:process:read' not in scopes:raise ValueError('Process read permission is no longer configured')
        return HumanPrincipal(subject=value['subject'],organization_id=value['organization_id'],
            actor_id=value['actor_id'],groups=frozenset(value['groups']),issuer=value['issuer'],scopes=scopes)

    def revoke(self,token):
        with self.db.connect() as conn:
            row=self.db._execute(conn,'SELECT principal_json FROM gateway_process_sessions WHERE session_hash = ?',
                                 (token_hash(token),)).fetchone()
            self.db._execute(conn,'DELETE FROM gateway_process_sessions WHERE session_hash = ?',(token_hash(token),))
            if row:
                identity=json.loads(row[0])
                principal=HumanPrincipal(subject=identity['subject'],organization_id=identity['organization_id'],
                    actor_id=identity['actor_id'],groups=frozenset(),scopes=frozenset(),issuer=identity['issuer'])
                self.db._execute(conn,'''INSERT INTO audit_log(observed_at, organization_id, principal_id, action, details_json)
                    VALUES (?, ?, ?, ?, ?)''',(normalize_timestamp(self.now().isoformat()),principal.organization_id,
                    principal.audit_id,'process.session.closed','{}'))


def build_process_signin_router(app,store,settings,delegated_human):
    db=store.db
    router=APIRouter()
    enabled_by_admin=os.getenv('OWG_GATEWAY_PROCESS_SSO_ENABLED','').lower() in {'1','true','yes','on'}
    def enabled():
        sso=app.state.sso_client
        return bool(enabled_by_admin and settings.enabled and sso.settings.enabled
                    and settings.issuer.rstrip('/')==sso.settings.issuer.rstrip('/'))

    def human(authorization: str | None = Header(default=None)):
        value=str(authorization or '')
        if value.lower().startswith('owg-process-session '):
            if not enabled():raise HTTPException(401,'Process company sign-in is disabled')
            try:return store.read(value[len('owg-process-session '):].strip())
            except (ValueError,KeyError,TypeError):raise HTTPException(401,'Process session expired or permissions changed; sign in again') from None
        return delegated_human(authorization)

    @router.get('/v1/human/processes/sign-in/config')
    def config():
        return JSONResponse({'company_sign_in_enabled':enabled(),'session_minutes_max':15},headers={'Cache-Control':'no-store'})

    @router.post('/v1/human/processes/sign-in')
    def start(request: Request):
        if not enabled():raise HTTPException(404,'Company process sign-in is not configured')
        public=urlsplit(app.state.sso_client.settings.public_url)
        if request.headers.get('origin') != public.scheme+'://'+public.netloc:
            raise HTTPException(403,'Start sign-in from the company process portal')
        binding=secrets.token_urlsafe(32)
        try:url=app.state.sso_client.start('processes',{'browser_binding_sha256':token_hash(binding)})
        except SSOError as exc:raise HTTPException(exc.status_code,str(exc)) from None
        except Exception:raise HTTPException(502,'The company sign-in provider could not be reached') from None
        response=JSONResponse({'redirect_url':url},headers={'Cache-Control':'no-store'})
        response.set_cookie(COOKIE,binding,httponly=True,secure=public.scheme=='https',
                            samesite='lax',path='/sso/callback',max_age=600)
        return response

    def finish(request,context,identity):
        target='/processes#error=Company%20sign-in%20failed%20or%20process%20permission%20is%20missing'
        try:
            cookie=request.cookies.get(COOKIE,'')
            expected=context.get('browser_binding_sha256')
            if not enabled() or not cookie or not isinstance(expected,str) or not hmac.compare_digest(token_hash(cookie),expected):
                raise ValueError('Sign-in browser binding failed')
            token,principal=store.issue(identity['verified_claims'])
            target='/processes#process_session='+token
        except Exception:
            pass
        response=RedirectResponse(target,status_code=303,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        response.delete_cookie(COOKIE,path='/sso/callback')
        return response
    app.state.process_sso_callback=finish

    @router.get('/v1/human/processes/session/me')
    def me(authorization: str | None = Header(default=None)):
        p=human(authorization)
        return JSONResponse({'organization_id':p.organization_id,'actor_id':p.actor_id,
            'scopes':sorted(s for s in p.scopes if s.startswith('org:process:'))},headers={'Cache-Control':'no-store'})

    @router.post('/v1/human/processes/session/logout')
    def logout(authorization: str | None = Header(default=None)):
        value=str(authorization or '')
        if not value.lower().startswith('owg-process-session '):raise HTTPException(401,'Process session required')
        token=value[len('owg-process-session '):].strip()
        if len(token)>100:raise HTTPException(401,'Invalid process session')
        store.revoke(token)
        return JSONResponse({'signed_out':True},headers={'Cache-Control':'no-store'})
    app.state.process_session_store=store
    return router,human
