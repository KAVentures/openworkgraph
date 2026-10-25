"""Explicit independent source-state exports never persist joined receipt data."""
from datetime import datetime,timezone
from hashlib import sha256
import json

import pytest

from gateway.db import GatewayDB
from shared.process_catalog import CatalogWrite,ProcessCatalog
from shared.process_model import ProcessModel
from shared.workflow_knowledge import KnowledgeStore
from server import automation_pipeline as pipeline,opportunity_projects as projects
from server import project_source_checks as checks
from server.open_work_outcomes import source_operation_receipt
from tests.test_process_opportunity_lineage import model,ref,assess

RESOURCE='owg:r:'+'a'*32


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    monkeypatch.setattr(pipeline,'DATA_DIR',tmp_path)
    catalog=ProcessCatalog(KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "catalog.db"}')),'local')
    monkeypatch.setattr(pipeline,'_catalog',lambda:catalog)
    graph=model().model_dump()
    graph['outcome_expectations']=[{'terminal_activity_id':'review','operation':'github_pr_merged','user_confirmed':True}]
    record=catalog.save(CatalogWrite(workflow_id='review',title='Case review',process_model=ProcessModel.model_validate(graph),user_confirmed=True))
    idea=assess(pipeline.create_from_process(ref(record)))
    idea=pipeline.transition(idea['idea_id'],{'to':'approved','expected_revision':idea['revision']})
    _,project=projects.export_project(idea['idea_id'],{'expected_revision':idea['revision']})
    calls=[]
    def read(resource,operation):
        def reader(resource_ref,**kwargs):
            calls.append((resource_ref,kwargs))
            return {'record_observed':True,'record_identity_complete_within_retained_window':True,
                'record_type':{'provider':'github','kind':'pull_request'},
                'source_evidence':{'availability':'source_reported','source_state_reported':True,'source_state':'merged',
                                   'verified_at':datetime.now(timezone.utc).isoformat(),'PRIVATE_FIELD':'PRIVATE_PROVIDER_TEXT'}}
        receipt=source_operation_receipt(resource,operation,record_reader=reader)
        return {**receipt,'PRIVATE_FIELD':'PRIVATE_FUTURE_RECEIPT_FIELD'}
    monkeypatch.setattr(checks,'_read_receipt',read)
    return catalog,record,idea,project,calls


def request(idea,**changes):
    return {'expected_revision':idea['revision'],'terminal_activity_id':'review','expected_operation':'github_pr_merged',
            'resource_ref':RESOURCE,'source_read_and_export_confirmed':True,**changes}


def test_verified_source_state_is_downloaded_but_only_digest_metadata_is_retained(prepared):
    _,record,idea,project,calls=prepared
    options=checks.options(idea['idea_id'],project['project_id'])
    assert len(options['expectations'])==1 and options['source_read_performed'] is False and calls==[]
    before=pipeline.get(idea['idea_id'])
    content,metadata=checks.export_check(idea['idea_id'],project['project_id'],request(idea))
    document=json.loads(content)
    assert document['process_reference']==ref(record) and document['spec_sha256']==project['spec_sha256']
    assert document['receipt']['operation_verified'] is True and document['receipt']['provider_reported_state']=='merged'
    assert document['receipt']['resource_ref']==RESOURCE
    for key in checks.FALSE_CLAIMS:assert document[key] is False
    assert document['receipt']['business_objective_verified'] is False
    assert 'PRIVATE_FIELD' not in document['receipt'] and b'PRIVATE_' not in content
    assert metadata['document_sha256']==sha256(content).hexdigest()
    assert len(calls)==1 and calls[0][1]['include_source_status'] is True
    assert pipeline.get(idea['idea_id'])==before
    history=checks.exports(idea['idea_id'],project['project_id'])
    assert len(history['exports'])==1 and history['receipt_body_persisted'] is False
    assert len(calls)==1  # history reads never read the provider again
    with pipeline._connect() as conn:
        dumped='\n'.join(conn.iterdump())
    for private in (RESOURCE,'PRIVATE_PROVIDER_TEXT','PRIVATE_FUTURE_RECEIPT_FIELD','provider_reported_state','operation_verified'):
        assert private not in dumped
    assert metadata['document_sha256'] in dumped


@pytest.mark.parametrize('change,exception',[({'source_read_and_export_confirmed':False},checks.ConsentRequired),
    ({'expected_revision':1},pipeline.PipelineError),({'terminal_activity_id':'unknown'},pipeline.PipelineError),
    ({'expected_operation':'jira_issue_resolved'},pipeline.PipelineError),({'resource_ref':'not-a-record'},pipeline.PipelineError)])
def test_invalid_or_unconfirmed_requests_do_not_read_sources(prepared,change,exception):
    _,_,idea,project,calls=prepared
    with pytest.raises(exception):checks.export_check(idea['idea_id'],project['project_id'],request(idea,**change))
    assert calls==[] and checks.exports(idea['idea_id'],project['project_id'])['exports']==[]


def test_unknown_receipts_stay_unknown_and_source_errors_leave_no_record(prepared,monkeypatch):
    _,_,idea,project,_=prepared
    monkeypatch.setattr(checks,'_read_receipt',lambda r,o:source_operation_receipt(r,o,record_reader=lambda *a,**kw:{'record_observed':False}))
    content,_=checks.export_check(idea['idea_id'],project['project_id'],request(idea))
    doc=json.loads(content)
    assert doc['receipt']['availability']=='unknown' and doc['receipt']['operation_verified'] is False
    count=len(checks.exports(idea['idea_id'],project['project_id'])['exports'])
    def broken(*a):raise RuntimeError('PRIVATE_BACKEND_ERROR')
    monkeypatch.setattr(checks,'_read_receipt',broken)
    with pytest.raises(pipeline.PipelineError,match='no document was recorded') as exc:
        checks.export_check(idea['idea_id'],project['project_id'],request(idea))
    assert 'PRIVATE_BACKEND_ERROR' not in str(exc.value)
    assert len(checks.exports(idea['idea_id'],project['project_id'])['exports'])==count


def test_wrong_idea_or_deleted_model_is_refused_before_source_read(prepared):
    catalog,_,idea,project,calls=prepared
    other=pipeline.create({'title':'Other idea'})
    with pytest.raises(pipeline.PipelineError,match='recorded for this idea'):
        checks.export_check(other['idea_id'],project['project_id'],request(other))
    catalog.store.forget('local','review')
    with pytest.raises(pipeline.PipelineError,match='linked process changed'):
        checks.export_check(idea['idea_id'],project['project_id'],request(idea))
    assert calls==[]


@pytest.mark.parametrize('change',['idea','model'])
def test_mid_read_changes_discard_receipt_without_recording(prepared,monkeypatch,change):
    catalog,_,idea,project,_=prepared
    original=checks._read_receipt
    def read(r,o):
        receipt=original(r,o)
        if change=='model':catalog.store.forget('local','review')
        else:pipeline.transition(idea['idea_id'],{'to':'on_hold','expected_revision':idea['revision']})
        return receipt
    monkeypatch.setattr(checks,'_read_receipt',read)
    with pytest.raises(pipeline.PipelineError,match='changed'):
        checks.export_check(idea['idea_id'],project['project_id'],request(idea))
    assert checks.exports(idea['idea_id'],project['project_id'])['exports']==[]


def test_cap_is_checked_before_source_read_and_deletion_cleans_metadata(prepared,monkeypatch):
    _,_,idea,project,calls=prepared
    monkeypatch.setattr(checks,'MAX_EXPORTS_PER_PROJECT',1)
    checks.export_check(idea['idea_id'],project['project_id'],request(idea))
    with pytest.raises(pipeline.PipelineError,match='at most 1'):
        checks.export_check(idea['idea_id'],project['project_id'],request(idea))
    assert len(calls)==1
    pipeline.delete(idea['idea_id'])
    with pipeline._connect() as conn:assert conn.execute('SELECT count(*) FROM project_source_exports').fetchone()[0]==0


def test_real_routes_human_consent_hash_and_unobserved_source(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    code=r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth,automation_pipeline as p
from server.local_process_catalog import catalog
from server.opportunity_projects import export_project
from shared.process_catalog import CatalogWrite
from shared.process_model import ProcessModel
from tests.test_process_opportunity_lineage import model,ref,assess
from hashlib import sha256
m=model().model_dump();m['outcome_expectations']=[{'terminal_activity_id':'review','operation':'github_pr_merged','user_confirmed':True}]
r=catalog.save(CatalogWrite(workflow_id='review',title='Review',process_model=ProcessModel.model_validate(m),user_confirmed=True))
i=assess(p.create_from_process(ref(r)));i=p.transition(i['idea_id'],{'to':'approved','expected_revision':i['revision']})
_,artifact=export_project(i['idea_id'],{'expected_revision':i['revision']})
url='/v1/automation-pipeline/'+i['idea_id']+'/projects/'+artifact['project_id']
body={'expected_revision':i['revision'],'terminal_activity_id':'review','expected_operation':'github_pr_merged','resource_ref':'owg:r:'+'a'*32}
with TestClient(app) as c:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()};bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
 for suffix in ('/source-check-options','/source-check-exports'):
  assert c.get(url+suffix,headers=bearer).status_code in (401,403)
 assert c.post(url+'/source-check-export',json={**body,'source_read_and_export_confirmed':True},headers=bearer).status_code in (401,403)
 assert c.post(url+'/source-check-export',json=body,headers=h).status_code==403
 result=c.post(url+'/source-check-export',json={**body,'source_read_and_export_confirmed':True},headers=h)
 assert result.status_code==200,result.text
 assert result.headers['cache-control']=='no-store'
 assert result.headers['x-owg-document-sha256']==sha256(result.content).hexdigest()
 assert result.json()['receipt']['availability']=='unknown' and result.json()['receipt']['operation_verified'] is False
 listing=c.get(url+'/source-check-exports',headers=h).json()
 assert len(listing['exports'])==1 and body['resource_ref'] not in str(listing)
'''
    env=os.environ.copy();env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr


def test_identical_document_bytes_reuse_metadata_without_unique_key_errors(prepared,monkeypatch):
    _,_,idea,project,_=prepared
    instant=datetime.now(timezone.utc)
    class FrozenClock:
        @staticmethod
        def now(zone):return instant
    monkeypatch.setattr(checks,'datetime',FrozenClock)
    monkeypatch.setattr(checks,'_read_receipt',lambda r,o:source_operation_receipt(r,o,record_reader=lambda *a,**kw:{'record_observed':False}))
    first,one=checks.export_check(idea['idea_id'],project['project_id'],request(idea))
    second,two=checks.export_check(idea['idea_id'],project['project_id'],request(idea))
    assert first==second and one==two
    assert len(checks.exports(idea['idea_id'],project['project_id'])['exports'])==1
