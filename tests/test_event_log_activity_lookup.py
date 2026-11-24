"""Bounded literal search in the same case notion and mapping as filters."""
from io import BytesIO
import json
import os
import subprocess
import sys

import pytest
from server import event_log_activity_lookup as lookup
from server import event_log_activity_mappings as mappings
from server import event_log_workspace as workspace

DOC = b'case,activity,time\nprivate-case,A,2024-01-01T00:00:00Z\nprivate-case,B,2024-01-01T01:00:00Z\n'
MAPPING = {'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'}


def test_search_pages_literal_labels_and_respects_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    log = workspace.import_log(BytesIO(DOC), name='one', source_format='csv', mapping=MAPPING)['log_id']
    other = workspace.import_log(BytesIO(DOC), name='two', source_format='csv', mapping=MAPPING)['log_id']
    conn = workspace._connect()
    try:
        conn.execute('''INSERT INTO events SELECT ?, 'fixture_' || lpad(CAST(i AS VARCHAR), 6, '0'),
            'rare%_' || lpad(CAST(i AS VARCHAR), 3, '0'), TIMESTAMP '2024-01-01 02:00:00', '', '{}'
            FROM range(205) t(i)''', [log])
        conn.execute('''INSERT INTO event_objects SELECT log_id, event_id, 'private-case', '' FROM events
            WHERE log_id = ? AND starts_with(event_id, 'fixture_')''', [log])
        conn.execute("INSERT INTO objects VALUES (?, 'invoice-1', 'invoice', '{}')", [log])
        conn.execute("INSERT INTO events VALUES (?, 'invoice-event', 'Invoice-only', TIMESTAMP '2024-01-01', 'Invoice clerk', '{}')", [log])
        conn.execute("INSERT INTO event_objects VALUES (?, 'invoice-event', 'invoice-1', '')", [log])
    finally:
        conn.close()
    first = lookup.search(log, 'rare%_')
    assert len(first['labels']) == 100 and first['next_after'] == 'rare%_099'
    second = lookup.search(log, 'rare%_', first['next_after'])
    third = lookup.search(log, 'rare%_', second['next_after'])
    assert len(second['labels']) == 100 and len(third['labels']) == 5 and third['next_after'] is None
    assert first['stable_snapshot'] is False and first['limit'] == 100
    assert first['labels'] + second['labels'] + third['labels'] == [f'rare%_{i:03}' for i in range(205)]
    assert lookup.search(other, 'rare%_')['labels'] == []
    assert lookup.search(log, 'Invoice', case_type='case')['labels'] == []
    assert lookup.search(log, 'Invoice', case_type='invoice')['labels'] == ['Invoice-only']
    assert lookup.resource_search(log, 'clerk', case_type='case')['labels'] == []
    assert lookup.resource_search(log, 'clerk', case_type='invoice')['labels'] == ['Invoice clerk']
    assert lookup.search(log, "%' OR 1=1 --")['labels'] == []
    assert 'private-case' not in json.dumps(first)
    made = mappings.create(log, {'name':'group', 'groups':[{'label':'Grouped 雪', 'activities':['A','B']}], 'collapse_repeats':False})
    mappings.apply_mapping(log, made['mapping_id'])
    grouped = lookup.search(log, 'Grouped')
    assert grouped['labels'] == ['Grouped 雪'] and grouped['activity_mapping']['mapping_id'] == made['mapping_id']
    assert lookup.search(log, 'A')['labels'] == []
    for text, after, case_type in [('', '', None), ('A' * 201, '', None), ('A', 1, None), ('\n', '', None), ('A', '', True)]:
        with pytest.raises(workspace.EventLogWorkspaceError):
            lookup.search(log, text, after, case_type)


def test_owner_only_http_and_strict_body(tmp_path):
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
    url='/v1/event-logs/'+made.json()['log_id']+'/activity-labels/search'
    assert client.post(url,json={'search':'A'}).status_code in (401,403)
    assert client.post(url,json={'search':'A'},headers=bearer).status_code in (401,403)
    result=client.post(url,json={'search':'A'},headers=owner)
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    assert result.json()['labels']==['A']
    assert client.post(url,json={'search':'A','extra':1},headers=owner).status_code==422
    resource_url=url.replace('/activity-labels/','/resource-labels/')
    assert client.post(resource_url,json={'search':'A'},headers=bearer).status_code==403
    assert client.post(resource_url,json={'search':'A'},headers=owner).json()['labels']==[]
    assert client.post(url,content='{"search":"A","search":"B"}',headers={**owner,'content-type':'application/json'}).status_code==422
'''
    env = {**os.environ, 'WORKFLOW_OBSERVER_DATA': str(tmp_path / 'data'), 'WORKFLOW_OBSERVER_AUTH_DIR': str(tmp_path / 'auth')}
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=60)


def test_ten_thousand_distinct_labels_return_one_bounded_page(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    log = workspace.import_log(BytesIO(DOC), name='scale', source_format='csv', mapping=MAPPING)['log_id']
    conn = workspace._connect()
    try:
        conn.execute('''INSERT INTO events SELECT ?, 'fixture_' || lpad(CAST(i AS VARCHAR), 6, '0'),
            'Label_' || lpad(CAST(i AS VARCHAR), 6, '0'), TIMESTAMP '2024-01-01', '', '{}'
            FROM range(10000) t(i)''', [log])
        conn.execute('''INSERT INTO event_objects SELECT log_id, event_id, 'private-case', '' FROM events
            WHERE log_id = ? AND starts_with(event_id, 'fixture_')''', [log])
    finally:
        conn.close()
    result = lookup.search(log, 'Label_')
    assert len(result['labels']) == 100 and result['next_after'] == 'Label_000099'
    assert result['labels'][0] == 'Label_000000'
