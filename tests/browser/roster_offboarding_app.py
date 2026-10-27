"""Owned synthetic-only IdP/roster controls; never installed as Gateway routes."""
import os
os.environ['OWG_GATEWAY_ANALYSIS_SSO_ENABLED']='true'
from process_signin_app import app,db,settings,key,ISSUER
import time
import jwt
from gateway import employees as roster
from shared.lifespan import extend_lifespan

for group,scopes in list(settings.group_scope_map.items()):
    settings.group_scope_map[group]=scopes|frozenset({'org:analysis:read'})

def seed():
    e,_=roster.upsert_employee(db,'acme',email='alice@synthetic.test')
    with db.connect() as c:
        db._execute(c,'UPDATE gateway_employees SET sso_subject=? WHERE employee_id=?',('alice',e['employee_id']))
extend_lifespan(app,startup=seed)

@app.post('/synthetic/disable')
def disable():
    e=roster.employee_by_actor(db,'acme','alice@synthetic.test')
    return roster.offboard_employee(db,'acme',e['employee_id'])

@app.post('/synthetic/rehire')
def rehire():
    e,_=roster.upsert_employee(db,'acme',email='alice@synthetic.test')
    return e

@app.get('/synthetic/delegated')
def delegated():
    now=int(time.time())
    return {'token':jwt.encode({'iss':ISSUER,'aud':settings.audience,'sub':'bob','owg_org':'acme','groups':['readers'],'iat':now,'exp':now+300},key,algorithm='RS256',headers={'kid':'synthetic'})}
