"""Stored quality history can be read without recomputing whole-log data."""
from io import BytesIO
import os
import subprocess
import sys

import pytest

from server import event_log_quality as quality
from server import event_log_workspace as workspace

DOC = b'case,activity,time\nprivate-case,private-activity,2024-01-01T00:00:00Z\n'
MAPPING = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'}


def test_history_matches_full_report_without_whole_log_scans(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    log = workspace.import_log(BytesIO(DOC), name='history', source_format='csv', mapping=MAPPING)['log_id']
    workspace.append_log(log, BytesIO(DOC), source_format='csv', mapping=MAPPING)
    full = quality.quality(log)
    expected = {key: full[key] for key in ('reports', 'batches_without_report', 'batch_trends')}
    def forbidden(*args, **kwargs):
        raise AssertionError('history must not compute whole-log diagnostics or digests')
    for name in ('diagnostics', 'content_digest', 'content_digest_v2', 'source_identity_digest'):
        monkeypatch.setattr(quality, name, forbidden)
    history = quality.batch_history(log)
    assert history == expected
    assert 'private-case' not in str(history) and 'private-activity' not in str(history)
    with pytest.raises(workspace.EventLogWorkspaceError):
        quality.batch_history('not-a-log')


def test_owner_only_http_history_and_no_store(tmp_path):
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
    url='/v1/event-logs/'+made.json()['log_id']+'/quality/batch-history'
    assert client.get(url).status_code in (401,403)
    assert client.get(url,headers=bearer).status_code in (401,403)
    response=client.get(url,headers=owner)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert len(response.json()['reports'])==1 and 'current' not in response.json()
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)
