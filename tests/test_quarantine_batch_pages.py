"""Quarantine batches are paged without losing scoped row counts."""
from io import BytesIO
import os
import subprocess
import sys

import pytest
from server import event_log_quarantine as quarantine
from server import event_log_workspace as workspace

DOC = b'case,activity,time\nsecret,A,2024-01-01T00:00:00Z\nsecret,B,2024-01-01 00:00:00\n'
MAPPING = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time', 'keep_rejected': True}


def test_batch_pages_preserve_scoped_counts_and_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    log = workspace.import_log(BytesIO(DOC), name='one', source_format='csv', mapping=MAPPING)['log_id']
    other = workspace.import_log(BytesIO(DOC), name='two', source_format='csv', mapping=MAPPING)['log_id']
    conn = workspace._connect()
    try:
        conn.execute('''INSERT INTO quarantine SELECT log_id, i, 1, reasons, row
            FROM quarantine, range(1, 205) t(i) WHERE log_id = ? AND batch = 0''', [log])
    finally:
        conn.close()
    overview = quarantine.quarantine(log)
    assert overview['rows_kept'] == 205 and len(overview['batches']) == 100
    assert overview['batches_total'] == 205 and overview['batches_next_after'] == 99
    assert overview['batches_stable_snapshot'] is False
    assert quarantine.quarantine(log, batch=150)['rows_kept'] == 1
    pages = [quarantine.batch_history(log, after) for after in (-1, 99, 199, 204)]
    assert [len(page['batches']) for page in pages] == [100, 100, 5, 0]
    assert [page['next_after'] for page in pages] == [99, 199, None, None]
    assert [b['batch'] for page in pages for b in page['batches']] == list(range(205))
    assert all(page['total'] == 205 and page['stable_snapshot'] is False for page in pages)
    assert 'secret' not in str(pages)
    assert quarantine.batch_history(other)['total'] == 1
    for after, limit in [(-2, 1), (True, 1), (0, 0), (0, 101), (0, True), (2**31, 1)]:
        with pytest.raises(workspace.EventLogWorkspaceError):
            quarantine.batch_history(log, after, limit)


def test_owner_only_http_and_validation(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time&keep_rejected=true'
    made=client.post(url,content=b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n',headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    url='/v1/event-logs/'+made.json()['log_id']+'/quarantine/batches'
    assert client.get(url).status_code in (401,403)
    assert client.get(url,headers=bearer).status_code in (401,403)
    response=client.get(url,headers=owner)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert response.json()['batches']==[] and response.json()['total']==0
    for query in ('?limit=101','?limit=0','?after=-2','?after=2147483648'):
        assert client.get(url+query,headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)


def test_ten_thousand_retained_batches_return_bounded_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    log = workspace.import_log(BytesIO(DOC), name='scale', source_format='csv', mapping=MAPPING)['log_id']
    conn = workspace._connect()
    try:
        conn.execute('''INSERT INTO quarantine SELECT log_id, i, 1, reasons, row
            FROM quarantine, range(1, 10001) t(i) WHERE log_id = ? AND batch = 0''', [log])
    finally:
        conn.close()
    result = quarantine.quarantine(log, limit=1)
    assert result['batches_total'] == result['rows_kept'] == 10001
    assert len(result['batches']) == 100 and result['batches_next_after'] == 99
    assert quarantine.quarantine(log, batch=10000, limit=1)['rows_kept'] == 1
