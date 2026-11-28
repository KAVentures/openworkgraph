import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import native_shadow_review_routes as routes
from shared.native_shadow_trace_review import digest
from test_native_shadow_trace_review import trace, run, declaration


def client(monkeypatch, evidence):
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda value: value == 'owner')
    calls = []
    def reader(request, since, until, include):
        calls.append((since, until, include))
        return evidence
    monkeypatch.setattr(routes, 'read_trace', reader)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), calls


HEADERS = {'Authorization': 'OWG-Session owner'}
QUERY = '?since=2026-11-28T10:00:00Z&until=2026-11-28T11:00:00Z'
BASE = '/v1/native-shadow-review'


def test_exact_snapshot_and_review(monkeypatch):
    evidence = trace(run(1, states=[('tool_call','success','Write')]))
    evidence['evidence_rows_considered'] = routes.ROW_LIMIT
    c, calls = client(monkeypatch, evidence)
    response = c.get(BASE+'/snapshot'+QUERY, headers=HEADERS)
    assert response.status_code == 200, response.text
    s = response.json()
    assert response.headers['cache-control'] == 'no-store'
    assert json.loads(s['trace_json']) == evidence and s['trace_sha256'] == digest(evidence)
    assert s['source_row_limit_may_have_been_reached'] and s['execution_authorized'] is False
    assert calls == [('2026-11-28T10:00:00Z','2026-11-28T11:00:00Z',False)]
    payload = {'trace_json':s['trace_json'],'trace_sha256':s['trace_sha256'],'declaration':declaration(1)}
    response = c.post(BASE+'/review',json=payload,headers=HEADERS)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['cases'][0]['status'] == 'observed_deviation_or_failure'
    assert result['execution_authorized'] is False and result['skill_invocation_verified'] is False
    assert response.headers['cache-control'] == 'no-store' and len(calls) == 1
    assert c.post(BASE+'/review',json={**payload,'trace_sha256':'f'*64},headers=HEADERS).status_code == 422
    assert c.post(BASE+'/review',json={**payload,'trace_json':'{"x":1,"x":2}'},headers=HEADERS).status_code == 422


def test_dashboard_auth_and_window(monkeypatch):
    c,calls = client(monkeypatch, {})
    for headers in ({},{'Authorization':'Bearer owner'},{'Authorization':'OWG-Session wrong'}):
        assert c.get(BASE+'/snapshot'+QUERY,headers=headers).status_code == 403
        assert c.post(BASE+'/review',json={},headers=headers).status_code == 403
    for q in ('?since=2026-11-28&until=2026-11-29','?since=2026-11-29T00:00:00Z&until=2026-11-28T00:00:00Z',
              '?since=2026-11-01T00:00:00Z&until=2026-11-28T00:00:00Z'):
        assert c.get(BASE+'/snapshot'+q,headers=HEADERS).status_code == 422
    assert not calls


def test_empty_and_budget_refusal(monkeypatch):
    evidence = {'executions':[],'returned':0,'evidence_rows_considered':0}
    c,_ = client(monkeypatch,evidence)
    assert c.get(BASE+'/snapshot'+QUERY,headers=HEADERS).json()['returned'] == 0
    monkeypatch.setattr(routes,'MAX_TRACE_BYTES',1)
    assert c.get(BASE+'/snapshot'+QUERY,headers=HEADERS).status_code == 413
    monkeypatch.setattr(routes,'MAX_REQUEST_BYTES',8)
    assert c.post(BASE+'/review',json={'a':'x'*20},headers=HEADERS).status_code == 413
