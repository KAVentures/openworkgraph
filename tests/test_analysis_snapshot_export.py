"""No raw/group/filter identifiers in explicit sharing snapshots; exact selected arithmetic."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import analysis_snapshot_export as sharing, event_log_workspace as workspace
from server import event_log_query as analysis
from shared.analysis_snapshot import Snapshot, content_digest


@pytest.fixture
def log(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)
    return workspace.import_log(BytesIO(b'case,activity,time,person\nsecret-case,A,2024-01-01T00:00:00Z,private-person\nsecret-case,B,2024-01-01T01:00:00Z,private-person\nother-case,A,2024-01-02T00:00:00Z,private-other\n'),name='private-customer-log',source_format='csv',mapping={
        'case_column':'case','activity_column':'activity','timestamp_column':'time','resource_column':'person'})['log_id']


def spec(**extra):
    return {'level':'cases','dimensions':['last_activity'],'measures':[{'fn':'count'}],**extra}


def test_export_discards_source_and_group_labels_but_preserves_numbers_units_and_spec_digest(log):
    definition=spec()
    raw=analysis.run(log,definition);export=sharing.export_snapshot(log,definition)
    assert [r[-1] for r in export['rows']]==[r[-1] for r in raw['rows']]
    assert export['columns']==[{'heading':'Group 1','kind':'group','unit':None,'aggregation':None,'value_basis':None,'metric_slot':None},{'heading':'Measure 1','kind':'number','unit':'cases','aggregation':'count','value_basis':None,'metric_slot':None}]
    assert export['analysis_spec_sha256']==content_digest(raw['spec'])
    assert export['matching_cases']==2 and export['matching_rows']==2
    assert export['source_content_sha256'] and not export['source_independently_verified']
    encoded=json.dumps(export)
    for forbidden in ['secret-case','other-case','private-person','private-other','private-customer-log','last_activity',log,'resource','cohort','case_id']:
        assert forbidden not in encoded
    assert {r[0] for r in export['rows']}=={'G1_1','G1_2'}
    assert Snapshot.model_validate(export).model_dump(mode='json')==export


def test_complete_fingerprint_changes_on_append_and_group_dictionary_is_not_shared(log):
    before=sharing.export_snapshot(log,spec())
    workspace.append_log(log,BytesIO(b'case,activity,time\nnew-case,C,2024-01-03T00:00:00Z\n'),source_format='csv',mapping={
        'case_column':'case','activity_column':'activity','timestamp_column':'time'})
    after=sharing.export_snapshot(log,spec())
    assert before['source_content_sha256']!=after['source_content_sha256'] and after['matching_cases']==3
    assert after['analysis_spec_sha256']==before['analysis_spec_sha256']


def test_coverage_and_safe_missing_arithmetic_propagate_without_definitions(log):
    wait={'kind':'time_between','name':'private_metric','from_activity':'A','to_activity':'B'}
    half={'kind':'arithmetic','name':'private_derived','operation':'divide','left':{'kind':'metric','name':'private_metric'},'right':{'kind':'constant','value':2}}
    r=sharing.export_snapshot(log,spec(dimensions=[],metrics=[wait,half],measures=[{'fn':'max','of':'metric:private_derived'}]))
    assert r['rows']==[[1800]] and r['columns'][0]['unit']=='seconds'
    assert r['metric_coverage'][1]=={'slot':2,'with_value':1,'without_value':1,'missing_operand':1,'zero_divisor':0,'non_finite':0}
    assert 'private_metric' not in json.dumps(r) and 'from_activity' not in json.dumps(r)


def test_budget_refuses_before_a_partial_fingerprint(log,monkeypatch):
    import server.process_discovery_provenance as provenance
    monkeypatch.setattr(provenance,'MAX_SOURCE_ROWS',0)
    monkeypatch.setattr(provenance,'source_digest',lambda *a:pytest.fail('partial digest'))
    with pytest.raises(workspace.EventLogWorkspaceError,match='fingerprint budget'):sharing.export_snapshot(log,spec())
    with pytest.raises(workspace.EventLogWorkspaceError,match='100 rows'):sharing.export_snapshot(log,spec(limit=101))


def test_local_route_human_preview_intent_and_no_store(tmp_path):
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as c:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 d=b'case,activity,time\nsecret,A,2024-01-01T00:00:00Z\n'
 r=c.post('/v1/event-logs/import?format=csv&name=private&case_column=case&activity_column=activity&timestamp_column=time',content=d,headers={**h,'X-OWG-Local-Storage-Confirmed':'true'})
 base='/v1/event-logs/'+r.json()['log_id']+'/analysis-sharing-preview'
 spec={'level':'cases','dimensions':['last_activity'],'measures':[{'fn':'count'}]}
 assert c.post(base,json=spec,headers=h).status_code==403
 reply=c.post(base,json=spec,headers={**h,'X-OWG-Analysis-Sharing-Preview':'true'})
 assert reply.status_code==200,reply.text
 assert reply.headers['cache-control']=='no-store' and reply.json()['rows']==[['G1_1',1]]
 assert 'secret' not in reply.text and 'private' not in reply.text
 bearer={'Authorization':'Bearer '+local_auth.ensure_api_token(),'X-OWG-Analysis-Sharing-Preview':'true'}
 assert c.post(base,json=spec,headers=bearer).status_code==403
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    r=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert r.returncode==0,r.stdout+r.stderr
