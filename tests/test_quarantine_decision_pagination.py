"""Bounded, owner-only access to retained quarantine decision history."""
from datetime import datetime, timedelta
from io import BytesIO
import json
import os
import subprocess
import sys

import pytest

from server import event_log_quarantine as quarantine
from server import event_log_workspace as workspace

DOC = b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-01 02:00:00\n'
MAPPING = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time', 'keep_rejected': True}


def test_history_is_bounded_ordered_and_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    log = workspace.import_log(BytesIO(DOC), name='one', source_format='csv', mapping=MAPPING)['log_id']
    other = workspace.import_log(BytesIO(DOC), name='two', source_format='csv', mapping=MAPPING)['log_id']
    conn = workspace._connect()
    try:
        when = datetime(2024, 1, 1)
        conn.executemany('INSERT INTO quarantine_decisions VALUES (?, ?, ?)',
                         [(log, when + timedelta(seconds=i), json.dumps({'kind': 'discard', 'rows_discarded': i})) for i in range(205)] +
                         [(other, when, json.dumps({'kind': 'discard', 'rows_discarded': 999}))])
    finally:
        conn.close()
    initial = quarantine.quarantine(log)
    assert len(initial['decisions']) == 100
    assert initial['decisions_total'] == 205 and initial['decisions_next_offset'] == 100
    assert initial['decisions_stable_snapshot'] is False
    pages = [quarantine.decision_history(log, offset) for offset in (0, 100, 200, 300)]
    assert [len(p['decisions']) for p in pages] == [100, 100, 5, 0]
    assert [p['next_offset'] for p in pages] == [100, 200, None, None]
    assert all(p['total'] == 205 and p['stable_snapshot'] is False for p in pages)
    assert [d['rows_discarded'] for p in pages for d in p['decisions']] == list(range(205))
    assert quarantine.decision_history(other)['total'] == 1
    for offset, limit in [(-1, 1), (True, 1), (0, 0), (0, 101), (0, True), (2**31, 1)]:
        with pytest.raises(workspace.EventLogWorkspaceError):
            quarantine.decision_history(log, offset, limit)


def test_http_owner_only_no_store_and_validation(tmp_path):
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
    url='/v1/event-logs/'+made.json()['log_id']+'/quarantine/decisions'
    assert client.get(url).status_code in (401,403)
    assert client.get(url,headers=bearer).status_code in (401,403)
    result=client.get(url,headers=owner)
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    assert result.json()['decisions']==[] and result.json()['total']==0
    for query in ('?limit=101','?limit=0','?offset=-1','?offset=2147483648'):
        assert client.get(url+query,headers=owner).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
