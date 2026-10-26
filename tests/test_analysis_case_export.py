from io import BytesIO
import json
import pytest
from server import analysis_case_export as sharing, event_log_workspace as workspace
from test_analysis_snapshot_export import log, spec
from shared.analysis_case_dataset import CaseDataset


def test_paired_export_has_complete_cases_and_no_original_fields(log):
    r=sharing.prepare(log,spec());a=r['aggregate_snapshot'];d=r['case_dataset']
    assert len(d['cases'])==a['matching_cases']==2
    assert d['source_content_sha256']==a['source_content_sha256']
    assert d['analysis_spec_sha256']==a['analysis_spec_sha256']
    assert d['cases'][1]['events']==[{'activity':'A1','elapsed_microseconds':0},{'activity':'A2','elapsed_microseconds':3600000000}]
    encoded=json.dumps(d)
    for secret in ['secret-case','other-case','private-person','private-other','private-customer-log','2024-','case_id','timestamp','resource','attributes',log]:assert secret not in encoded
    assert CaseDataset.model_validate(d)


@pytest.mark.parametrize('extra',[{'level':'events','dimensions':['activity']},{'where':{'min_events':1}},{'activity_in':['A']}])
def test_unsupported_analysis_filters_refuse_instead_of_sharing_wrong_population(log,extra):
    with pytest.raises(workspace.EventLogWorkspaceError,match='cohort filters only'):sharing.prepare(log,spec(**extra))


def test_whole_case_budget_refuses_without_sampling(log):
    rows=['case,activity,time']+[f'x{i},A,2024-01-01T00:00:00Z' for i in range(501)]
    workspace.append_log(log,BytesIO('\n'.join(rows).encode()),source_format='csv',mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})
    with pytest.raises(workspace.EventLogWorkspaceError,match='no partial dataset'):sharing.prepare(log,spec())


def test_local_route_requires_dashboard_and_separate_case_preview_intent(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as c:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 data=b'case,activity,time\nprivate,A,2024-01-01T00:00:00Z\nprivate,B,2024-01-01T01:00:00Z\n'
 r=c.post('/v1/event-logs/import?format=csv&name=secret&case_column=case&activity_column=activity&timestamp_column=time',content=data,headers={**h,'X-OWG-Local-Storage-Confirmed':'true'})
 base='/v1/event-logs/'+r.json()['log_id']+'/case-sharing-preview'
 spec={'level':'cases','dimensions':[],'measures':[{'fn':'count'}]}
 assert c.post(base,json=spec,headers=h).status_code==403
 r=c.post(base,json=spec,headers={**h,'X-OWG-Case-Sharing-Preview':'true'})
 assert r.status_code==200,r.text
 assert r.headers['cache-control']=='no-store'
 assert r.json()['case_dataset']['cases'][0]['events'][-1]['elapsed_microseconds']==3600000000
 assert 'private' not in r.text and '2024-' not in r.text
 assert c.post(base,json=spec,headers={'Authorization':'Bearer '+local_auth.ensure_api_token(),'X-OWG-Case-Sharing-Preview':'true'}).status_code==403
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    r=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert r.returncode==0,r.stdout+r.stderr


def test_cohort_keeps_complete_case_timelines_and_no_original_selection(log):
    r=sharing.prepare(log,spec(cohort={'activity':'B'}))
    assert len(r['case_dataset']['cases'])==r['aggregate_snapshot']['matching_cases']==1
    assert [e['activity'] for e in r['case_dataset']['cases'][0]['events']]==['A1','A2']


def test_export_lock_prevents_append_between_fingerprint_and_case_selection(log,monkeypatch):
    from threading import Thread,Event
    entered,finished=Event(),Event();original=sharing.export_snapshot;workers=[]
    def append():
        entered.set()
        workspace.append_log(log,BytesIO(b'case,activity,time\nnew-private,C,2024-01-03T00:00:00Z\n'),source_format='csv',mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})
        finished.set()
    def export(*a):
        result=original(*a);worker=Thread(target=append);workers.append(worker);worker.start()
        assert entered.wait(2) and not finished.wait(.05)
        return result
    monkeypatch.setattr(sharing,'export_snapshot',export)
    result=sharing.prepare(log,spec())
    for worker in workers:worker.join(5)
    assert finished.is_set() and len(result['case_dataset']['cases'])==result['aggregate_snapshot']['matching_cases']==2
    assert workspace.summary(log)['cases']==3
