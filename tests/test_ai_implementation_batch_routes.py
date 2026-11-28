import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import ai_implementation_batch_routes as routes
from shared import ai_implementation_review as contract
from test_ai_implementation_batch import setup

BASE = '/v1/ai-opportunity-handoff/'
HEADERS = {'Authorization': 'OWG-Session owner'}


def client(monkeypatch):
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'owner')
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def inputs():
    sources, pin, proposals = setup()
    return {'sources': [json.dumps(s) for s in sources], 'packet_sha256': pin,
            'proposals_json': json.dumps(proposals)}, proposals


def test_exact_batch_and_individual_selection(monkeypatch):
    c = client(monkeypatch)
    p, proposals = inputs()
    r = c.post(BASE+'batch-review', json=p, headers=HEADERS)
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    data = r.json(); report = json.loads(data['report_json'])
    assert report['item_count'] == 6 and report['counts']['structural_review_refused'] == 2
    assert report['counts']['exact_duplicate'] == 2
    assert data['report_json'] == contract._json(report).decode()
    select = {**p, 'batch_review_sha256': data['batch_review_sha256'], 'item_index': 2}
    r = c.post(BASE+'batch-select', json=select, headers=HEADERS)
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    d = r.json()
    assert d['proposal_json'] == contract._json(proposals[2]).decode()
    assert d['proposal_sha256'] == report['items'][2]['proposal_sha256']
    assert d['portfolio_written'] is False and d['execution_authorized'] is False
    for change in ({'item_index': 1}, {'item_index': 3}, {'item_index': True},
                   {'batch_review_sha256': '0'*64}, {'proposals_json': json.dumps(proposals[::-1])}):
        assert c.post(BASE+'batch-select', json={**select, **change}, headers=HEADERS).status_code == 422


def test_auth_invalid_documents_and_bounds(monkeypatch):
    c = client(monkeypatch)
    p, _ = inputs()
    for headers in ({}, {'Authorization': 'Bearer owner'}, {'Authorization': 'OWG-Session wrong'}):
        for path in ('batch-review', 'batch-select'):
            assert c.post(BASE+path, json=p, headers=headers).status_code == 403
    for change in ({'packet_sha256': '0'*64}, {'proposals_json': '[{"x":1,"x":2}]'},
                   {'proposals_json': '[NaN]'}, {'proposals_json': ' '* (routes.batch.MAX_BATCH_BYTES+1)},
                   {'sources': ['{"x":1,"x":2}']}):
        assert c.post(BASE+'batch-review', json={**p, **change}, headers=HEADERS).status_code == 422
    monkeypatch.setattr(routes, 'MAX_REQUEST', 8)
    assert c.post(BASE+'batch-review', json=p, headers=HEADERS).status_code == 413
