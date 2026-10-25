"""Exact local catalog identity across the process-to-opportunity journey."""
from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from gateway.db import GatewayDB
from shared.process_catalog import CatalogWrite, ProcessCatalog
from shared.process_model import ProcessModel
from shared.workflow_knowledge import KnowledgeStore
from server import automation_pipeline as pipeline

ROOT = Path(__file__).resolve().parents[1]


def model(goal='Review the chosen case'):
    return ProcessModel(goal=goal, baseline_source='user_declared', baseline_coverage='user_declared_design',
        baseline_path=['Review'], activities=[{'id':'review','label':'Review'}], transitions=[],
        entry_activity_id='review', terminal_activity_ids=['review'])


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'DATA_DIR', tmp_path)
    store = KnowledgeStore(GatewayDB(f'sqlite:///{tmp_path / "catalog.db"}'))
    catalog = ProcessCatalog(store, 'local')
    monkeypatch.setattr(pipeline, '_catalog', lambda: catalog)
    return catalog


def save(catalog, revision=0, goal='Review the chosen case'):
    return catalog.save(CatalogWrite(workflow_id='review', expected_revision=revision,
        title='Case review', process_model=model(goal), user_confirmed=True))


def ref(record):
    return {key:record[key] for key in ('workflow_id','revision','content_sha256')}


def assess(idea):
    idea = pipeline.transition(idea['idea_id'], {'to':'assessing','expected_revision':idea['revision']})
    return pipeline.assess(idea['idea_id'], {'ease':{q:4 for q in pipeline.EASE_QUESTIONS},
        'cases_per_month':100,'minutes_per_case':6,'eligible_share':.5,'expected_revision':idea['revision']})


def test_pin_resolves_actual_revision_without_copying_model_or_evidence(catalog):
    saved = save(catalog)
    idea = pipeline.create_from_process(ref(saved))
    assert idea['process_reference'] == ref(saved)
    assert idea['source'] == {'kind':'process_catalog','reference':'review@1'}
    assert idea['title'] == saved['title'] and idea['description'] == saved['process_model']['goal']
    assert 'process_model' not in idea and 'evidence_refs' not in idea
    status = pipeline.process_status(idea['idea_id'])
    assert status['status'] == 'current' and status['reference_is_current']
    assert status['latest_revision'] == 1
    assert status['execution_authorized'] is False and status['business_outcomes_verified'] is False
    assert status['independent_design_review_verified'] is False
    assert pipeline.get(idea['idea_id']) == idea  # status reads do not rewrite stored history/revision


def test_manual_references_cannot_assert_verified_lineage(catalog):
    saved = save(catalog)
    manual = pipeline.create({'title':'Manual idea','source':{'kind':'process_catalog','reference':'review@1'}})
    assert 'process_reference' not in manual
    assert pipeline.process_status(manual['idea_id'])['status'] == 'unbound_reference'
    with pytest.raises(pipeline.PipelineError, match='Extra inputs'):
        pipeline.create({'title':'Forged','process_reference':ref(saved)})
    with pytest.raises(pipeline.PipelineError, match='Extra inputs'):
        pipeline.create_from_process({**ref(saved), 'owner':'another-person'})
    with pytest.raises(pipeline.PipelineError, match='changed'):
        pipeline.create_from_process({**ref(saved), 'content_sha256':'0'*64})
    foreign = ProcessCatalog(catalog.store, 'someone-else')
    secret = foreign.save(CatalogWrite(workflow_id='private',title='PRIVATE_TITLE',process_model=model(),user_confirmed=True))
    with pytest.raises(pipeline.PipelineError, match='unavailable') as exc:
        pipeline.create_from_process(ref(secret))
    assert 'PRIVATE_TITLE' not in str(exc.value)
    assert len(pipeline.pipeline()['ideas']) == 1


def test_approval_snapshot_and_changed_source_block_implementation(catalog):
    saved = save(catalog)
    idea = assess(pipeline.create_from_process(ref(saved)))
    approved = pipeline.transition(idea['idea_id'], {'to':'approved','expected_revision':idea['revision']})
    assert approved['history'][-1]['process_reference'] == ref(saved)
    second = save(catalog, 1, 'Review the changed case')
    status = pipeline.process_status(idea['idea_id'])
    assert status['status'] == 'changed' and status['latest_revision'] == 2
    assert status['process_reference'] == ref(saved) and not status['reference_is_current']
    with pytest.raises(pipeline.PipelineError, match='linked process changed'):
        pipeline.transition(idea['idea_id'], {'to':'implemented','expected_revision':approved['revision']})
    assert pipeline.get(idea['idea_id']) == approved
    # Historical links remain inspectable but cannot approve a new stale idea.
    historical = assess(pipeline.create_from_process(ref(saved)))
    with pytest.raises(pipeline.PipelineError, match='linked process changed'):
        pipeline.transition(historical['idea_id'], {'to':'approved','expected_revision':historical['revision']})
    fresh = assess(pipeline.create_from_process(ref(second)))
    fresh = pipeline.transition(fresh['idea_id'], {'to':'approved','expected_revision':fresh['revision']})
    fresh = pipeline.transition(fresh['idea_id'], {'to':'implemented','expected_revision':fresh['revision']})
    assert fresh['state'] == 'implemented' and not fresh['execution_authorized']


def test_forget_and_reuse_same_process_id_cannot_retarget_pin(catalog):
    saved = save(catalog)
    idea = assess(pipeline.create_from_process(ref(saved)))
    catalog.store.forget('local','review')
    assert pipeline.process_status(idea['idea_id'])['status'] == 'unavailable'
    with pytest.raises(pipeline.PipelineError, match='linked process changed'):
        pipeline.transition(idea['idea_id'], {'to':'approved','expected_revision':idea['revision']})
    replacement = save(catalog, goal='Review an unrelated business process')
    assert replacement['revision'] == saved['revision'] == 1
    assert replacement['content_sha256'] != saved['content_sha256']
    assert pipeline.process_status(idea['idea_id'])['status'] == 'reference_mismatch'
    with pytest.raises(pipeline.PipelineError, match='linked process changed'):
        pipeline.transition(idea['idea_id'], {'to':'approved','expected_revision':idea['revision']})
    assert pipeline.get(idea['idea_id']) == idea


def test_uncheckable_source_fails_closed_and_stale_idea_revision_still_refused(catalog, monkeypatch):
    saved = save(catalog)
    idea = assess(pipeline.create_from_process(ref(saved)))
    def broken():
        raise RuntimeError('PRIVATE_BACKEND_DETAILS')
    monkeypatch.setattr(pipeline, '_catalog', broken)
    with pytest.raises(pipeline.PipelineError, match='could not be checked'):
        pipeline.create_from_process(ref(saved))
    status = pipeline.process_status(idea['idea_id'])
    assert status['status'] == 'check_failed' and 'PRIVATE_BACKEND_DETAILS' not in json.dumps(status)
    with pytest.raises(pipeline.PipelineError, match='changed'):
        pipeline.transition(idea['idea_id'], {'to':'approved','expected_revision':1})
    with pytest.raises(pipeline.PipelineError, match='linked process changed'):
        pipeline.transition(idea['idea_id'], {'to':'approved','expected_revision':idea['revision']})
    assert pipeline.get(idea['idea_id']) == idea
    # Unlinked legacy ideas never require new catalog access.
    manual = assess(pipeline.create({'title':'Legacy'}))
    assert pipeline.transition(manual['idea_id'], {'to':'approved','expected_revision':manual['revision']})['state'] == 'approved'


def test_actual_local_routes_resolve_pin_and_require_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.local_process_catalog import catalog
from shared.process_catalog import CatalogWrite
from shared.process_model import ProcessModel
model=ProcessModel(goal='Review the chosen case',baseline_source='user_declared',baseline_path=['Review'],
    activities=[{'id':'review','label':'Review'}],transitions=[],entry_activity_id='review',terminal_activity_ids=['review'])
record=catalog.save(CatalogWrite(workflow_id='local_review',title='Local case review',process_model=model,user_confirmed=True))
ref={k:record[k] for k in ('workflow_id','revision','content_sha256')}
with TestClient(app) as c:
    session={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert c.post('/v1/automation-pipeline/from-process',json=ref,headers=bearer).status_code in (401,403)
    created=c.post('/v1/automation-pipeline/from-process',json=ref,headers=session)
    assert created.status_code==200,created.text
    assert created.headers['cache-control']=='no-store'
    idea=created.json()
    status_url='/v1/automation-pipeline/'+idea['idea_id']+'/process-status'
    assert c.get(status_url,headers=bearer).status_code in (401,403)
    status=c.get(status_url,headers=session)
    assert status.status_code==200 and status.json()['status']=='current'
    assert status.headers['cache-control']=='no-store'
    assert c.post('/v1/automation-pipeline/from-process',json={**ref,'content_sha256':'0'*64},headers=session).status_code==409
    assert c.post('/v1/automation-pipeline/from-process',json={**ref,'revision':True},headers=session).status_code==422
    # The existing catalog route and pipeline resolver share the same local owner/store.
    existing=c.get('/v1/process-catalog/revision',params={'workflow_id':'local_review'},headers=session)
    assert existing.status_code==200 and existing.json()['content_sha256']==ref['content_sha256']
'''
    env = os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),
               WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result = subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode == 0, result.stdout+result.stderr
