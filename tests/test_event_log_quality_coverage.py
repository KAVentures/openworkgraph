"""Coverage pages preserve counts-only diagnostics and detect appends."""
import io
import json
from datetime import datetime

import pytest
from server import event_log_quality as quality
from server import event_log_quality_coverage as coverage
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def make_log():
    text = b'case,activity,time\nc1,Start,2026-01-01T00:00:00Z\n'
    return workspace.import_log(io.BytesIO(text), name='Coverage', source_format='csv',
        mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})['log_id']


def test_pages_match_diagnostics_and_reject_stale_revision():
    log = make_log()
    conn = workspace._connect()
    try:
        conn.execute('UPDATE events SET attributes=? WHERE log_id=?', [json.dumps({f'key{i:02d}': f'PRIVATE{i}' for i in range(32)}), log])
        conn.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)', [log, 'second', 'Private activity', datetime(2026,1,2), '',
            json.dumps({**{f'key{i:02d}': None for i in range(32,39)}, 'zero': 0, 'false': False})])
        diag = quality.diagnostics(conn, log)
    finally:
        conn.close()
    first = coverage.page(log)
    second = coverage.page(log, first['next_offset'], first['revision'])
    assert first['total_keys'] == second['total_keys'] == 41
    assert first['attributes'] == diag['attribute_coverage']
    assert len(second['attributes']) == 9 and second['next_offset'] is None
    assert len({r['attribute'] for r in first['attributes'] + second['attributes']}) == 41
    counts = {r['attribute']: r['events_with_value'] for r in first['attributes'] + second['attributes']}
    assert counts['key32'] == 0 and counts['zero'] == counts['false'] == 1
    assert all(r['events_with_value'] <= 2 and r['events_without_value'] >= 0 for r in first['attributes'] + second['attributes'])
    assert 'PRIVATE' not in str(first) + str(second) and 'Private activity' not in str(first)
    assert coverage.page(log, 64, first['revision'])['total_keys'] == 41
    with pytest.raises(workspace.EventLogWorkspaceError):
        coverage.page(log, 32)
    workspace.append_log(log, io.BytesIO(b'case,activity,time\nc2,End,2026-01-03T00:00:00Z\n'), source_format='csv',
        mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})
    with pytest.raises(workspace.EventLogWorkspaceError, match='changed'):
        coverage.page(log, 32, first['revision'])


@pytest.mark.parametrize('offset,revision', [(-1,None),(True,None),(1,None),(0,True),(0,-1),(2_147_483_648,0)])
def test_invalid_pages_are_refused(offset, revision):
    with pytest.raises(workspace.EventLogWorkspaceError):
        coverage.page(make_log(), offset, revision)


def test_dashboard_route_requires_session_and_valid_page():
    from fastapi.testclient import TestClient
    from server.secure_app import app
    from server import local_auth
    log = make_log()
    url = '/v1/event-logs/' + log + '/quality/attribute-coverage'
    headers = {'Authorization': 'OWG-Session ' + local_auth.create_dashboard_session()}
    with TestClient(app) as client:
        assert client.get(url).status_code in (401, 403)
        assert client.get(url, headers={'Authorization':'Bearer '+local_auth.ensure_api_token()}).status_code in (401, 403)
        assert client.get(url+'?offset=-1', headers=headers).status_code == 422
        assert client.get(url+'?offset=32', headers=headers).status_code == 422
        response = client.get(url, headers=headers)
        assert response.status_code == 200 and response.json()['attributes'] == []
        assert response.headers['cache-control'] == 'no-store'
