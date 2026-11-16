"""Quality overview bounds stored history while preserving the current snapshot."""
from io import BytesIO
import json
import os
import subprocess
import sys

from server import event_log_quality as quality
from server import event_log_workspace as workspace


def test_overview_preserves_original_mapping_and_current_digests_with_many_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    mapping = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'}
    log = workspace.import_log(BytesIO(b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n'),
                               name='overview', source_format='csv', mapping=mapping)['log_id']
    conn = workspace._connect()
    try:
        conn.execute('''INSERT INTO log_appends
            SELECT ?, i, TIMESTAMP '2024-01-01', 'csv', 1, 0, 0, 0 FROM range(1, 206) t(i)''', [log])
        conn.execute('''INSERT INTO log_quality
            SELECT log_id, i, created_at, json_merge_patch(report, json_object('batch', i, 'kind', 'append'))
            FROM log_quality, range(1, 206) t(i) WHERE log_id = ? AND batch = 0 AND i != 150''', [log])
    finally:
        conn.close()
    full, overview = quality.quality(log), quality.overview(log)
    assert overview['window']['total_batches'] == 206 and overview['window']['batch_count'] == 100
    assert len(overview['reports']) == 99 and overview['batches_without_report'] == [150]
    assert overview['first_report'] == full['reports'][0]
    assert overview['first_report']['transform']['mapping']['case_column'] == 'case'
    assert overview['current']['content_sha256'] == full['current']['content_sha256']
    assert overview['current']['content_sha256_v2'] == full['current']['content_sha256_v2']
    assert overview['current']['diagnostics']['events'] == full['current']['diagnostics']['events']
    assert 'batch_trends' not in overview
    assert quality.quality(log)['batch_trends'] == full['batch_trends']
    conn = workspace._connect()
    try:
        conn.execute('DELETE FROM log_quality WHERE log_id = ? AND batch = 0', [log])
    finally:
        conn.close()
    assert quality.overview(log)['first_report']['batch'] == 1


def test_overview_owner_only_and_no_store(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content=b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n',headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    url='/v1/event-logs/'+made.json()['log_id']+'/quality/overview'
    assert client.get(url).status_code in (401,403)
    assert client.get(url,headers=bearer).status_code in (401,403)
    response=client.get(url,headers=owner)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert response.json()['window']['batch_count']==1 and response.json()['first_report']['batch']==0
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
