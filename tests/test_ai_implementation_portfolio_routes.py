import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import ai_implementation_batch_routes as batch_routes
from server import ai_implementation_portfolio_routes as routes
from shared import ai_implementation_review as contract
from test_ai_implementation_portfolio_link import data, idea

PATH='/v1/ai-opportunity-handoff/planning-portfolio'
HEADERS={'Authorization':'OWG-Session owner'}


def client(monkeypatch, args, ideas):
    monkeypatch.setattr(batch_routes,'dashboard_session_valid',lambda v:v=='owner')
    monkeypatch.setattr(routes,'current_ideas',lambda:ideas)
    app=FastAPI();app.include_router(routes.router)
    s,p,proposals,rpin,plan,ppin=args
    payload={'sources':[json.dumps(x) for x in s],'packet_sha256':p,'proposals_json':json.dumps(proposals),
             'batch_review_sha256':rpin,'plan_json':json.dumps(plan),'planning_review_sha256':ppin}
    return TestClient(app),payload


def test_exact_live_inventory_and_refresh(monkeypatch):
    args=data();ideas=[idea(args,0,1)];c,p=client(monkeypatch,args,ideas)
    res=c.post(PATH,json=p,headers=HEADERS);assert res.status_code==200 and res.headers['cache-control']=='no-store'
    d=res.json();q=json.loads(d['report_json']);assert q['matched_count']==1 and q['item_count']==6
    assert d['report_json']==contract._json(q).decode() and d['reconciliation_sha256']==q['reconciliation_sha256']
    assert d['portfolio_written'] is False
    ideas.append(idea(args,2,2))
    q2=json.loads(c.post(PATH,json=p,headers=HEADERS).json()['report_json']);assert q2['matched_count']==2
    assert q2['reconciliation_sha256']!=q['reconciliation_sha256']
    for change in ({'planning_review_sha256':'0'*64},{'plan_json':'{}'},{'ideas':[]}):
        assert c.post(PATH,json={**p,**change},headers=HEADERS).status_code==422


def test_auth_and_bounds(monkeypatch):
    args=data();c,p=client(monkeypatch,args,[])
    for h in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
        assert c.post(PATH,json=p,headers=h).status_code==403
    for change in ({'sources':['{"a":1,"a":2}']},{'plan_json':'{"a":1,"a":2}'},{'proposals_json':'[NaN]'}):
        assert c.post(PATH,json={**p,**change},headers=HEADERS).status_code==422
    monkeypatch.setattr(batch_routes,'MAX_REQUEST',8)
    assert c.post(PATH,json=p,headers=HEADERS).status_code==413
