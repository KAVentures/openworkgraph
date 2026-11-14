"""Paged batch quality history, including missing reports and page boundaries."""
from datetime import datetime
from io import BytesIO
import json
import os
import subprocess
import sys

import pytest

from server import event_log_quality as quality
from server import event_log_quality_pages as pages
from server import event_log_workspace as workspace


def test_bounded_history_pages_cover_missing_reports_and_compare_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    doc = b'case,activity,time\nsecret,A,2024-01-01T00:00:00Z\n'
    mapping = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'}
    log = workspace.import_log(BytesIO(doc), name='history', source_format='csv', mapping=mapping)['log_id']
    conn = workspace._connect()
    try:
        original = json.loads(conn.execute('SELECT report FROM log_quality WHERE log_id = ?', [log]).fetchone()[0])
        now = datetime(2024, 1, 1)
        conn.executemany('INSERT INTO log_appends VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                         [(log, i, now, 'csv', 1, 0, 0, 0) for i in range(1, 205)])
        values = []
        for i in range(1, 205):
            if i in (4, 130):
                continue
            report = dict(original, batch=i, kind='append')
            report['rows'] = {**original['rows'], 'duplicates_skipped_on_append': 0}
            values.append((log, i, now, json.dumps(report)))
        conn.executemany('INSERT INTO log_quality VALUES (?, ?, ?, ?)', values)
    finally:
        conn.close()
    full = quality.quality(log)['batch_trends']
    first = pages.page(log)
    assert first['window'] == {'before': None, 'limit': 100, 'batch_count': 100,
                               'total_batches': 205, 'next_before': 105, 'stable_snapshot': False}
    assert len(first['reports']) == 99 and first['batches_without_report'] == [130]
    assert len(first['batch_trends']['review_queue']) == 100
    # Even at the page boundary, comparison matches the complete history.
    full_by_id = {item['batch']: item for item in full['batches']}
    for item in first['batch_trends']['batches']:
        assert item == full_by_id[item['batch']]
    second = pages.page(log, before=105)
    third = pages.page(log, before=second['window']['next_before'])
    assert second['window']['next_before'] == 5 and len(second['reports']) == 100
    assert third['window']['next_before'] is None and third['batches_without_report'] == [4]
    observed = [x['batch'] for p in (first, second, third) for x in p['batch_trends']['review_queue']]
    assert sorted(observed) == list(range(205))
    assert 'secret' not in str(first)
    assert pages.page(log, before=0)['window']['batch_count'] == 0
    # Corrupt duplicate report records must not silently become a partial page.
    conn = workspace._connect()
    try:
        conn.execute('INSERT INTO log_quality SELECT * FROM log_quality WHERE log_id = ? AND batch = 204', [log])
    finally:
        conn.close()
    with pytest.raises(workspace.EventLogWorkspaceError, match='Duplicate'):
        pages.page(log)
    for before, limit in [(-1, 1), (True, 1), (0, 0), (0, 101), (0, True), (2**31, 1)]:
        with pytest.raises(workspace.EventLogWorkspaceError):
            pages.page(log, before, limit)


def test_owner_only_http_page(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time'
    made=client.post(url,content=b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n',headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    url='/v1/event-logs/'+made.json()['log_id']+'/quality/batch-history/page'
    assert client.get(url).status_code in (401,403)
    assert client.get(url,headers=bearer).status_code in (401,403)
    response=client.get(url,headers=owner)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert response.json()['window']['batch_count']==1
    for query in ('?limit=101','?limit=0','?before=-1','?before=2147483648'):
        assert client.get(url+query,headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)


def test_ten_thousand_batch_history_returns_one_bounded_page(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    mapping = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'}
    log = workspace.import_log(BytesIO(b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n'),
                               name='scale', source_format='csv', mapping=mapping)['log_id']
    conn = workspace._connect()
    try:
        conn.execute('''INSERT INTO log_appends
            SELECT ?, i, TIMESTAMP '2024-01-01', 'csv', 1, 0, 0, 0 FROM range(1, 10001) t(i)''', [log])
        conn.execute('''INSERT INTO log_quality
            SELECT log_id, i, created_at, json_merge_patch(report, json_object('batch', i, 'kind', 'append'))
            FROM log_quality, range(1, 10001) t(i) WHERE log_id = ? AND batch = 0''', [log])
    finally:
        conn.close()
    result = pages.page(log)
    assert result['window']['total_batches'] == 10001
    assert result['window']['batch_count'] == len(result['reports']) == 100
    assert result['window']['next_before'] == 9901
    assert len(result['batch_trends']['review_queue']) == 100
