import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import ai_implementation_batch_routes as batch_routes
from server import ai_implementation_portfolio_routes as portfolio_routes
from server import ai_implementation_forecast_routes as routes
from test_ai_implementation_forecast_review import fixture

URL='/v1/ai-opportunity-handoff/planning-forecast-review'
HEADERS={'Authorization':'OWG-Session owner'}


def setup(monkeypatch):
    args,ideas,pin,quality,comparability=fixture();s,p,proposals,rpin,plan,ppin=args
    calls=[]
    def analyze(iid,spec):
        calls.append((iid,spec));assert iid==quality['idea_id']
        if spec!=quality['assumptions']:raise ValueError('stale assumptions')
        return quality
    monkeypatch.setattr(batch_routes,'dashboard_session_valid',lambda v:v=='owner')
    monkeypatch.setattr(portfolio_routes,'current_ideas',lambda:ideas)
    monkeypatch.setattr(routes,'quality_result',analyze)
    app=FastAPI();app.include_router(routes.router)
    payload={'sources':[json.dumps(x) for x in s],'packet_sha256':p,'proposals_json':json.dumps(proposals),
        'batch_review_sha256':rpin,'plan_json':json.dumps(plan),'planning_review_sha256':ppin,
        'reconciliation_sha256':pin,'item_index':0,'quality_report_json':json.dumps(quality),'comparability':comparability}
    return TestClient(app),payload,ideas,quality,calls


def test_recomputes_actual_report_and_refuses_tampering(monkeypatch):
    c,p,ideas,q,calls=setup(monkeypatch)
    r=c.post(URL,json=p,headers=HEADERS);assert r.status_code==200,r.text
    assert r.headers['cache-control']=='no-store'
    data=r.json();report=json.loads(data['report_json'])
    assert report['idea_id']==q['idea_id'] and not report['all_declared_gates_met']
    assert report['forecast_review_sha256']==data['forecast_review_sha256']
    assert calls==[(q['idea_id'],q['assumptions'])]
    altered=json.loads(p['quality_report_json']);altered['financial_scenario']['modeled_net_monthly_value_change']=99999
    assert c.post(URL,json={**p,'quality_report_json':json.dumps(altered)},headers=HEADERS).status_code==422
    assert len(calls)==2
    ideas[0]['revision']+=1
    assert c.post(URL,json=p,headers=HEADERS).status_code==422 and len(calls)==2


def test_auth_and_bounds(monkeypatch):
    c,p,ideas,q,calls=setup(monkeypatch)
    for h in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
        assert c.post(URL,json=p,headers=h).status_code==403
    for change in ({'item_index':1},{'item_index':True},{'reconciliation_sha256':'0'*64},
                   {'quality_report_json':' '* (routes.MAX_QUALITY_BYTES+1)},
                   {'quality_report_json':'{"x":1,"x":2}'}):
        assert c.post(URL,json={**p,**change},headers=HEADERS).status_code==422
    assert not calls


def test_browser_integer_float_serialization_and_boolean_distinction(monkeypatch):
    c,p,ideas,q,calls=setup(monkeypatch)
    q['financial_scenario']['after_monthly_case_volume']=100.0
    assert c.post(URL,json=p,headers=HEADERS).status_code==200
    bad=json.loads(p['quality_report_json']);bad['all_declared_gates_met']=0
    assert c.post(URL,json={**p,'quality_report_json':json.dumps(bad)},headers=HEADERS).status_code==422
