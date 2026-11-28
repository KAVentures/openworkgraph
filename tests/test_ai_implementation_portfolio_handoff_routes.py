import copy
import json
from threading import RLock
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import ai_implementation_batch_routes as batch_routes
from server import ai_implementation_portfolio_routes as portfolio_routes
from server import ai_implementation_portfolio_handoff_routes as routes
from shared import ai_implementation_portfolio_link as link
from test_ai_implementation_portfolio_handoff import fixture

BASE='/v1/ai-opportunity-handoff/planning-save-'
HEADERS={'Authorization':'OWG-Session owner'}


def setup(monkeypatch):
    args, ideas=fixture(); s,p,proposals,rpin,plan,ppin=args
    calls=[]
    def save(iid,payload):
        calls.append((iid,copy.deepcopy(payload)))
        target=next(i for i in ideas if i['idea_id']==iid)
        assert target['revision']==payload['expected_revision']
        target['planning']={k:v for k,v in payload.items() if k!='expected_revision'}
        target['revision']+=1
        return target
    monkeypatch.setattr(batch_routes,'dashboard_session_valid',lambda v:v=='owner')
    monkeypatch.setattr(portfolio_routes,'current_ideas',lambda:ideas)
    monkeypatch.setattr(routes,'_pipeline',lambda:SimpleNamespace(_LOCK=RLock(),pipeline=lambda:{'ideas':ideas},plan=save))
    app=FastAPI();app.include_router(routes.router)
    payload={'sources':[json.dumps(x) for x in s],'packet_sha256':p,'proposals_json':json.dumps(proposals),
        'batch_review_sha256':rpin,'plan_json':json.dumps(plan),'planning_review_sha256':ppin,
        'reconciliation_sha256':link.reconcile(*args,ideas)['reconciliation_sha256'],'item_index':2,'budget_estimate':'150'}
    return TestClient(app),payload,ideas,calls


def test_exact_preview_then_explicit_save(monkeypatch):
    c,p,ideas,calls=setup(monkeypatch)
    r=c.post(BASE+'preview',json=p,headers=HEADERS);assert r.status_code==200,r.text
    v=r.json();assert not calls and not v['portfolio_written']
    req={**p,'expected_preview_sha256':v['preview_sha256'],'confirm_save_planning':True}
    for change in ({'confirm_save_planning':False},{'expected_preview_sha256':'0'*64},{'budget_estimate':'151'}):
        assert c.post(BASE+'confirm',json={**req,**change},headers=HEADERS).status_code==422
    assert not calls
    r=c.post(BASE+'confirm',json=req,headers=HEADERS);assert r.status_code==200,r.text
    assert r.headers['cache-control']=='no-store' and r.json()['revision']==3
    assert r.json()['portfolio_written'] and not r.json()['approval_changed'] and not r.json()['execution_authorized']
    assert calls==[(v['idea_id'],v['proposed_planning'])]
    assert c.post(BASE+'confirm',json=req,headers=HEADERS).status_code==422 and len(calls)==1


def test_stale_and_auth_refuse(monkeypatch):
    c,p,ideas,calls=setup(monkeypatch)
    for h in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
        for path in ('preview','confirm'):assert c.post(BASE+path,json=p,headers=h).status_code==403
    v=c.post(BASE+'preview',json=p,headers=HEADERS).json();ideas[1]['revision']+=1
    assert c.post(BASE+'confirm',json={**p,'expected_preview_sha256':v['preview_sha256'],'confirm_save_planning':True},headers=HEADERS).status_code==422
    assert not calls
