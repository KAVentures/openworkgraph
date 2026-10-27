"""Shared source lineage, exact SDK bytes, current review and metadata-only history."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Event
import os
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from shared.process_agent_project import build_runtime_project,build_runtime_spec
from shared.shared_opportunity_project import ExportRequest,RegenerateRequest,Remove,Manifest,link_archive,spec_digest,MANIFEST_FILE
from shared.process_opportunity_review import Remove as OpportunityRemove
from gateway.shared_opportunity_projects import SharedOpportunityProjects,namespace,pin
from gateway.process_collaboration import ProcessCollaborationError as Error,ProcessArchiveWrite,ProcessDeleteWrite,SharedProcessWrite
from gateway.process_opportunity_review import SharedOpportunityStore
from test_shared_opportunity_review import root,save,decide
from test_process_collaboration import store,person,app,grant,review


def setup(store):
    r=root(store);opps=SharedOpportunityStore(store);save(opps,store,r);decide(opps,store,r,'approved')
    return r,opps,SharedOpportunityProjects(store)

def export(projects,r,who='alice',**changes):
    view=projects.detail(person(who),r['workflow_id'])
    values={'expected_opportunity':view['current_opportunity'],'expected_project_revision':view['latest_project_revision'],'expected_access_revision':view['access_revision'],'user_confirmed':True,**changes}
    return projects.generate(person(who),r['workflow_id'],ExportRequest.model_validate(values))

def regenerate(projects,r,record,who='alice',**changes):
    return projects.regenerate(person(who),r['workflow_id'],record['manifest']['project_id'],RegenerateRequest(expected_record_sha256=record['content_sha256'],expected_access_revision=projects.detail(person(who),r['workflow_id'])['access_revision'],user_confirmed=True,**changes))


def test_exact_source_manifest_and_every_original_sdk_entry_are_unchanged_with_deduplication(store):
    r,opps,projects=setup(store);wid=r['workflow_id'];source=deepcopy(opps.detail(person(),wid)['artifact'])
    with store.db.connect() as c:raw_source=projects.q(c,'SELECT owner,workflow_id,revision,payload FROM workflow_knowledge ORDER BY owner,workflow_id,revision').fetchall()
    with store.db.connect() as c:packet=projects.packet(c,person(),wid,source['data']['process'])
    base=build_runtime_project(packet);content,record=export(projects,r)
    manifest=record['manifest'];assert manifest['opportunity']==pin(source) and manifest['approved_opportunity']==pin(source)
    assert manifest['process']==pin(r) and manifest['spec_sha256']==spec_digest(build_runtime_spec(packet))
    assert manifest['base_archive_sha256']==sha256(base).hexdigest() and record['archive_sha256']==sha256(content).hexdigest()
    with ZipFile(BytesIO(base)) as original,ZipFile(BytesIO(content)) as linked:
        assert linked.namelist()==original.namelist()+[MANIFEST_FILE]
        for name in original.namelist():assert linked.read(name)==original.read(name)
        assert json.loads(linked.read(MANIFEST_FILE))==manifest
        assert json.loads(linked.read('workflow.spec.json'))['organization_catalog_reference']=={'organization_id':'acme'}
        assert b'budget_currency' not in linked.read(MANIFEST_FILE) and b'implementation_budget' not in linked.read(MANIFEST_FILE)
    again,duplicate=export(projects,r,who='ed');assert again==content and duplicate==record
    assert duplicate['created_by_actor_id']=='alice' and projects.detail(person(),wid)['latest_project_revision']==1
    assert regenerate(projects,r,record)==(content,record)
    assert opps.detail(person(),wid)['artifact']==source and store.detail(person(),wid)['process']==r
    with store.db.connect() as c:after=projects.q(c,'SELECT owner,workflow_id,revision,payload FROM workflow_knowledge WHERE owner<>? ORDER BY owner,workflow_id,revision',(namespace('acme'),)).fetchall()
    assert [tuple(row) for row in after]==[tuple(row) for row in raw_source]
    assert not record['archive_stored'] and not manifest['execution_authorized'] and not manifest['deployment_verified']


def test_direct_implementation_preserves_approval_basis_but_reassessment_and_new_approval_do_not(store):
    r,opps,projects=setup(store);wid=r['workflow_id'];content,record=export(projects,r)
    decide(opps,store,r,'implemented',who='alice',implementation_self_report_confirmed=True)
    view=projects.detail(person(),wid);status=view['items'][0]['source_status']
    assert status['current_approved_assessment_basis'] and not status['opportunity_snapshot_is_latest']
    assert regenerate(projects,r,record)==(content,record)
    newer,new_record=export(projects,r);assert new_record['manifest']['opportunity']['revision']==3 and new_record['manifest']['approved_opportunity']['revision']==2
    assert new_record['manifest']['project_id']!=record['manifest']['project_id']
    save(opps,store,r,expected=3)
    assert all(not item['source_status']['current_approved_assessment_basis'] for item in projects.detail(person(),wid)['items'])
    assert projects.detail(person(),wid,exporting_project=record['manifest']['project_id'])['items'][0]['manifest']==record['manifest']
    with pytest.raises(Error):regenerate(projects,r,record)
    with pytest.raises(Error):export(projects,r)
    decide(opps,store,r,'approved')
    assert not projects.detail(person(),wid)['items'][0]['source_status']['current_approved_assessment_basis']


def test_model_change_review_withdrawal_and_archive_withhold_current_claims_without_rewriting_history(store):
    r,opps,projects=setup(store);wid=r['workflow_id'];content,record=export(projects,r)
    store.review(person('bob'),wid,review(r,'changes_requested',expected=1))
    assert not projects.detail(person(),wid)['items'][0]['source_status']['current_approved_assessment_basis']
    with pytest.raises(Error):regenerate(projects,r,record)
    store.review(person('bob'),wid,review(r,'reviewed_design',expected=2))
    assert projects.detail(person(),wid)['items'][0]['source_status']['current_approved_assessment_basis']
    new_model=deepcopy(r['process_model']);new_model['goal']='Updated design'
    store.save(person(),SharedProcessWrite(title='Updated',process_model=new_model,expected_revision=1,user_confirmed=True,organization_sharing_confirmed=True),wid)
    assert not projects.detail(person(),wid)['items'][0]['source_status']['current_approved_assessment_basis']
    assert projects.detail(person(),wid)['items'][0]['manifest']==record['manifest']
    with pytest.raises(Error):export(projects,r)
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=2,archived=True,user_confirmed=True))
    with pytest.raises(Error):regenerate(projects,r,record)
    projects.delete(person(),wid,Remove(expected_project_revision=1,expected_access_revision=3,user_confirmed=True))
    assert projects.detail(person(),wid)['items']==[]


def test_current_permissions_scopes_access_and_registry_cas_are_required(store):
    r,opps,projects=setup(store);wid=r['workflow_id']
    for who in ['bob','reader']:
        with pytest.raises(Error):export(projects,r,who=who)
    with pytest.raises(Error):projects.detail(person(org='foreign'),wid)
    body=ExportRequest(expected_opportunity=pin(opps.detail(person(),wid)['artifact']),expected_project_revision=0,expected_access_revision=2,user_confirmed=True)
    with pytest.raises(Error):projects.generate(person(scopes=frozenset({'org:process:read'})),wid,body)
    with pytest.raises(Error):export(projects,r,user_confirmed=False)
    with pytest.raises(Error):export(projects,r,expected_access_revision=1)
    for wrong in ({**body.expected_opportunity.model_dump(),'revision':1},{**body.expected_opportunity.model_dump(),'content_sha256':'0'*64},{**body.expected_opportunity.model_dump(),'workflow_id':'other'}):
        with pytest.raises(Error):export(projects,r,expected_opportunity=wrong)
    content,record=export(projects,r)
    with pytest.raises(Error):projects.regenerate(person(),wid,record['manifest']['project_id'],RegenerateRequest(expected_record_sha256='0'*64,expected_access_revision=2,user_confirmed=True))
    with pytest.raises(Error):projects.generate(person(),wid,body)
    grant(store,wid,members=[{'actor_id':'bob','role':'reviewer'}],expected=2)
    with pytest.raises(Error):regenerate(projects,r,record,who='ed')
    assert projects.detail(person('reader'),wid)['items'][0]['manifest']==record['manifest']


def altered_archive(base,name,value):
    result=BytesIO()
    with ZipFile(BytesIO(base)) as source,ZipFile(result,'w',compression=ZIP_DEFLATED) as target:
        for item in source.infolist():target.writestr(item,source.read(item.filename))
        info=ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=ZIP_DEFLATED
        target.writestr(info,value)
    return result.getvalue()


def test_generator_drift_never_silently_replaces_old_archive_and_new_exports_get_distinct_identity(store,monkeypatch):
    import gateway.shared_opportunity_projects as module
    r,opps,projects=setup(store);wid=r['workflow_id'];content,record=export(projects,r);original=module.build_runtime_project
    monkeypatch.setattr(module,'build_runtime_project',lambda packet:altered_archive(original(packet),'generator-change.txt','New generator version'))
    with pytest.raises(Error,match='generator'):regenerate(projects,r,record)
    assert projects.detail(person(),wid)['items'][0]['archive_sha256']==sha256(content).hexdigest()
    new_content,new_record=export(projects,r)
    assert new_record['manifest']['project_id']!=record['manifest']['project_id'] and new_content!=content
    assert projects.detail(person(),wid)['latest_project_revision']==2


def test_opportunity_erasure_keeps_minimal_history_and_root_deletion_erases_registry_atomically(store,monkeypatch):
    r,opps,projects=setup(store);wid=r['workflow_id'];content,record=export(projects,r)
    opps.delete(person(),wid,OpportunityRemove(expected_revision=2,expected_access_revision=2,user_confirmed=True))
    after=projects.detail(person(),wid);assert not after['items'][0]['source_status']['current_approved_assessment_basis']
    assert after['items'][0]['manifest']==record['manifest']
    with pytest.raises(Error):regenerate(projects,r,record)
    original=store._audit
    def fail(*args,**kwargs):raise RuntimeError('Synthetic audit failure')
    with monkeypatch.context() as patch:
        patch.setattr(store,'_audit',fail)
        with pytest.raises(RuntimeError):store.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    assert projects.detail(person(),wid)['latest_project_revision']==1
    store.delete(person(),wid,ProcessDeleteWrite(expected_revision=1,expected_access_revision=2,user_confirmed=True))
    with store.db.connect() as c:assert projects.latest(c,person(),wid)==0


def test_corruption_audit_failures_and_reserved_manifest_withhold_results(store,monkeypatch):
    import gateway.shared_opportunity_projects as module
    r,opps,projects=setup(store);wid=r['workflow_id'];original=module.build_runtime_project
    monkeypatch.setattr(module,'build_runtime_project',lambda packet:altered_archive(original(packet),MANIFEST_FILE,'bad'))
    with pytest.raises(Error):export(projects,r)
    assert projects.detail(person(),wid)['latest_project_revision']==0
    monkeypatch.setattr(module,'build_runtime_project',lambda packet:b'not a ZIP')
    with pytest.raises(Error,match='could not be generated'):export(projects,r)
    assert projects.detail(person(),wid)['latest_project_revision']==0
    monkeypatch.setattr(module,'build_runtime_project',original)
    def fail(*args,**kwargs):raise RuntimeError('Synthetic audit failure')
    with monkeypatch.context() as patch:
        patch.setattr(store,'_audit',fail)
        with pytest.raises(RuntimeError):export(projects,r)
    assert projects.detail(person(),wid)['latest_project_revision']==0
    content,record=export(projects,r)
    with monkeypatch.context() as patch:
        patch.setattr(store.db,'audit',fail)
        with pytest.raises(RuntimeError):projects.detail(person(),wid,exporting_project=record['manifest']['project_id'])
    with store.write_transaction() as c:projects.q(c,'UPDATE workflow_knowledge SET payload=? WHERE owner=? AND workflow_id=?',('{"private-corruption":true}',namespace('acme'),wid))
    result=projects.detail(person(),wid);assert not result['integrity_ok'] and result['items']==[] and not result['can_generate']
    with pytest.raises(Error):projects.detail(person(),wid,exporting_project=record['manifest']['project_id'])
    projects.delete(person(),wid,Remove(expected_project_revision=1,expected_access_revision=2,user_confirmed=True))
    assert projects.detail(person(),wid)['integrity_ok']


def test_all_fifty_project_records_remain_exact_and_duplicate_at_capacity_is_allowed(store,monkeypatch):
    import gateway.shared_opportunity_projects as module
    r,opps,projects=setup(store);wid=r['workflow_id'];original=module.build_runtime_project;marker={'value':0}
    monkeypatch.setattr(module,'build_runtime_project',lambda packet:altered_archive(original(packet),'version.txt',str(marker['value'])))
    first=None
    for i in range(50):
        marker['value']=i;content,record=export(projects,r)
        if first is None:first=deepcopy(record)
    assert projects.detail(person(),wid)['latest_project_revision']==50
    content,record=export(projects,r);assert record['revision']==50
    marker['value']=50
    with pytest.raises(Error):export(projects,r)
    assert projects.detail(person(),wid,exporting_project=first['manifest']['project_id'])['items'][0]['manifest']==first['manifest']
    assert len(projects.detail(person(),wid)['items'])==50
    with store.write_transaction() as c:projects.q(c,'DELETE FROM workflow_knowledge WHERE owner=? AND workflow_id=? AND revision=?',(namespace('acme'),wid,25))
    incomplete=projects.detail(person(),wid);assert not incomplete['integrity_ok'] and incomplete['items']==[] and not incomplete['can_generate']


def test_real_http_preflight_bounded_parser_zip_headers_and_audited_manifest(tmp_path):
    with TestClient(app(tmp_path)) as client:
        store=client.app.state.organization_process_store;r,opps,projects=setup(store);wid=r['workflow_id'];url='/v1/human/processes/'+wid+'/opportunity-projects';headers={'Authorization':'Bearer alice'}
        assert client.post(url,headers={'Authorization':'Bearer reader'},content='not json').status_code==403
        assert client.post(url,headers={**headers,'Content-Type':'application/json'},content='{"user_confirmed":true,"user_confirmed":false}').status_code==422
        body=ExportRequest(expected_opportunity=pin(opps.detail(person(),wid)['artifact']),expected_project_revision=0,expected_access_revision=2,user_confirmed=True).model_dump(mode='json')
        result=client.post(url,headers=headers,json=body);assert result.status_code==200,result.text
        assert result.headers['content-type']=='application/zip' and result.headers['cache-control']=='no-store'
        assert result.headers['x-owg-archive-sha256']==sha256(result.content).hexdigest()
        project_id=result.headers['x-owg-project-id'];record=client.get(url+'/'+project_id+'/manifest',headers=headers).json()['items'][0]
        assert record['content_sha256']==result.headers['x-owg-project-record-sha256']
        assert client.get(url+'/'+project_id+'/manifest',headers={'Authorization':'Bearer foreign'}).status_code==404


def test_same_clock_replacement_approval_is_not_mistaken_for_the_exported_named_decision(store,monkeypatch):
    import gateway.process_opportunity_review as module
    monkeypatch.setattr(module,'_now',lambda:'2026-09-01T00:00:00+00:00')
    r,opps,projects=setup(store);wid=r['workflow_id'];content,record=export(projects,r)
    first=opps.detail(person(),wid)['artifact']['approval']
    decide(opps,store,r,'on_hold');decide(opps,store,r,'assessing',who='alice');decide(opps,store,r,'approved')
    assert opps.detail(person(),wid)['artifact']['approval']==first
    status=projects.detail(person(),wid)['items'][0]['source_status']
    assert not status['current_approved_assessment_basis'] and 'opportunity_approval_replaced' in status['blockers']
    with pytest.raises(Error):regenerate(projects,r,record)


def test_simultaneous_exports_cannot_overwrite_registry_revision(store):
    r,opps,projects=setup(store);wid=r['workflow_id'];barrier=Barrier(2)
    request=ExportRequest(expected_opportunity=pin(opps.detail(person(),wid)['artifact']),expected_project_revision=0,expected_access_revision=2,user_confirmed=True)
    def generate():
        barrier.wait(timeout=10)
        try:projects.generate(person(),wid,request);return 'saved'
        except Error:return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(generate) for _ in range(2)];assert sorted(f.result(timeout=30) for f in futures)==['refused','saved']
    assert projects.detail(person(),wid)['latest_project_revision']==1


def test_reassessment_waiting_on_generation_invalidates_present_basis_after_commit(store,monkeypatch):
    import gateway.shared_opportunity_projects as module
    r,opps,projects=setup(store);wid=r['workflow_id'];original=module.build_runtime_project
    building=Event();release=Event();mutation=Event()
    def held(packet):
        building.set();assert release.wait(timeout=20);return original(packet)
    monkeypatch.setattr(module,'build_runtime_project',held)
    request=ExportRequest(expected_opportunity=pin(opps.detail(person(),wid)['artifact']),expected_project_revision=0,expected_access_revision=2,user_confirmed=True)
    def reassess():mutation.set();return save(opps,store,r,expected=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        generation=pool.submit(projects.generate,person(),wid,request);assert building.wait(timeout=10)
        change=pool.submit(reassess);assert mutation.wait(timeout=10)
        release.set();content,record=generation.result(timeout=30);change.result(timeout=30)
    view=projects.detail(person(),wid)
    assert not view['items'][0]['source_status']['current_approved_assessment_basis'] and not view['can_generate']
    with ZipFile(BytesIO(content)) as archive:assert json.loads(archive.read(MANIFEST_FILE))['opportunity']['revision']==2


def test_manifest_identity_and_authority_flags_cannot_be_promoted(store):
    r,opps,projects=setup(store);content,record=export(projects,r);manifest=record['manifest']
    for change in ({'project_id':'project_'+'0'*64},{'execution_authorized':True},{'deployment_verified':True},{'business_outcomes_verified':True},{'per_run_binary_attribution_verified':True}):
        with pytest.raises(ValidationError):Manifest.model_validate({**manifest,**change})
    wrong=deepcopy(manifest);wrong['process']['workflow_id']='other'
    with pytest.raises(ValidationError):Manifest.model_validate(wrong)


@pytest.mark.skipif(not os.getenv('OWG_AGENT_SDK_PYTHON'),reason='An isolated actual SDK interpreter is required')
@pytest.mark.parametrize('parallel,source',[(False,True),('nested','decision')])
def test_added_lineage_manifest_preserves_actual_sdk_transport_source_checks_monitoring_and_model_revocation(tmp_path,monkeypatch,parallel,source):
    """Composition check: added metadata cannot alter the shipped runner bytes.

    Synthetic pins here are declared test metadata, not production approvals.
    The separate database/HTTP/browser tests establish real registry generation.
    """
    import test_process_customer_runtime as customer
    original=customer.build_runtime_project
    def linked(packet):
        base=original(packet);reference={k:packet[k] for k in ('workflow_id','revision','content_sha256')}
        opportunity={**reference,'revision':2,'content_sha256':'b'*64}
        archive,manifest=link_archive(base,{'organization_id':packet['organization_catalog_reference']['organization_id'],
            'opportunity':opportunity,'approved_opportunity':opportunity,'process':reference,'assessment_sha256':'c'*64,'assessed_in_revision':1,
            'spec_sha256':spec_digest(build_runtime_spec(packet)),'base_archive_sha256':sha256(base).hexdigest()})
        with ZipFile(BytesIO(archive)) as linked_zip,ZipFile(BytesIO(base)) as original_zip:
            assert all(linked_zip.read(n)==original_zip.read(n) for n in original_zip.namelist())
        return archive
    monkeypatch.setattr(customer,'build_runtime_project',linked)
    customer.test_customer_project_executes_exact_mcp_calls_and_checks_independent_source_state(tmp_path,monkeypatch,True,source,parallel)
