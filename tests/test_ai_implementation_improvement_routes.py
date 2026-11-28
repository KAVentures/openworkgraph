import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import ai_implementation_improvement_routes as routes
from server import ai_implementation_forecast_routes as forecast_routes
from test_ai_implementation_forecast_routes import setup, URL, HEADERS

BASE='/v1/ai-opportunity-handoff/improvement-'


def client(monkeypatch):
    c,p,ideas,q,calls=setup(monkeypatch)
    f=c.post(URL,json=p,headers=HEADERS);assert f.status_code==200
    app=FastAPI();app.include_router(routes.router)
    return TestClient(app),{**p,'forecast_review_sha256':f.json()['forecast_review_sha256']},ideas,q,calls


def test_prepare_and_revision_recompute_exact_forecast(monkeypatch):
    c,p,ideas,q,calls=client(monkeypatch)
    r=c.post(BASE+'feedback',json=p,headers=HEADERS);assert r.status_code==200,r.text
    f=r.json();packet=json.loads(f['feedback_json'])
    assert f['feedback_sha256']==packet['feedback_sha256'] and r.headers['cache-control']=='no-store'
    revised=packet['original_proposal'];revised['steps'][0]['text']='Proposed alternative requiring testing'
    req={**p,'feedback_sha256':f['feedback_sha256'],'revised_proposal_json':json.dumps(revised)}
    r=c.post(BASE+'revision',json=req,headers=HEADERS);assert r.status_code==200,r.text
    report=json.loads(r.json()['report_json']);assert report['changed_fields']==['steps']
    assert report['workflow_replaced'] is False and len(calls)==3
    for change in ({'feedback_sha256':'0'*64},{'forecast_review_sha256':'0'*64},{'revised_proposal_json':'{"x":1,"x":2}'}):
        assert c.post(BASE+'revision',json={**req,**change},headers=HEADERS).status_code==422
    ideas[0]['revision']+=1
    assert c.post(BASE+'feedback',json=p,headers=HEADERS).status_code==422


def test_auth_and_stale_quality_refused(monkeypatch):
    c,p,ideas,q,calls=client(monkeypatch)
    for h in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
        assert c.post(BASE+'feedback',json=p,headers=h).status_code==403
        assert c.post(BASE+'revision',json=p,headers=h).status_code==403
    q['financial_scenario']['modeled_net_monthly_value_change']=1234
    assert c.post(BASE+'feedback',json=p,headers=HEADERS).status_code==422
