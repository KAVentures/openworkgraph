"""Tenant-bound report inspection uses existing process scopes and minimal audits."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from shared.process_catalog import ProcessCatalog
from shared.process_shadow_review import ShadowReviewRequest
from gateway.process_collaboration import SharedProcessWrite, ProcessCollaborationError, namespace
from test_process_collaboration import store, person, grant, review, app
from test_process_shadow_review import generated_report, model


def headers(actor):
    return {'Authorization': 'Bearer '+actor}


def publish():
    return SharedProcessWrite(title='Shared shadow review', process_model=model(), user_confirmed=True,
                              organization_sharing_confirmed=True)


def test_tenant_bound_reports_are_read_only_unverified_and_minimally_audited(store, tmp_path):
    saved = store.save(person(), publish()); record = saved['process']; wid = record['workflow_id']
    packet = ProcessCatalog(store.knowledge, namespace('acme')).export(wid, 1)
    packet['organization_catalog_reference'] = {'organization_id': 'acme'}
    report = generated_report(packet, tmp_path/'shared-project')
    body = ShadowReviewRequest(workflow_id=wid, revision=1, report=report)
    reader = person('reader', scopes=frozenset({'org:process:read'}))
    result = store.inspect_shadow_report(reader, wid, body)
    assert result['organization_id']=='acme' and result['exact_specification_match'] is True
    assert result['active_independently_reviewed_revision_at_inspection'] is False
    assert result['report_authenticity_verified'] is False and result['result_persisted'] is False
    assert result['minimal_inspection_audit_persisted'] is True
    grant(store,wid,members=[{'actor_id':'bob','role':'reviewer'}])
    store.review(person('bob'),wid,review(record))
    result = store.inspect_shadow_report(reader,wid,body)
    assert result['active_independently_reviewed_revision_at_inspection'] is True
    assert result['execution_authorized'] is False
    with pytest.raises(ProcessCollaborationError) as error:
        store.inspect_shadow_report(person(org='elsewhere'),wid,body)
    assert error.value.status==404
    with pytest.raises(ProcessCollaborationError) as error:
        store.inspect_shadow_report(person(scopes=frozenset({'self:evidence:read'})),wid,body)
    assert error.value.status==403
    # Matching IDs/models still cannot substitute a local or different-org spec.
    for origin in (None, {'organization_id':'elsewhere'}):
        changed=deepcopy(packet)
        if origin is None: changed.pop('organization_catalog_reference')
        else: changed['organization_catalog_reference']=origin
        other_report=generated_report(changed,tmp_path/('local' if origin is None else 'foreign'))
        with pytest.raises(ProcessCollaborationError) as error:
            store.inspect_shadow_report(reader,wid,body.model_copy(update={'report':type(body.report).model_validate(other_report)}))
        assert error.value.status==422
    store.save(person(),publish().model_copy(update={'expected_revision':1}),wid)
    old=store.inspect_shadow_report(reader,wid,body)
    assert old['process_reference']['revision']==1 and old['latest_revision_at_inspection']==2
    assert old['active_independently_reviewed_revision_at_inspection'] is False
    assert len(store.knowledge.history(namespace('acme'),wid)['workflows'])==2
    with store.db.connect() as conn:
        rows=store.db._execute(conn,"SELECT details_json FROM audit_log WHERE action='process.shadow_report.inspected'").fetchall()
    assert len(rows)==3
    for row in rows:
        details=json.loads(row[0])
        assert set(details)=={'workflow_id','revision','case_count','matching_case_count'}
        assert details['case_count']==1 and details['matching_case_count']==0
        assert 'PRIVATE_' not in row[0]


def test_shared_report_route_rejects_scopes_before_parsing_and_binds_origin(tmp_path):
    with TestClient(app(tmp_path)) as client:
        saved=client.post('/v1/human/processes',headers=headers('alice'),json=publish().model_dump()).json()
        record=saved['process'];wid=record['workflow_id'];url='/v1/human/processes/'+wid+'/shadow-review'
        packet=client.get('/v1/human/processes/'+wid+'/export',headers=headers('reader'),params={'revision':1}).json()
        packet['organization_catalog_reference']={'organization_id':'acme'}
        report=generated_report(packet,tmp_path/'route-project');body={'workflow_id':wid,'revision':1,'report':report}
        assert client.post(url,json=body).status_code==401
        assert client.post(url,headers={**headers('no-process'),'Content-Type':'application/json'},content=b'bad').status_code==403
        assert client.post(url,headers=headers('foreign'),json=body).status_code==404
        result=client.post(url,headers=headers('reader'),json=body)
        assert result.status_code==200,result.text
        assert result.headers['cache-control']=='no-store'
        assert result.json()['organization_id']=='acme' and 'PRIVATE_' not in result.text
        for changes in ({'report':report|{'raw_case':'PRIVATE_CASE_VALUE'}},{'revision':True},
                        {'report':report|{'execution_authorized':True}},{'report':report|{'spec_sha256':'c'*64}}):
            rejected=client.post(url,headers=headers('reader'),json=body|changes)
            assert rejected.status_code==422 and 'PRIVATE_' not in rejected.text
