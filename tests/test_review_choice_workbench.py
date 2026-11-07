"""Authenticated review-choice route and fail-closed status contract."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import review_choice_routes as routes

def test_review_choice_workbench_contract():
    root = Path(__file__).resolve().parents[1]
    script = (root / 'dashboard/review_choice_workbench.js').read_text()
    assert 'Review basis changed. Inspect again' in script
    assert 'window.__owgAuthReady' in script
    assert 'fresh.process_reference' in script
    assert 'fresh.dataset_origins' in script

def test_authenticated_choice_and_review_blockers(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'test-session')
    calls = []
    def journey(idea_id):
        calls.append(idea_id)
        return {
            'idea': {'idea_id': idea_id, 'revision': 3, 'state': 'approved'},
            'process_status': {'status': 'changed', 'process_reference': {'workflow_id': 'wf', 'revision': 2, 'content_sha256': 'a'*64}},
            'projects': [{'project_id': 'p1', 'idea_revision': 2, 'idea_revision_is_current': False,
                          'spec_sha256': 'b'*64, 'archive_sha256': 'c'*64, 'reported_specifications': []}],
            'projects_truncated': False, 'dataset_origins': [],
            'dataset_origin_check_available': True,
        }
    monkeypatch.setattr(routes.opportunity_journey, 'journey', journey)
    path = '/v1/review-choice-workbench/idea-1'
    assert client.get(path).status_code == 403
    assert not calls
    response = client.get(path, headers={'Authorization': 'OWG-Session test-session'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    data = response.json()
    assert data['execution_authorized'] is False
    assert data['source_read_authorized'] is False
    assert data['verified_business_outcomes'] is False
    assert set(data['projects'][0]['blockers']) == {
        'saved_process_not_current', 'historical_idea_revision',
        'dataset_origin_not_linked', 'no_customer_report'
    }
    assert client.get('/v1/review-choice-workbench/'+'x'*129,
                      headers={'Authorization': 'OWG-Session test-session'}).status_code == 422


def test_review_refresh_fail_closed_browser_contract():
    """A failed or inconsistent second fetch must invalidate the prior choice."""
    script = (Path(__file__).resolve().parents[1] / 'dashboard/review_choice_workbench.js').read_text()
    assert "data.source_read_authorized!==false" in script
    assert "data.verified_business_outcomes!==false" in script
    assert "fresh.execution_authorized!==false" in script
    assert "fresh.source_read_authorized!==false" in script
    assert "fresh.verified_business_outcomes!==false" in script
    assert "fresh.idea?.idea_id!==id" in script
    assert "Review failed. Inspect current records again before choosing." in script
    assert "}catch(e){if(current===generation){clear();" in script


def test_unknown_idea_and_invalid_identifier_fail_closed(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'test-session')
    def unknown(_):
        raise routes.pipeline.PipelineError('unknown idea')
    monkeypatch.setattr(routes.opportunity_journey, 'journey', unknown)
    headers = {'Authorization': 'OWG-Session test-session'}
    assert client.get('/v1/review-choice-workbench/unknown', headers=headers).status_code == 404
    assert client.get('/v1/review-choice-workbench/' + 'x'*129, headers=headers).status_code == 422
    assert client.get('/v1/review-choice-workbench/unknown').status_code == 403


def test_project_switch_invalidates_previously_inspected_review():
    """A different export must not inherit the preceding export's reviewed evidence."""
    script = (Path(__file__).resolve().parents[1] / 'dashboard/review_choice_workbench.js').read_text()
    assert "choice.addEventListener('change',()=>{generation++;" in script
    assert "details.replaceChildren();review.disabled=!snapshot;" in script
    assert "choice.value!==projectId||input.value.trim()!==id" in script
    assert "current===generation&&snapshot&&choice.value===projectId" in script


def test_live_review_preflight_is_authenticated_and_revision_pinned(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'test-session')
    state = {'revision': 3, 'digest': 'b'*64, 'truncated': False, 'process': 'current'}
    def journey(idea_id):
        return {'idea': {'idea_id': idea_id, 'revision': state['revision'], 'state': 'approved'},
                'process_status': {'status': state['process'], 'process_reference': {}},
                'projects': [{'project_id': 'p1', 'idea_revision': state['revision'],
                              'idea_revision_is_current': True, 'spec_sha256': state['digest'],
                              'archive_sha256': 'c'*64, 'reported_specifications': []}],
                'projects_truncated': state['truncated'], 'dataset_origins': [],
                'dataset_origin_check_available': True}
    monkeypatch.setattr(routes.opportunity_journey, 'journey', journey)
    path = '/v1/review-choice-workbench/idea-1/preflight'
    payload = {'project_id': 'p1', 'idea_revision': 3, 'spec_sha256': 'b'*64,
               'archive_sha256': 'c'*64, 'process_status': 'current',
               'process_reference': {}, 'dataset_origins': [],
               'dataset_origin_check_available': True,
               'blockers': ['dataset_origin_not_linked', 'no_customer_report']}
    assert client.post(path, json=payload).status_code == 403
    headers = {'Authorization': 'OWG-Session test-session'}
    ok = client.post(path, json=payload, headers=headers)
    assert ok.status_code == 200
    assert ok.headers['cache-control'] == 'no-store'
    assert ok.json()['execution_authorized'] is False
    assert ok.json()['review_required'] is True
    assert 'no_customer_report' in ok.json()['blockers']
    state['revision'] = 4
    assert client.post(path, json=payload, headers=headers).status_code == 409
    state['revision'] = 3
    state['digest'] = 'd'*64
    assert client.post(path, json=payload, headers=headers).status_code == 409
    state['digest'] = 'b'*64
    state['truncated'] = True
    assert client.post(path, json=payload, headers=headers).status_code == 409
    state['truncated'] = False
    state['process'] = 'changed'
    assert client.post(path, json=payload, headers=headers).status_code == 409
    state['process'] = 'current'
    assert client.post(path, json={**payload, 'blockers': []}, headers=headers).status_code == 409
    assert client.post(path, json={**payload, 'dataset_origins': [{'status': 'current'}]}, headers=headers).status_code == 409
    assert client.post(path, json={**payload, 'spec_sha256': 'bad'}, headers=headers).status_code == 422
    assert client.post(path, json={**payload, 'project_id': 'missing'}, headers=headers).status_code == 409


def test_ambiguous_project_and_oversized_provenance_fail_closed(monkeypatch):
    """Never silently truncate provenance or select between duplicate exports."""
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'test-session')
    state = {'origins': 65, 'duplicates': False}
    def journey(idea_id):
        project = {'project_id': 'p1', 'idea_revision': 3, 'idea_revision_is_current': True,
                   'spec_sha256': 'b'*64, 'archive_sha256': 'c'*64,
                   'reported_specifications': []}
        return {'idea': {'idea_id': idea_id, 'revision': 3, 'state': 'approved'},
                'process_status': {'status': 'current', 'process_reference': {}},
                'projects': [project, dict(project)] if state['duplicates'] else [project],
                'projects_truncated': False,
                'dataset_origins': [{'status': 'current'}] * state['origins'],
                'dataset_origin_check_available': True}
    monkeypatch.setattr(routes.opportunity_journey, 'journey', journey)
    headers = {'Authorization': 'OWG-Session test-session'}
    path = '/v1/review-choice-workbench/idea-1'
    assert client.get(path, headers=headers).status_code == 409
    state['origins'] = 1
    state['duplicates'] = True
    assert client.get(path, headers=headers).status_code == 409
    state['duplicates'] = False
    ok = client.get(path, headers=headers)
    assert ok.status_code == 200
    assert len(ok.json()['dataset_origins']) == 1
