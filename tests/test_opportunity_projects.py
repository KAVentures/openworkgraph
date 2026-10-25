"""Tracked exports preserve the existing runtime contract and pin local review."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import os
import subprocess
import sys
from zipfile import ZipFile

import pytest

from gateway.db import GatewayDB
from shared.process_catalog import CatalogWrite, ProcessCatalog
from shared.workflow_knowledge import KnowledgeStore
from shared.process_agent_project import build_runtime_project
from server import automation_pipeline as pipeline
from server import opportunity_projects as projects
from tests.test_process_opportunity_lineage import model, ref, assess, save

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'DATA_DIR', tmp_path)
    catalog = ProcessCatalog(KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "catalog.db"}')), 'local')
    monkeypatch.setattr(pipeline, '_catalog', lambda: catalog)
    record = save(catalog)
    idea = assess(pipeline.create_from_process(ref(record)))
    idea = pipeline.transition(idea['idea_id'], {'to':'approved','expected_revision':idea['revision']})
    return catalog,record,idea


def test_export_preserves_every_generator_file_and_records_exact_artifact(setup):
    catalog,record,idea = setup
    base = build_runtime_project(catalog.export(record['workflow_id'], record['revision']))
    content,metadata = projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    with ZipFile(BytesIO(base)) as original, ZipFile(BytesIO(content)) as linked:
        assert set(linked.namelist()) == set(original.namelist()) | {'opportunity.lineage.json'}
        for name in original.namelist():
            assert linked.read(name) == original.read(name),name
        spec = json.loads(linked.read('workflow.spec.json'))
        manifest = json.loads(linked.read('opportunity.lineage.json'))
    assert manifest['process_reference'] == ref(record)
    assert manifest['idea_id'] == idea['idea_id'] and manifest['idea_revision'] == idea['revision']
    assert manifest['spec_sha256'] == sha256(json.dumps(spec,sort_keys=True,allow_nan=False).encode()).hexdigest()
    assert metadata['archive_sha256'] == sha256(content).hexdigest()
    assert metadata['base_archive_sha256'] == sha256(base).hexdigest()
    assert spec['execution_authorization'] is False and spec['live_business_tools'] == []
    assert manifest['execution_authorized'] is False and manifest['business_outcomes_verified'] is False
    assert 'assessment' not in manifest and 'title' not in manifest and 'description' not in manifest
    assert pipeline.get(idea['idea_id']) == idea  # exporting never changes its decision/revision
    listing = projects.projects(idea['idea_id'])
    assert len(listing['projects']) == 1 and listing['projects'][0]['archive_sha256'] == metadata['archive_sha256']
    assert listing['projects'][0]['idea_revision_is_current']
    assert listing['deployment_verified'] is False and listing['business_outcomes_verified'] is False
    repeated,second = projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    assert repeated == content and second == metadata and len(projects.projects(idea['idea_id'])['projects']) == 1


def test_reassessment_invalidates_export_and_keeps_historical_records(setup):
    catalog,record,idea = setup
    _,artifact = projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    revised = pipeline.assess(idea['idea_id'], {'ease':{q:4 for q in pipeline.EASE_QUESTIONS},
        'cases_per_month':200,'minutes_per_case':2,'eligible_share':.5,'expected_revision':idea['revision']})
    with pytest.raises(pipeline.PipelineError,match='changed'):
        projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    with pytest.raises(pipeline.PipelineError,match='approve'):
        projects.export_project(idea['idea_id'], {'expected_revision':revised['revision']})
    historical = projects.projects(idea['idea_id'])['projects'][0]
    assert historical['project_id'] == artifact['project_id'] and not historical['idea_revision_is_current']


@pytest.mark.parametrize('change',['new_revision','forget','replace','uncheckable'])
def test_source_drift_refuses_new_exports_without_destroying_old_metadata(setup, monkeypatch, change):
    catalog,record,idea = setup
    projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    before = projects.projects(idea['idea_id'])['projects']
    if change == 'new_revision':
        save(catalog,1,'Use changed source rules')
    elif change in {'forget','replace'}:
        catalog.store.forget('local',record['workflow_id'])
        if change == 'replace':save(catalog,goal='An unrelated replacement')
    else:
        monkeypatch.setattr(pipeline,'_catalog',lambda: (_ for _ in ()).throw(RuntimeError('PRIVATE_ERROR')))
    with pytest.raises(pipeline.PipelineError,match='linked process changed'):
        projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    listing = projects.projects(idea['idea_id'])
    assert listing['projects'] == before and not listing['process_status']['reference_is_current']
    assert 'PRIVATE_ERROR' not in json.dumps(listing)


def test_source_change_during_generation_leaves_no_record(setup, monkeypatch):
    catalog,record,idea = setup
    original = projects.build_runtime_project
    def change(packet):
        archive = original(packet)
        save(catalog,1,'Changed during generation')
        return archive
    monkeypatch.setattr(projects,'build_runtime_project',change)
    with pytest.raises(pipeline.PipelineError,match='linked process changed'):
        projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    assert projects.projects(idea['idea_id'])['projects'] == []


def test_failed_generation_is_safe_and_unlinked_ideas_cannot_export(setup, monkeypatch):
    _,_,idea = setup
    def broken(packet):raise RuntimeError('PRIVATE_DETAILS')
    monkeypatch.setattr(projects,'build_runtime_project',broken)
    with pytest.raises(pipeline.PipelineError,match='could not be generated') as exc:
        projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    assert 'PRIVATE_DETAILS' not in str(exc.value) and projects.projects(idea['idea_id'])['projects'] == []
    manual = pipeline.create({'title':'Manual source','source':{'kind':'process_catalog','reference':'review@1'}})
    with pytest.raises(pipeline.PipelineError,match='linked to an exact'):
        projects.export_project(manual['idea_id'], {'expected_revision':manual['revision']})


def test_registry_bound_allows_identical_download_and_delete_cleans_records(setup, monkeypatch):
    _,_,idea = setup
    monkeypatch.setattr(projects,'MAX_PROJECTS_PER_IDEA',1)
    projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    projects.export_project(idea['idea_id'], {'expected_revision':idea['revision']})
    held = pipeline.transition(idea['idea_id'], {'to':'on_hold','expected_revision':idea['revision']})
    assessed = pipeline.transition(idea['idea_id'], {'to':'assessing','expected_revision':held['revision']})
    approved = pipeline.transition(idea['idea_id'], {'to':'approved','expected_revision':assessed['revision']})
    with pytest.raises(pipeline.PipelineError,match='at most 1'):
        projects.export_project(idea['idea_id'], {'expected_revision':approved['revision']})
    assert len(projects.projects(idea['idea_id'])['projects']) == 1
    pipeline.delete(idea['idea_id'])
    with pipeline._connect() as conn:
        assert conn.execute('SELECT count(*) FROM idea_projects').fetchone()[0] == 0


def test_real_routes_require_session_and_return_matching_archive_headers(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, automation_pipeline as p
from server.local_process_catalog import catalog
from tests.test_process_opportunity_lineage import save,ref,assess
from hashlib import sha256
record=save(catalog)
idea=assess(p.create_from_process(ref(record)))
idea=p.transition(idea['idea_id'],{'to':'approved','expected_revision':idea['revision']})
with TestClient(app) as c:
    h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    url='/v1/automation-pipeline/'+idea['idea_id']
    assert c.post(url+'/agent-project',headers=bearer,json={'expected_revision':idea['revision']}).status_code in (401,403)
    assert c.get(url+'/projects',headers=bearer).status_code in (401,403)
    downloaded=c.post(url+'/agent-project',headers=h,json={'expected_revision':idea['revision']})
    assert downloaded.status_code==200,downloaded.text
    assert downloaded.headers['cache-control']=='no-store'
    assert downloaded.headers['x-owg-archive-sha256']==sha256(downloaded.content).hexdigest()
    listed=c.get(url+'/projects',headers=h)
    assert listed.status_code==200 and listed.headers['cache-control']=='no-store'
    assert listed.json()['projects'][0]['project_id']==downloaded.headers['x-owg-project-id']
    assert c.post(url+'/agent-project',headers=h,json={'expected_revision':1}).status_code==409
'''
    env=os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode == 0,result.stdout+result.stderr


@pytest.mark.parametrize('tamper',['specification','manifest_collision'])
def test_generator_contract_mismatch_is_refused_before_recording(setup, monkeypatch, tamper):
    _,_,idea=setup
    original=projects.build_runtime_project
    def wrong(packet):
        base=original(packet);out=BytesIO()
        with ZipFile(BytesIO(base)) as source, ZipFile(out,'w') as target:
            for entry in source.infolist():
                content=source.read(entry.filename)
                if tamper=='specification' and entry.filename=='workflow.spec.json':
                    spec=json.loads(content);spec['execution_authorization']=True
                    content=json.dumps(spec).encode()
                target.writestr(entry,content)
            if tamper=='manifest_collision':target.writestr('opportunity.lineage.json','{}')
        return out.getvalue()
    monkeypatch.setattr(projects,'build_runtime_project',wrong)
    with pytest.raises(pipeline.PipelineError,match='no export was recorded'):
        projects.export_project(idea['idea_id'],{'expected_revision':idea['revision']})
    assert projects.projects(idea['idea_id'])['projects']==[]
