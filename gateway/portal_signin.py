"""Shared machinery for separately scoped process and analysis browser sessions.

Only fixed internal portal names select SQL identifiers or authentication schemes.
ID tokens are never API credentials; each portal has its own opt-in and binding.
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

class PortalSessionStore:
    def __init__(self,db,settings,*,portal,now=None):
        if portal not in {'process','analysis'}:
            raise ValueError('Unknown scoped portal')
        self.portal=portal
        self.plural={'process':'processes','analysis':'analyses'}[portal]
        self.table='gateway_'+portal+'_sessions'
        self.prefix='owg_'+portal+'_session_'
        self.scope_prefix='org:'+portal+':'
        self.scheme='OWG-'+portal.title()+'-Session'
        self.db=db;self.settings=settings
        self.now=now or (lambda:datetime.now(timezone.utc))

    def initialize(self):
        from .employees import init_employee_schema
        init_employee_schema(self.db)
        with self.db.connect() as conn:
            self.db._execute(conn,f'''CREATE TABLE IF NOT EXISTS {self.table} (
                session_hash TEXT PRIMARY KEY, principal_json TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL)''')
            self.db._execute(conn,f'CREATE INDEX IF NOT EXISTS idx_{self.portal}_session_expiry ON {self.table}(expires_at)')

    def _principal(self,claims):
        principal=principal_from_claims(self.settings,claims)
        from .roster_access import active_identity
        employee=active_identity(self.db,principal.organization_id,principal.actor_id)
        if employee:principal=replace(principal,actor_id=employee['actor_id'])
        if len(principal.groups)>100 or any(len(group)>200 for group in principal.groups):
            raise ValueError('Portal identity group claims exceed their bound')
        principal=replace(principal,scopes=frozenset(s for s in principal.scopes if s.startswith(self.scope_prefix)))
        if self.scope_prefix+'read' not in principal.scopes:
            raise ValueError('Explicit organization portal read permission is required')
        return principal,employee

    def issue(self,claims):
        if not isinstance(claims.get('iss'),str) or claims['iss'].rstrip('/') != self.settings.issuer.rstrip('/'):
            raise ValueError('The company and portal identity issuers must match')
        principal,employee=self._principal(claims)
        if type(claims.get('exp')) not in {int,float}:
            raise ValueError('A verified identity expiry is required')
        now=self.now()
        expires=min(now+timedelta(minutes=15),datetime.fromtimestamp(claims['exp'],timezone.utc))
        if expires<=now:raise ValueError('The verified company identity expired')
        token=self.prefix+secrets.token_urlsafe(32)
        # Keep only bounded identity fields; never persist ID/access tokens,
        # extra email claims, provider responses, designs or arbitrary claims.
        # The authorized actor identifier can itself be a roster work email.
        identity={'subject':principal.subject,'organization_id':principal.organization_id,
                  'actor_id':principal.actor_id,'groups':sorted(principal.groups),'issuer':principal.issuer}
        if employee:identity['roster_binding']=employee['binding']
        with self.db.connect() as conn:
            self.db._execute(conn,f'DELETE FROM {self.table} WHERE expires_at <= ?',
                             (normalize_timestamp(now.isoformat()),))
            self.db._execute(conn,f'INSERT INTO {self.table} VALUES (?, ?, ?, ?)',
                (token_hash(token),json.dumps(identity),normalize_timestamp(now.isoformat()),normalize_timestamp(expires.isoformat())))
            self.db._execute(conn,'''INSERT INTO audit_log(observed_at, organization_id, principal_id, action, details_json)
                VALUES (?, ?, ?, ?, ?)''',(normalize_timestamp(now.isoformat()),principal.organization_id,principal.audit_id,
                self.portal+'.session.created',json.dumps({'method':'sso','max_minutes':15})))
        return token,principal

    def read(self,token):
        if not isinstance(token,str) or not token.startswith(self.prefix) or len(token)>100:
            raise ValueError('A current portal session is required')
        with self.db.connect() as conn:
            row=self.db._execute(conn,f'SELECT principal_json, expires_at FROM {self.table} WHERE session_hash = ?',
                                 (token_hash(token),)).fetchone()
        if not row or row[1]<=normalize_timestamp(self.now().isoformat()):
            raise ValueError('Portal session expired; sign in again')
        value=json.loads(row[0])
        if value['issuer']!=self.settings.issuer:
            raise ValueError('Portal identity issuer changed')
        from .roster_access import active_identity
        employee=active_identity(self.db,value['organization_id'],value['actor_id'],value['subject'])
        binding=value.get('roster_binding')
        if (employee and (employee['actor_id']!=value['actor_id'] or binding!=employee['binding'])) or (binding is not None and employee is None):
            # Legacy/unbound sessions need fresh sign-in once roster-linked.
            # A monotonic epoch, not wall-clock time, prevents rehire revival.
            raise ValueError('Roster identity changed; sign in again')
        # Re-evaluate the current group map on every request. No stored scope
        # list can retain permissions after the configured mapping changes.
        scopes=set()
        for group in value['groups']:scopes.update((self.settings.group_scope_map or {}).get(group,()))
        scopes=frozenset(scope for scope in scopes if scope.startswith(self.scope_prefix))
        if self.scope_prefix+'read' not in scopes:raise ValueError('Portal read permission is no longer configured')
        return HumanPrincipal(subject=value['subject'],organization_id=value['organization_id'],
            actor_id=value['actor_id'],groups=frozenset(value['groups']),issuer=value['issuer'],scopes=scopes)

    def revoke(self,token):
        with self.db.connect() as conn:
            row=self.db._execute(conn,f'SELECT principal_json FROM {self.table} WHERE session_hash = ?',
                                 (token_hash(token),)).fetchone()
            self.db._execute(conn,f'DELETE FROM {self.table} WHERE session_hash = ?',(token_hash(token),))
            if row:
                identity=json.loads(row[0])
                principal=HumanPrincipal(subject=identity['subject'],organization_id=identity['organization_id'],
                    actor_id=identity['actor_id'],groups=frozenset(),scopes=frozenset(),issuer=identity['issuer'])
                self.db._execute(conn,'''INSERT INTO audit_log(observed_at, organization_id, principal_id, action, details_json)
                    VALUES (?, ?, ?, ?, ?)''',(normalize_timestamp(self.now().isoformat()),principal.organization_id,
                    principal.audit_id,self.portal+'.session.closed','{}'))


def build_portal_signin_router(app,store,settings,delegated_human):
    portal=store.portal
    plural=store.plural
    base='/v1/human/'+plural
    cookie_name='owg_'+portal+'_login'
    router=APIRouter()
    enabled_by_admin=os.getenv('OWG_GATEWAY_'+portal.upper()+'_SSO_ENABLED','').lower() in {'1','true','yes','on'}
    def enabled():
        sso=app.state.sso_client
        return bool(enabled_by_admin and settings.enabled and sso.settings.enabled
                    and settings.issuer.rstrip('/')==sso.settings.issuer.rstrip('/'))

    def human(authorization: str | None = Header(default=None)):
        value=str(authorization or '')
        if value.lower().startswith(store.scheme.lower()+' '):
            if not enabled():raise HTTPException(401,'Company portal sign-in is disabled')
            try:return store.read(value[len(store.scheme)+1:].strip())
            except (ValueError,KeyError,TypeError):raise HTTPException(401,'Portal session expired or permissions changed; sign in again') from None
        return delegated_human(authorization)

    @router.get(base+'/sign-in/config')
    def config():
        return JSONResponse({'company_sign_in_enabled':enabled(),'session_minutes_max':15},headers={'Cache-Control':'no-store'})

    @router.post(base+'/sign-in')
    def start(request: Request):
        if not enabled():raise HTTPException(404,'Company portal sign-in is not configured')
        public=urlsplit(app.state.sso_client.settings.public_url)
        if request.headers.get('origin') != public.scheme+'://'+public.netloc:
            raise HTTPException(403,'Start sign-in from the company portal')
        binding=secrets.token_urlsafe(32)
        try:url=app.state.sso_client.start(plural,{'browser_binding_sha256':token_hash(binding)})
        except SSOError as exc:raise HTTPException(exc.status_code,str(exc)) from None
        except Exception:raise HTTPException(502,'The company sign-in provider could not be reached') from None
        response=JSONResponse({'redirect_url':url},headers={'Cache-Control':'no-store'})
        response.set_cookie(cookie_name,binding,httponly=True,secure=public.scheme=='https',
                            samesite='lax',path='/sso/callback',max_age=600)
        return response

    def finish(request,context,identity):
        target='/'+plural+'#error=Company%20sign-in%20failed%20or%20portal%20permission%20is%20missing'
        try:
            cookie=request.cookies.get(cookie_name,'')
            expected=context.get('browser_binding_sha256')
            if not enabled() or not cookie or not isinstance(expected,str) or not hmac.compare_digest(token_hash(cookie),expected):
                raise ValueError('Sign-in browser binding failed')
            token,principal=store.issue(identity['verified_claims'])
            target='/'+plural+'#'+portal+'_session='+token
        except Exception:
            pass
        response=RedirectResponse(target,status_code=303,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        response.delete_cookie(cookie_name,path='/sso/callback')
        return response
    setattr(app.state,portal+'_sso_callback',finish)

    @router.get(base+'/session/me')
    def me(authorization: str | None = Header(default=None)):
        p=human(authorization)
        return JSONResponse({'organization_id':p.organization_id,'actor_id':p.actor_id,
            'scopes':sorted(s for s in p.scopes if s.startswith(store.scope_prefix))},headers={'Cache-Control':'no-store'})

    @router.post(base+'/session/logout')
    def logout(authorization: str | None = Header(default=None)):
        value=str(authorization or '')
        if not value.lower().startswith(store.scheme.lower()+' '):raise HTTPException(401,'Portal session required')
        token=value[len(store.scheme)+1:].strip()
        if len(token)>100:raise HTTPException(401,'Invalid portal session')
        store.revoke(token)
        return JSONResponse({'signed_out':True},headers={'Cache-Control':'no-store'})
    setattr(app.state,portal+'_session_store',store)
    return router,human
