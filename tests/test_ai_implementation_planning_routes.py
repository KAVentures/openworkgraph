import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import ai_implementation_batch_routes as batch_routes
from server import ai_implementation_planning_routes as routes
from shared import ai_implementation_review as contract
from test_ai_implementation_planning import fixture, assumptions

BASE = '/v1/ai-opportunity-handoff/'
HEADERS = {'Authorization': 'OWG-Session owner'}


def client(monkeypatch):
    monkeypatch.setattr(batch_routes, 'dashboard_session_valid', lambda token: token == 'owner')
    app = FastAPI(); app.include_router(routes.router)
    return TestClient(app)


def inputs():
    (sources, pin, proposals, review_pin), plan = fixture()
    return {'sources': [json.dumps(s) for s in sources], 'packet_sha256': pin,
            'proposals_json': json.dumps(proposals), 'batch_review_sha256': review_pin}, plan


def test_exact_worklist_and_comparison(monkeypatch):
    c = client(monkeypatch); p, plan = inputs()
    r = c.post(BASE+'planning-worklist', json={**p, 'currency':'SEK'}, headers=HEADERS)
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    data = r.json(); assert data['worklist_json'] == contract._json(plan).decode()
    assert data['portfolio_written'] is False
    plan['confirm_human_reviewed_assumptions'] = True
    plan['candidates'][0]['assessment'] = assumptions()
    req = {**p, 'plan_json': json.dumps(plan)}
    r = c.post(BASE+'planning-compare', json=req, headers=HEADERS)
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    data = r.json(); report = json.loads(data['report_json'])
    assert data['plan_json'] == contract._json(plan).decode()
    assert data['report_json'] == contract._json(report).decode()
    assert report['planning_input_sha256'] == contract._digest(plan)
    assert report['planning_review_sha256'] == data['planning_review_sha256']
    assert report['item_count'] == 6 and report['assessed_count'] == 1
    assert report['portfolio_total'] is None and data['execution_authorized'] is False
    for change in ({'batch_review_sha256':'0'*64}, {'plan_json':'{}'}, {'packet_sha256':'0'*64},
                   {'plan_json':json.dumps({**plan,'confirm_human_reviewed_assumptions':False})}):
        assert c.post(BASE+'planning-compare', json={**req,**change}, headers=HEADERS).status_code == 422


def test_auth_and_bounds(monkeypatch):
    c=client(monkeypatch); p, plan=inputs()
    for headers in ({}, {'Authorization':'Bearer owner'}, {'Authorization':'OWG-Session wrong'}):
        for path in ('planning-worklist','planning-compare'):
            assert c.post(BASE+path, json=p, headers=headers).status_code==403
    for change in ({'currency':'sek'}, {'proposals_json':'[NaN]'}, {'sources':['{"a":1,"a":2}']}):
        assert c.post(BASE+'planning-worklist',json={**p,'currency':'SEK',**change},headers=HEADERS).status_code==422
    for text in ('{"a":1,"a":2}', ' '*(routes.planning.MAX_PLAN_BYTES+1)):
        assert c.post(BASE+'planning-compare',json={**p,'plan_json':text},headers=HEADERS).status_code==422
    monkeypatch.setattr(batch_routes,'MAX_REQUEST',8)
    assert c.post(BASE+'planning-worklist',json={**p,'currency':'SEK'},headers=HEADERS).status_code==413
