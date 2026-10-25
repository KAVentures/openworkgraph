"""Exact hash associations preserve customer-report uncertainty and historical scope."""
from io import BytesIO
import json
from zipfile import ZipFile

import pytest

from server import automation_pipeline as pipeline, automation_monitoring as am
from server import opportunity_projects as projects, project_report_links as links
from tests.test_opportunity_projects import setup
from tests.test_process_opportunity_lineage import assess


@pytest.fixture
def prepared(setup, tmp_path, monkeypatch):
    catalog,record,idea=setup
    monkeypatch.setattr(am,'DATA_DIR',tmp_path)
    content,artifact=projects.export_project(idea['idea_id'],{'expected_revision':idea['revision']})
    with ZipFile(BytesIO(content)) as z:spec=json.loads(z.read('workflow.spec.json'))
    return catalog,record,idea,artifact,spec


def report(spec_hash=None, name='Reported agent'):
    deployment={'name':name,'runtime':'openai-agents'}
    if spec_hash is not None:deployment['workflow_spec_sha256']=spec_hash
    return {'format':am.FORMAT,'deployment':deployment,'runs':[{'run_id':'reported_run',
        'started_at':'2024-03-01T10:00:00Z','finished_at':'2024-03-01T10:01:00Z','status':'succeeded',
        'steps':[],'outcome':{'verified':True,'source':'CLIENT_ASSERTION_NOT_INDEPENDENT'}}]}


def deployment(spec, bound=True):
    result=am.import_report(report(am.spec_digest(spec) if bound else None))['deployment']
    am.attach_design(result['deployment_id'],spec)
    return result['deployment_id']


def body(idea,artifact,dep):
    return {'project_id':artifact['project_id'],'deployment_id':dep,'expected_revision':idea['revision']}


def test_match_is_an_explicit_reported_association_not_outcome_verification(prepared):
    _,_,idea,artifact,spec=prepared
    dep=deployment(spec)
    legacy=report();legacy['runs'][0]['run_id']='legacy_unhashed_run'
    am.import_report(legacy,dep)
    assert links.links(idea['idea_id'])['links']==[] # never auto-associate
    before=pipeline.get(idea['idea_id'])
    saved=links.link(idea['idea_id'],body(idea,artifact,dep))
    repeated=links.link(idea['idea_id'],body(idea,artifact,dep))
    assert repeated==saved and saved['status']=='matching_reported_specification'
    for key in ('per_run_attribution_verified','independent_execution_verified','business_outcomes_verified','execution_authorized'):
        assert saved[key] is False
    listing=links.links(idea['idea_id'])
    assert len(listing['links'])==1 and listing['links'][0]['status']=='matching_reported_specification'
    assert pipeline.get(idea['idea_id'])==before
    reference=am.deployment_references()[0]
    assert set(reference)=={'deployment_id','name','runtime','design_sha256','reported_spec_sha256','reported_run_count'}
    assert reference['reported_run_count']==2
    assert 'CLIENT_ASSERTION' not in json.dumps(listing)


def test_unbound_and_wrong_specs_cannot_create_links(prepared):
    _,_,idea,artifact,spec=prepared
    no_hash=deployment(spec,bound=False)
    no_design=am.import_report(report(am.spec_digest(spec)))['deployment']['deployment_id']
    wrong={**spec,'content_sha256':'0'*64}
    wrong_dep=deployment(wrong)
    for dep in (no_hash,no_design,wrong_dep):
        with pytest.raises(pipeline.PipelineError,match='specification changed'):
            links.link(idea['idea_id'],body(idea,artifact,dep))
    assert links.links(idea['idea_id'])['links']==[]


def test_wrong_idea_and_stale_revision_cannot_associate(prepared):
    _,_,idea,artifact,spec=prepared
    dep=deployment(spec)
    other=pipeline.create({'title':'Another idea'})
    with pytest.raises(pipeline.PipelineError,match='recorded for this idea'):
        links.link(other['idea_id'],{**body(other,artifact,dep)})
    with pytest.raises(pipeline.PipelineError,match='changed'):
        links.link(idea['idea_id'],{**body(idea,artifact,dep),'expected_revision':1})
    assert links.links(idea['idea_id'])['links']==[]


def test_historical_artifact_can_link_without_reapproving_or_rebinding(prepared):
    catalog,record,idea,artifact,spec=prepared
    dep=deployment(spec)
    current=pipeline.assess(idea['idea_id'],{'ease':{q:3 for q in pipeline.EASE_QUESTIONS},'cases_per_month':10,
        'minutes_per_case':2,'eligible_share':.1,'expected_revision':idea['revision']})
    catalog.store.forget('local',record['workflow_id'])
    assert current['state']=='assessing'
    saved=links.link(idea['idea_id'],body(current,artifact,dep))
    assert saved['status']=='matching_reported_specification' and saved['execution_authorized'] is False
    assert pipeline.get(idea['idea_id'])==current


def test_deleted_changed_and_uncheckable_sources_do_not_retarget_link(prepared,monkeypatch):
    _,_,idea,artifact,spec=prepared
    dep=deployment(spec)
    links.link(idea['idea_id'],body(idea,artifact,dep))
    real=am.deployment_references
    monkeypatch.setattr(am,'deployment_references',lambda:[{**real()[0],'design_sha256':'0'*64}])
    assert links.links(idea['idea_id'])['links'][0]['status']=='specification_mismatch'
    def broken():raise RuntimeError('PRIVATE_SOURCE_DETAIL')
    monkeypatch.setattr(am,'deployment_references',broken)
    result=links.links(idea['idea_id'])
    assert result['links'][0]['status']=='check_failed' and not result['monitoring_metadata_available']
    assert 'PRIVATE_SOURCE_DETAIL' not in json.dumps(result)
    monkeypatch.setattr(am,'deployment_references',real)
    am.delete_deployment(dep)
    result=links.links(idea['idea_id'])
    assert result['links'][0]['status']=='unavailable'
    assert result['links'][0]['deployment_id']==dep
    assert len(result['links'])==1


def test_link_cap_unlink_and_idea_deletion_preserve_monitoring_reports(prepared,monkeypatch):
    _,_,idea,artifact,spec=prepared
    monkeypatch.setattr(links,'MAX_LINKS_PER_PROJECT',1)
    first=deployment(spec);second=deployment(spec)
    links.link(idea['idea_id'],body(idea,artifact,first))
    with pytest.raises(pipeline.PipelineError,match='at most 1'):
        links.link(idea['idea_id'],body(idea,artifact,second))
    assert links.unlink(idea['idea_id'],body(idea,artifact,first))['removed']
    assert len(am.deployment_references())==2
    links.link(idea['idea_id'],body(idea,artifact,second))
    pipeline.delete(idea['idea_id'])
    with pipeline._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_report_links').fetchone()[0]==0
    assert len(am.deployment_references())==2


def test_actual_link_routes_require_human_session_and_preserve_report_claims(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    code = r''' 
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth, automation_pipeline as p, automation_monitoring as am
from server.local_process_catalog import catalog
from server.opportunity_projects import export_project
from tests.test_process_opportunity_lineage import save,ref,assess
from tests.test_project_report_links import deployment
from io import BytesIO
from zipfile import ZipFile
import json
r=save(catalog);i=assess(p.create_from_process(ref(r)))
i=p.transition(i['idea_id'],{'to':'approved','expected_revision':i['revision']})
content,a=export_project(i['idea_id'],{'expected_revision':i['revision']})
with ZipFile(BytesIO(content)) as z: spec=json.loads(z.read('workflow.spec.json'))
dep=deployment(spec)
body={'project_id':a['project_id'],'deployment_id':dep,'expected_revision':i['revision']}
with TestClient(app) as c:
 h={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
 bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
 url='/v1/automation-pipeline/'+i['idea_id']+'/project-reports'
 assert c.get(url,headers=bearer).status_code in (401,403)
 assert c.post(url,json=body,headers=bearer).status_code in (401,403)
 assert c.post(url+'/unlink',json=body,headers=bearer).status_code in (401,403)
 result=c.post(url,json=body,headers=h)
 assert result.status_code==200,result.text
 assert result.headers['cache-control']=='no-store' and result.json()['business_outcomes_verified'] is False
 assert c.get(url,headers=h).json()['links'][0]['status']=='matching_reported_specification'
 assert c.post(url,json={**body,'expected_revision':1},headers=h).status_code==409
 assert c.post(url+'/unlink',json=body,headers=h).json()['removed'] is True
 assert c.get(url,headers=h).json()['links']==[]
 assert len(am.deployment_references())==1
'''
    env=os.environ.copy()
    env.update(WORKFLOW_OBSERVER_DATA=str(tmp_path/'data'),WORKFLOW_OBSERVER_AUTH_DIR=str(tmp_path/'auth'),WORKFLOW_OBSERVER_CONFIG=str(tmp_path/'config.json'))
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
