import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import ai_opportunity_routes as routes
from test_ai_implementation_trial_handoff import payload
from test_ai_implementation_context_trial import server

BASE = '/v1/ai-opportunity-handoff/context-trial'
HEADERS = {'Authorization': 'OWG-Session owner'}


def client(monkeypatch):
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'owner')
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def request(port):
    p = payload(port)
    return {**p, 'sources': [json.dumps(s) for s in p['sources']]}


def test_trial_api_requires_owner_and_current_pins(monkeypatch, server):
    c = client(monkeypatch)
    port, calls = server()
    p = request(port)
    for headers in ({}, {'Authorization': 'Bearer owner'}, {'Authorization': 'OWG-Session wrong'}):
        assert c.post(BASE, json=p, headers=headers).status_code == 403
    for change in ({'confirm_two_requests': False}, {'expected_packet_sha256': '0'*64},
                   {'expected_compact_sha256': '0'*64}, {'sources': ['{"x":1,"x":2}']}):
        assert c.post(BASE, json={**p, **change}, headers=HEADERS).status_code == 422
    assert routes._LOCAL_MODEL_SLOT.acquire(blocking=False)
    try:
        assert c.post(BASE, json=p, headers=HEADERS).status_code == 409
    finally:
        routes._LOCAL_MODEL_SLOT.release()
    assert not calls
    result = c.post(BASE, json=p, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert result.headers['cache-control'] == 'no-store'
    assert [arm for arm, _ in calls] == ['compact', 'original']
    data = result.json()
    report = json.loads(data['trial_json'])
    assert report['structurally_validated_count'] == 2 and report['execution_authorized'] is False
    assert data['trial_sha256'] == report['trial_sha256']
    assert routes._LOCAL_MODEL_SLOT.acquire(blocking=False)
    routes._LOCAL_MODEL_SLOT.release()


def test_trial_api_retains_failed_arm_and_budget(monkeypatch, server):
    c = client(monkeypatch)
    port, calls = server(failure='compact')
    p = request(port)
    result = c.post(BASE, json=p, headers=HEADERS)
    assert result.status_code == 200, result.text
    data = result.json()
    assert json.loads(data['trial_json'])['structurally_validated_count'] == 1
    assert data['proposal_json']['compact'] is None
    assert len(calls) == 2
    monkeypatch.setattr(routes, 'MAX_BYTES', 8)
    assert c.post(BASE, json=p, headers=HEADERS).status_code == 413
    assert len(calls) == 2
