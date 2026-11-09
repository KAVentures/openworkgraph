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
    snapshot = client.get('/v1/review-choice-workbench/idea-1', headers={'Authorization': 'OWG-Session test-session'}).json()
    payload = {'project_id': 'p1', 'project_set_sha256': snapshot['project_set_sha256'], 'idea_revision': 3, 'idea_state': 'approved', 'spec_sha256': 'b'*64,
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
    assert client.post(path, json={**payload, 'idea_state': 'implemented'}, headers=headers).status_code == 409
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


def test_preflight_continue_uses_existing_pipeline_decision_not_implicit_approval():
    """The UX bridge must invoke the actual pipeline, never issue a grant."""
    script = (Path(__file__).resolve().parents[1] / 'dashboard/review_choice_workbench.js').read_text()
    pipeline = (Path(__file__).resolve().parents[1] / 'dashboard/automation_pipeline.js').read_text()
    assert "Open opportunity decision and handoff" in script
    assert "new CustomEvent('owg-pipeline-open',{detail:{idea_id:id,project_id:projectId" in script
    assert "window.addEventListener('owg-pipeline-open'" in pipeline
    assert "if(!journeyResponse.ok)throw new Error" in pipeline
    assert "if(!button)throw new Error" in pipeline
    assert "if(current()){detail.replaceChildren();" in pipeline
    assert "if(token!==generation||choice.value!==projectId||input.value.trim()!==id)return;" in script
    assert "if(!pipelineCard)" in script
    assert "details.append(continueReview);" in script
    assert "execution_authorized:false" in pipeline


def test_oversized_project_history_rejected_before_review_or_preflight(monkeypatch):
    """Even a falsely untruncated journey cannot be used as a partial approval basis."""
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'test-session')
    count = {'projects': 65}
    def journey(idea_id):
        return {'idea': {'idea_id': idea_id, 'revision': 3, 'state': 'approved'},
                'process_status': {'status': 'current', 'process_reference': {}},
                'projects': [{'project_id': f'p{i}', 'idea_revision': 3,
                              'idea_revision_is_current': True, 'spec_sha256': 'b'*64,
                              'archive_sha256': 'c'*64, 'reported_specifications': []}
                             for i in range(count['projects'])],
                'projects_truncated': False, 'dataset_origins': [],
                'dataset_origin_check_available': True}
    monkeypatch.setattr(routes.opportunity_journey, 'journey', journey)
    headers = {'Authorization': 'OWG-Session test-session'}
    url = '/v1/review-choice-workbench/idea-1'
    assert client.get(url, headers=headers).status_code == 409
    payload = {'project_id': 'p0', 'project_set_sha256': '0'*64, 'idea_revision': 3, 'idea_state': 'approved', 'spec_sha256': 'b'*64,
               'archive_sha256': 'c'*64, 'process_status': 'current',
               'process_reference': {}, 'dataset_origins': [],
               'dataset_origin_check_available': True,
               'blockers': ['dataset_origin_not_linked', 'no_customer_report']}
    assert client.post(url+'/preflight', headers=headers, json=payload).status_code == 409
    count['projects'] = 64
    assert client.get(url, headers=headers).status_code == 200


def test_preflight_detects_other_project_mutation_and_recovers(monkeypatch):
    """Selected project unchanged is insufficient when another reviewed export changes."""
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'test-session')
    state = {'other': 'd'*64}
    def journey(idea_id):
        def project(pid, digest):
            return {'project_id': pid, 'idea_revision': 3, 'idea_revision_is_current': True,
                    'spec_sha256': digest, 'archive_sha256': 'c'*64,
                    'reported_specifications': []}
        return {'idea': {'idea_id': idea_id, 'revision': 3, 'state': 'approved'},
                'process_status': {'status': 'current', 'process_reference': {}},
                'projects': [project('p1', 'b'*64), project('p2', state['other'])],
                'projects_truncated': False, 'dataset_origins': [],
                'dataset_origin_check_available': True}
    monkeypatch.setattr(routes.opportunity_journey, 'journey', journey)
    headers = {'Authorization': 'OWG-Session test-session'}
    path = '/v1/review-choice-workbench/idea-1'
    snapshot = client.get(path, headers=headers).json()
    payload = {'project_id': 'p1', 'project_set_sha256': snapshot['project_set_sha256'],
               'idea_revision': 3, 'idea_state': 'approved', 'spec_sha256': 'b'*64, 'archive_sha256': 'c'*64,
               'process_status': 'current', 'process_reference': {},
               'dataset_origins': [], 'dataset_origin_check_available': True,
               'blockers': ['dataset_origin_not_linked', 'no_customer_report']}
    assert client.post(path+'/preflight', headers=headers, json=payload).status_code == 200
    state['other'] = 'e'*64
    assert client.post(path+'/preflight', headers=headers, json=payload).status_code == 409
    new_snapshot = client.get(path, headers=headers).json()
    assert new_snapshot['project_set_sha256'] != snapshot['project_set_sha256']
    payload['project_set_sha256'] = new_snapshot['project_set_sha256']
    assert client.post(path+'/preflight', headers=headers, json=payload).status_code == 200
    assert client.post(path+'/preflight', json=payload).status_code == 403


def test_review_preflight_rejects_state_change_without_revision_bump(monkeypatch):
    """A state transition cannot inherit an approval basis even with unchanged hashes."""
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'test-session')
    state = {'value': 'approved'}
    def journey(idea_id):
        return {'idea': {'idea_id': idea_id, 'revision': 3, 'state': state['value']},
                'process_status': {'status': 'current', 'process_reference': {}},
                'projects': [{'project_id': 'p1', 'idea_revision': 3, 'idea_revision_is_current': True,
                              'spec_sha256': 'b'*64, 'archive_sha256': 'c'*64, 'reported_specifications': []}],
                'projects_truncated': False, 'dataset_origins': [], 'dataset_origin_check_available': True}
    monkeypatch.setattr(routes.opportunity_journey, 'journey', journey)
    headers = {'Authorization': 'OWG-Session test-session'}
    path = '/v1/review-choice-workbench/idea-1'
    inspected = client.get(path, headers=headers).json()
    payload = {'project_id': 'p1', 'project_set_sha256': inspected['project_set_sha256'],
               'idea_revision': 3, 'idea_state': 'approved', 'spec_sha256': 'b'*64,
               'archive_sha256': 'c'*64, 'process_status': 'current', 'process_reference': {},
               'dataset_origins': [], 'dataset_origin_check_available': True,
               'blockers': ['dataset_origin_not_linked', 'no_customer_report']}
    assert client.post(path+'/preflight', headers=headers, json=payload).status_code == 200
    state['value'] = 'implemented'
    assert client.post(path+'/preflight', headers=headers, json=payload).status_code == 409
    payload['idea_state'] = 'implemented'
    assert client.post(path+'/preflight', headers=headers, json=payload).status_code == 200
    assert client.post(path+'/preflight', json=payload).status_code == 403


def test_oversized_customer_report_history_fails_closed_and_recovers(monkeypatch):
    """Do not silently omit customer report mismatches beyond the 64-item review cap."""
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'test-session')
    state = {'count': 65}
    def journey(idea_id):
        return {'idea': {'idea_id': idea_id, 'revision': 3, 'state': 'approved'},
                'process_status': {'status': 'current', 'process_reference': {}},
                'projects': [{'project_id': 'p1', 'idea_revision': 3,
                              'idea_revision_is_current': True, 'spec_sha256': 'b'*64,
                              'archive_sha256': 'c'*64,
                              'reported_specifications': [
                                  {'status': 'matching_reported_specification'}
                                  for _ in range(state['count'])]}],
                'projects_truncated': False, 'dataset_origins': [],
                'dataset_origin_check_available': True}
    monkeypatch.setattr(routes.opportunity_journey, 'journey', journey)
    path = '/v1/review-choice-workbench/idea-1'
    headers = {'Authorization': 'OWG-Session test-session'}
    assert client.get(path, headers=headers).status_code == 409
    assert client.post(path+'/preflight', headers=headers, json={
        'project_id': 'p1', 'project_set_sha256': '0'*64,
        'idea_revision': 3, 'idea_state': 'approved',
        'spec_sha256': 'b'*64, 'archive_sha256': 'c'*64,
        'process_status': 'current', 'process_reference': {},
        'dataset_origins': [], 'dataset_origin_check_available': True,
        'blockers': ['dataset_origin_not_linked']}).status_code == 409
    state['count'] = 64
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json()['projects'][0]['blockers'] == ['dataset_origin_not_linked']
    assert client.get(path).status_code == 403


def test_matching_customer_report_change_invalidates_review_basis(monkeypatch):
    """Changed report evidence must invalidate preflight even if blockers stay identical."""
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    monkeypatch.setattr(routes, 'dashboard_session_valid', lambda token: token == 'test-session')
    state = {'report_sha': 'd' * 64}
    def journey(idea_id):
        return {'idea': {'idea_id': idea_id, 'revision': 3, 'state': 'approved'},
                'process_status': {'status': 'current', 'process_reference': {}},
                'projects': [{'project_id': 'p1', 'idea_revision': 3,
                              'idea_revision_is_current': True, 'spec_sha256': 'b'*64,
                              'archive_sha256': 'c'*64,
                              'reported_specifications': [{
                                  'status': 'matching_reported_specification',
                                  'report_sha256': state['report_sha']}]}],
                'projects_truncated': False, 'dataset_origins': [],
                'dataset_origin_check_available': True}
    monkeypatch.setattr(routes.opportunity_journey, 'journey', journey)
    headers = {'Authorization': 'OWG-Session test-session'}
    path = '/v1/review-choice-workbench/idea-1'
    first = client.get(path, headers=headers).json()
    assert first['projects'][0]['blockers'] == ['dataset_origin_not_linked']
    payload = {'project_id': 'p1', 'project_set_sha256': first['project_set_sha256'],
               'idea_revision': 3, 'idea_state': 'approved', 'spec_sha256': 'b'*64,
               'archive_sha256': 'c'*64, 'process_status': 'current',
               'process_reference': {}, 'dataset_origins': [],
               'dataset_origin_check_available': True,
               'blockers': ['dataset_origin_not_linked']}
    assert client.post(path+'/preflight', json=payload, headers=headers).status_code == 200
    state['report_sha'] = 'e' * 64
    second = client.get(path, headers=headers).json()
    assert second['projects'][0]['blockers'] == first['projects'][0]['blockers']
    assert second['project_set_sha256'] != first['project_set_sha256']
    assert client.post(path+'/preflight', json=payload, headers=headers).status_code == 409
    payload['project_set_sha256'] = second['project_set_sha256']
    assert client.post(path+'/preflight', json=payload, headers=headers).status_code == 200
    assert client.post(path+'/preflight', json=payload).status_code == 403


def test_emergency_review_revocation_survives_stale_evidence(monkeypatch, tmp_path):
    """A user must be able to withdraw approval after provenance disappears."""
    import sqlite3
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    db = tmp_path / "review.sqlite3"
    monkeypatch.setattr(routes.pipeline, "_connect", lambda: sqlite3.connect(db))
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "test-session")
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE project_human_reviews (
            idea_id TEXT NOT NULL, project_id TEXT NOT NULL,
            revision INTEGER NOT NULL, decision TEXT NOT NULL,
            basis_sha256 TEXT NOT NULL, reviewer_notes TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(idea_id, project_id))""")
        conn.execute("INSERT INTO project_human_reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ("idea-1", "p1", 4, "approved", "a"*64, "review notes", "now"))
    def stale(_):
        raise routes.pipeline.PipelineError("unknown idea")
    monkeypatch.setattr(routes.opportunity_journey, "journey", stale)
    path = "/v1/review-choice-workbench/idea-1/review/p1/revoke"
    headers = {"Authorization": "OWG-Session test-session"}
    assert client.post(path, json={"expected_review_revision": 4}).status_code == 403
    assert client.post(path, json={"expected_review_revision": 3}, headers=headers).status_code == 409
    response = client.post(path, json={"expected_review_revision": 4}, headers=headers)
    assert response.status_code == 200
    assert response.json()["decision"] == "revoked"
    assert response.json()["execution_authorized"] is False
    assert response.json()["revision"] == 5
    assert client.post(path, json={"expected_review_revision": 4}, headers=headers).status_code == 409
    assert client.post(path, json={"expected_review_revision": 5}, headers=headers).status_code == 409
    assert client.post(path.replace("/p1/", "/missing/"),
                       json={"expected_review_revision": 1}, headers=headers).status_code == 404
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT decision, revision, reviewer_notes FROM project_human_reviews").fetchone()
    assert row == ("revoked", 5, "review notes")


def test_saved_review_recovery_ui_uses_authenticated_revision_checked_revocation():
    """Recovery remains accessible without an inspectable opportunity."""
    script = (Path(__file__).resolve().parents[1] / 'dashboard/review_choice_workbench.js').read_text()
    assert "Recover or revoke a saved review" in script
    assert "card.append(heading,intro,label,input,load,status,results,details,recovery)" in script
    assert "const state=await request(recoveryPath(idea,project))" in script
    assert "await window.__owgAuthReady" in script
    assert "expected_review_revision:revision" in script
    assert "state.revision!==revision+1" in script
    assert "recoveryIdea.addEventListener('input',resetRecovery)" in script
    assert "recoveryProject.addEventListener('input',resetRecovery)" in script
    assert "recoveryRevoke.disabled=state.revision===0||state.decision==='revoked'" in script


def test_review_load_does_not_race_with_save_or_revocation():
    """The browser must not enable writes before loading the persisted revision.

    A late initial read must not replace the newer revision returned by a
    mutation, and a failed initial read must leave mutation controls disabled.
    """
    script = (Path(__file__).resolve().parents[1] /
              'dashboard/review_choice_workbench.js').read_text()
    assert 'let reviewRequestGeneration=0;' in script
    assert 'for(const b of [saveDraft,approve,revoke])b.disabled=true;' in script
    assert 'const requestGeneration=++reviewRequestGeneration;' in script
    assert 'requestGeneration!==reviewRequestGeneration' in script
    assert script.count('++reviewRequestGeneration;') >= 3
    assert "refreshReview().catch(e=>{if(reviewStillSelected()){reviewState.textContent=e.message;for(const b of [saveDraft,approve,revoke])b.disabled=true;}});" in script
