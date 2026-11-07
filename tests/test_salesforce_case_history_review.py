"""Dashboard-session Salesforce export review is bounded and does not store logs."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_review_endpoint_auth_consent_projection_and_no_storage(tmp_path):
    program = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

case = '500000000000001'
csv = ('Id,CaseId,CreatedDate,Field,NewValue,OldValue\n'
       '017000000000001,500000000000001,2026-01-01T00:00:00Z,created,,secret_old\n'
       '017000000000002,500000000000001,2026-01-01T01:00:00Z,Status,Closed,secret_old\n'
       '017000000000003,500000000000001,invalid,Description,secret_description,\n'
       '017000000000004,500000000000002,invalid,Status,secret_unselected,\n')
payload = {'account':'sandbox','cases':[case], 'csv':csv,'confirm_processing':True}
url = '/v1/event-logs/prepare-salesforce'
with TestClient(app) as client:
    session = {'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer = {'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.post(url,json=payload).status_code in (401,403)
    assert client.post(url,json=payload,headers=bearer).status_code in (401,403)
    assert client.post(url,json={**payload,'confirm_processing':False},headers=session).status_code == 422
    assert client.post(url,json={**payload,'unexpected':True},headers=session).status_code == 422
    assert client.post(url,content='not-json',headers={**session,'Content-Type':'text/plain'}).status_code == 415
    assert client.post(url,json={**payload,'cases':['not-a-case']},headers=session).status_code == 422
    response = client.post(url,json=payload,headers=session)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    result = response.json()
    assert result['persisted'] is False and result['report']['events'] == 2
    assert result['report']['excluded_fields'] == 1 and result['report']['unselected_rows'] == 1
    assert len(result['preview']) == 2 and result['preview_omitted'] == 0
    assert all(x['case_id'].startswith('sf:') for x in result['preview'])
    assert 'secret_' not in response.text and case not in response.text
    assert result['namespace'].startswith('sfexp_') and len(result['namespace']) == 38
    assert client.post(url,json=payload,headers=session).json()['namespace'] == result['namespace']
    assert client.post(url,json={**payload,'account':'another'},headers=session).json()['namespace'] != result['namespace']
    assert client.get('/v1/event-logs',headers=session).json()['logs'] == []
    rows = ''.join(f'{i:015d},{case},2026-01-01T00:00:00Z,Status,New,\n' for i in range(25))
    large = client.post(url,json={**payload,'csv':'Id,CaseId,CreatedDate,Field,NewValue,OldValue\n'+rows},headers=session).json()
    assert len(large['preview']) == 20 and large['preview_omitted'] == 5
'''
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path / 'data'),
               WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path / 'auth'),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path / 'config.json'))
    run = subprocess.run([sys.executable, '-c', program], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, f'stdout={run.stdout}\nstderr={run.stderr}'
