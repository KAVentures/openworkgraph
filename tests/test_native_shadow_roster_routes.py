import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import native_shadow_roster_routes as routes
from test_native_shadow_roster import package, cases, snapshot, run
from shared.native_shadow_roster import digest


def client(monkeypatch):
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda value: value == 'owner')
    app = FastAPI(); app.include_router(routes.router)
    return TestClient(app)


HEADERS = {'Authorization':'OWG-Session owner'}
BASE = '/v1/native-shadow-roster'


def inputs():
    manifest = package()
    return {'manifest_json':json.dumps(manifest), 'package_sha256':manifest['package_sha256'],
            'cases_json':json.dumps(cases()), 'confirm_freeze':True}


def test_exact_freeze_and_complete_review(monkeypatch):
    c = client(monkeypatch)
    response = c.post(BASE+'/freeze', json=inputs(), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    frozen = response.json(); plan = json.loads(frozen['plan_json'])
    assert plan['plan_sha256'] == frozen['plan_sha256'] and frozen['case_count'] == 3
    worklist = json.loads(frozen['worklist_json'])
    worklist[0]['root_execution_id'] = run(1)['execution_id']
    trace = snapshot(run(1, 'Write'))
    payload = {'plan_json':frozen['plan_json'], 'plan_sha256':frozen['plan_sha256'],
               'trace_json':json.dumps(trace), 'trace_sha256':digest(trace),
               'assignments_json':json.dumps(worklist)}
    response = c.post(BASE+'/review', json=payload, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    result = response.json(); report = json.loads(result['report_json'])
    assert result['review_sha256'] == report['review_sha256']
    assert report['case_count'] == 3 and report['counts']['not_assigned'] == 2
    assert report['counts']['observed_deviation_or_failure'] == 1
    assert report['business_outcomes_verified'] is False and result['execution_authorized'] is False
    for change in ({'plan_sha256':'0'*64},{'trace_sha256':'0'*64},
                   {'assignments_json':json.dumps(worklist[:-1])},{'trace_json':'{"x":1,"x":2}'}):
        assert c.post(BASE+'/review', json={**payload,**change}, headers=HEADERS).status_code == 422


def test_auth_confirmation_and_budgets(monkeypatch):
    c = client(monkeypatch)
    for headers in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
        for path in ('/freeze','/review'):
            assert c.post(BASE+path, json={}, headers=headers).status_code == 403
    for change in ({'confirm_freeze':False},{'package_sha256':'0'*64},
                   {'manifest_json':'{"x":1,"x":2}'},{'cases_json':'[NaN]'}):
        assert c.post(BASE+'/freeze', json={**inputs(),**change}, headers=HEADERS).status_code == 422
    monkeypatch.setattr(routes, 'MAX_SMALL', 1)
    assert c.post(BASE+'/freeze', json=inputs(), headers=HEADERS).status_code == 422
    monkeypatch.setattr(routes, 'MAX_REQUEST', 8)
    assert c.post(BASE+'/freeze', json=inputs(), headers=HEADERS).status_code == 413
