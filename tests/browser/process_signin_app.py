"""Owned synthetic OIDC provider and process portal for browser acceptance."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import parse_qs, urlencode

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
scratch=Path(os.environ['OWG_BROWSER_TEST_DIR'])
PUBLIC='http://127.0.0.1:8793';ISSUER=PUBLIC+'/idp';CLIENT='synthetic-process-browser'
os.environ.update(OWG_GATEWAY_PROCESS_SSO_ENABLED='true',OWG_GATEWAY_SSO_ISSUER=ISSUER,
    OWG_GATEWAY_SSO_CLIENT_ID=CLIENT,OWG_GATEWAY_PUBLIC_URL=PUBLIC,
    OWG_GATEWAY_DATABASE_URL='sqlite:///'+str(scratch/'gateway.db'))
os.environ['NO_PROXY']=','.join(filter(None,[os.getenv('NO_PROXY',''),'127.0.0.1','localhost']))

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from gateway.human_access import HumanAccessSettings
from gateway_identity_helpers import make_app
from test_process_collaboration import model

settings=HumanAccessSettings(issuer=ISSUER,audience='synthetic-api-resource',jwks_url=ISSUER+'/jwks',
    group_scope_map={'writers':frozenset({'org:process:read','org:process:write'}),
                     'readers':frozenset({'org:process:read'})})
app,db=make_app(scratch,human_access=settings)
idp=FastAPI();key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk.update(kid='synthetic',use='sig',alg='RS256')
requests={};codes={}
(scratch/'process.json').write_text(json.dumps({'format':'openworkgraph.versioned-process-export.v1',
    'title':'Signed-in support process','process_model':model().model_dump()}),encoding='utf-8')

@idp.get('/.well-known/openid-configuration')
def discovery():return {'issuer':ISSUER,'authorization_endpoint':ISSUER+'/authorize','token_endpoint':ISSUER+'/token','jwks_uri':ISSUER+'/jwks'}

@idp.get('/jwks')
def jwks():return {'keys':[jwk]}

@idp.get('/authorize')
def authorize(request:Request):
    query=dict(request.query_params)
    if query.get('client_id')!=CLIENT or query.get('redirect_uri')!=PUBLIC+'/sso/callback' or query.get('code_challenge_method')!='S256':
        raise HTTPException(400,'Invalid synthetic authorization request')
    ticket=secrets.token_urlsafe(20);requests[ticket]=query
    return HTMLResponse('<h1>Synthetic company sign-in</h1><form method="post" action="/idp/authorize">'
        '<input type="hidden" name="ticket" value="'+ticket+'">'
        '<button name="person" value="alice">Use synthetic Alice</button>'
        '<button name="person" value="bob">Use synthetic Bob</button></form>')

@idp.post('/authorize')
async def confirm(request:Request):
    form=parse_qs((await request.body()).decode());query=requests.pop(form.get('ticket',[''])[0],None)
    person=form.get('person',[''])[0]
    if not query or person not in {'alice','bob'}:raise HTTPException(400,'Invalid synthetic login')
    code=secrets.token_urlsafe(24);codes[code]=(query,person)
    return RedirectResponse(query['redirect_uri']+'?'+urlencode({'code':code,'state':query['state']}),status_code=303)

@idp.post('/token')
async def token(request:Request):
    form=parse_qs((await request.body()).decode());record=codes.pop(form.get('code',[''])[0],None)
    if not record:raise HTTPException(400,'Synthetic code already used or invalid')
    query,person=record
    verifier=form.get('code_verifier',[''])[0]
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    if (not secrets.compare_digest(challenge,query['code_challenge'])
            or form.get('client_id')!=[CLIENT] or form.get('redirect_uri')!=[PUBLIC+'/sso/callback']):
        raise HTTPException(400,'Synthetic PKCE validation failed')
    now=int(time.time());claims={'iss':ISSUER,'aud':CLIENT,'sub':person,'iat':now,'exp':now+300,
        'nonce':query['nonce'],'email':person+'@synthetic.test','email_verified':True,'owg_org':'acme',
        'groups':['writers' if person=='alice' else 'readers']}
    return {'id_token':jwt.encode(claims,key,algorithm='RS256',headers={'kid':'synthetic'}),'token_type':'Bearer'}

app.mount('/idp',idp)
