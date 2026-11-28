from hashlib import sha256
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import joint_shadow_source_routes as routes
from test_joint_shadow_source_review import payload


def test_joint_route_recomputes_both_reviews(monkeypatch):
    p, basis, checked = payload(['clean', 'missing'], ['verified', 'missing'])
    data = {'plan_json':json.dumps(p['plan']), 'plan_sha256':p['plan_sha256'],
            'trace_json':json.dumps(p['trace']), 'trace_sha256':p['trace_sha256'],
            'assignments_json':json.dumps(p['assignments']), 'source_cohort':p['source_cohort']}
    calls=[]
    monkeypatch.setattr(routes,'dashboard_session_valid',lambda v:v=='owner')
    monkeypatch.setattr(routes.checks,'options',lambda *a:basis)
    def reconcile(i,j,raw):
        calls.append((i,j));return checked[sha256(raw).hexdigest()]
    monkeypatch.setattr(routes.checks,'review_export',reconcile)
    app=FastAPI();app.include_router(routes.router)
    path='/v1/automation-pipeline/idea/projects/project/joint-shadow-source-review'
    with TestClient(app) as c:
        for h in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
            assert c.post(path,json=data,headers=h).status_code==403
        h={'Authorization':'OWG-Session owner'}
        result=c.post(path,json=data,headers=h)
        assert result.status_code==200,result.text
        assert result.headers['cache-control']=='no-store'
        r=result.json();assert r['case_count']==2 and r['exact_declared_case_ids_match']
        assert r['cases'][0]['native_observation_status']=='no_deviation_in_returned_events'
        assert r['cases'][0]['historical_source_status']=='historical_operation_reported_verified'
        assert r['cases'][1]['native_observation_status']=='root_not_returned'
        assert r['cases'][1]['historical_source_status']=='missing_document'
        assert not r['business_outcomes_verified'] and not r['execution_authorized']
        assert calls==[('idea','project')]
        for bad in ({**data,'plan_sha256':'f'*64},{**data,'trace_json':'{"x":1,"x":2}'},
                    {**data,'source_cohort':{**data['source_cohort'],'cases':[]}}):
            assert c.post(path,json=bad,headers=h).status_code==422
        assert calls==[('idea','project')]
        monkeypatch.setattr(routes,'MAX_REQUEST_BYTES',8)
        assert c.post(path,json=data,headers=h).status_code==413
