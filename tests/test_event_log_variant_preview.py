"""Bounded display paths preserve complete variant grouping and filter semantics."""
from io import BytesIO
import os
import subprocess
import sys

from server import event_log_activity_mappings as mappings
from server import event_log_variant_preview as preview
from server import event_log_workspace as workspace

MAPPING={'case_column':'case','activity_column':'activity','timestamp_column':'time'}


def test_full_paths_are_grouped_before_truncation_and_filters_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    monkeypatch.setattr(preview,'MAX_PATH',3)
    doc=('case,activity,time\n'
         'one,A,2024-01-01T00:00:00Z\none,B,2024-01-01T01:00:00Z\none,C,2024-01-01T02:00:00Z\none,X,2024-01-01T03:00:00Z\n'
         'two,A,2024-01-02T00:00:00Z\ntwo,B,2024-01-02T01:00:00Z\ntwo,C,2024-01-02T02:00:00Z\ntwo,Y,2024-01-02T03:00:00Z\n'
         'three,A,2024-01-03T00:00:00Z\nthree,B,2024-01-03T01:00:00Z\n')
    log=workspace.import_log(BytesIO(doc.encode()),name='paths',source_format='csv',mapping=MAPPING)['log_id']
    result=preview.preview(log)
    assert result['cases']==3 and result['distinct_variants']==3 and result['path_limit']==3
    shortened=[item for item in result['variants'] if item['path_truncated']]
    assert len(shortened)==2 and all(item['path']==['A','B','C'] for item in shortened)
    assert all(item['path_activity_count']==4 and item['cases']==1 for item in shortened)
    assert any(item['path']==['A','B'] and not item['path_truncated'] for item in result['variants'])
    filtered=preview.preview(log,cohort={'follows':[{'from':'C','to':'X','mode':'direct'}]})
    assert filtered['cases']==1 and filtered['cases_before_filter']==3 and filtered['variants'][0]['path_truncated']
    # The legacy endpoint continues to return the full original paths.
    assert {tuple(v['path']) for v in workspace.variants(log)['variants']} == {('A','B'),('A','B','C','X'),('A','B','C','Y')}


def test_mapped_labels_with_delimiter_are_not_split_or_conflated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    doc=b'case,activity,time\none,A,2024-01-01T00:00:00Z\ntwo,X,2024-01-02T00:00:00Z\ntwo,Y,2024-01-02T01:00:00Z\n'
    log=workspace.import_log(BytesIO(doc),name='labels',source_format='csv',mapping=MAPPING)['log_id']
    made=mappings.create(log,{'name':'delimiter','groups':[{'label':'X\x1fY','activities':['A']}],'collapse_repeats':False})
    mappings.apply_mapping(log,made['mapping_id'])
    result=preview.preview(log)
    assert result['distinct_variants']==2
    assert {tuple(v['path']) for v in result['variants']}=={('X\x1fY',),('X','Y')}


def test_owner_only_http_and_full_endpoint_unchanged(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    made=client.post('/v1/event-logs/import?format=csv&name=q&case_column=case&activity_column=activity&timestamp_column=time',
        content=b'case,activity,time\n1,A,2024-01-01T00:00:00Z\n',headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    base='/v1/event-logs/'+made.json()['log_id']
    assert client.get(base+'/variants/preview').status_code in (401,403)
    assert client.get(base+'/variants/preview',headers=bearer).status_code in (401,403)
    response=client.get(base+'/variants/preview',headers=owner)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert response.json()['variants'][0]['path']==['A'] and not response.json()['variants'][0]['path_truncated']
    assert 'path_truncated' not in client.get(base+'/variants',headers=owner).json()['variants'][0]
    assert client.get(base+'/variants/preview?limit=201',headers=owner).status_code==422
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth')}
    subprocess.run([sys.executable,'-c',code],env=env,check=True,timeout=60)


def test_long_variant_is_shortened_before_fetch_without_losing_its_length(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    log=workspace.import_log(BytesIO(b'case,activity,time\nlong,A,2024-01-01T00:00:00Z\n'),
                             name='long',source_format='csv',mapping=MAPPING)['log_id']
    conn=workspace._connect()
    try:
        conn.execute('''INSERT INTO events SELECT ?, 'fixture_' || lpad(CAST(i AS VARCHAR), 6, '0'),
            CASE WHEN i % 2 = 0 THEN 'A' ELSE 'B' END,
            TIMESTAMP '2024-01-01 00:01:00' + i * INTERVAL '1 second', '', '{}'
            FROM range(100000) t(i)''',[log])
        conn.execute('''INSERT INTO event_objects SELECT log_id,event_id,'long','' FROM events
            WHERE log_id=? AND starts_with(event_id,'fixture_')''',[log])
    finally:
        conn.close()
    actual_connect=workspace._connect
    fetched=[]
    class InspectConnection:
        def __init__(self, raw): self.raw,self.target=raw,False
        def execute(self,sql,*args):
            self.target='SELECT list_slice(path, 1, ?)' in sql
            self.raw.execute(sql,*args)
            return self
        def fetchone(self): return self.raw.fetchone()
        def fetchall(self):
            rows=self.raw.fetchall()
            if self.target: fetched.extend(len(row[0]) for row in rows)
            return rows
        def __getattr__(self,name): return getattr(self.raw,name)
    monkeypatch.setattr(workspace,'_connect',lambda:InspectConnection(actual_connect()))
    result=preview.preview(log)
    assert fetched==[100] and result['cases']==result['distinct_variants']==1
    assert result['variants'][0]['path_activity_count']==100001 and result['variants'][0]['path_truncated']
