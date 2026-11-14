"""Synthetic event exports: review-mode literals and unchanged exact interchange."""
import csv
from io import BytesIO, StringIO
import pytest

from server import event_log_workspace as workspace
from shared.spreadsheet_review import review_cell


@pytest.fixture(autouse=True)
def data_dir(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,'DATA_DIR',tmp_path)


def file(rows):
    out=StringIO(newline='');writer=csv.writer(out);writer.writerow(['event','case','activity','time','who']);writer.writerows(rows)
    return BytesIO(out.getvalue().encode())


def test_review_projection_neutralizes_formula_prefixes_and_keeps_exact_export():
    mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time','resource_column':'who',
             'event_id_column':'event','source_namespace':'crm'}
    made=workspace.import_log(file([['=event','=case',' =1+1','2026-01-01T00:00:00Z','@name'],
                                    ['normal','safe','Normal','2026-01-02T00:00:00Z','good']]),
                              name='Synthetic',source_format='csv',mapping=mapping)
    log=made['log_id']
    exact=StringIO(newline='');workspace.export_log(log,'sourcecsv',exact,'source_case')
    rows=list(csv.DictReader(StringIO(exact.getvalue())))
    dangerous=next(r for r in rows if r['source_event_id']=='=event')
    assert dangerous['case_id']=='=case' and dangerous['activity']==' =1+1' and dangerous['resource']=='@name'
    review=StringIO(newline='');workspace.export_log(log,'sourcecsv-review',review,'source_case')
    rows=list(csv.DictReader(StringIO(review.getvalue())))
    dangerous=next(r for r in rows if r['source_event_id_review']=="'=event")
    assert dangerous['case_id_review']=="'=case" and dangerous['activity_review']=="' =1+1"
    assert dangerous['resource_review']=="'@name" and 'source_event_id' not in dangerous
    assert next(r for r in rows if r['source_event_id_review']=='normal')['activity_review']=='Normal'
    flat=StringIO(newline='');workspace.export_log(log,'csv-review',flat,'source_case')
    assert any(r['activity_review']=="' =1+1" for r in csv.DictReader(StringIO(flat.getvalue())))
    repeated=StringIO(newline='');workspace.export_log(log,'sourcecsv',repeated,'source_case')
    assert repeated.getvalue()==exact.getvalue()


@pytest.mark.parametrize('source,expected', [('', ''),('normal','normal'),('=1',"'=1"),(' +a',"' +a"),
    ('-12',"'-12"),('@x',"'@x"),('\tdata',"'\tdata"),('\ufeff=1',"'\ufeff=1"),('\u00a0=1',"'\u00a0=1")])
def test_text_projection(source,expected):
    assert review_cell(source)==expected


def test_review_http_uses_owner_session_and_no_store(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    ai={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    data=b'case,activity,time\nc1,=SUM(1),2026-01-01T00:00:00Z\n'
    made=client.post('/v1/event-logs/import?format=csv&name=synthetic&case_column=case&activity_column=activity&timestamp_column=time',content=data,headers={**owner,'x-owg-local-storage-confirmed':'true'})
    assert made.status_code==200,made.text
    url='/v1/event-logs/'+made.json()['log_id']+'/export?format=csv-review'
    assert client.get(url,headers=ai).status_code in (401,403)
    assert client.get(url).status_code in (401,403)
    result=client.get(url,headers=owner)
    assert result.status_code==200 and result.headers['cache-control']=='no-store',result.text
    assert 'activity_review' in result.text and "'=SUM(1)" in result.text
    assert 'review.csv' in result.headers['content-disposition']
'''
    env={**os.environ,'WORKFLOW_OBSERVER_DATA':str(tmp_path/'data'),'WORKFLOW_OBSERVER_AUTH_DIR':str(tmp_path/'auth'),'WORKFLOW_OBSERVER_CONFIG':str(tmp_path/'config.json')}
    run=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert run.returncode==0,run.stdout+'\n'+run.stderr


def test_review_csv_keeps_quoted_formula_like_values_in_one_literal_cell():
    mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time','resource_column':'who'}
    activity='=HYPERLINK("https://example.invalid/?a=1,b=2","open")'
    resource='\u00a0@name'
    made=workspace.import_log(file([['e1','c1',activity,'2026-01-01T00:00:00Z',resource]]),
                              name='Synthetic quoting',source_format='csv',mapping=mapping)
    review=StringIO(newline='');workspace.export_log(made['log_id'],'csv-review',review,'case')
    row=next(csv.DictReader(StringIO(review.getvalue())))
    assert row['activity_review']=="'"+activity
    assert row['resource_review']=="'"+resource
    exact=StringIO(newline='');workspace.export_log(made['log_id'],'csv',exact,'case')
    original=next(csv.DictReader(StringIO(exact.getvalue())))
    assert original['activity']==activity and original['resource']==resource
