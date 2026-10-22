"""Organization readiness is a paged saved-design inventory, not an execution gate."""
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import pytest

from gateway.process_collaboration import ProcessArchiveWrite,ProcessCollaborationError,ProcessMember,SharedProcessWrite
from shared.process_readiness import design_findings
from shared.workflow_knowledge import KnowledgeWrite
from test_process_collaboration import store,person,model,grant,review,app,write


def test_findings_distinguish_missing_declarations_from_intentional_human_work():
    graph=model().model_dump()
    graph['activities'][0].update(implementation='human',responsibility_label='Case reviewer')
    graph['activities'][1].update(kind='end')
    graph['business_rules']=[{'id':'text','text':'PRIVATE_RULE_TEXT','user_confirmed':False},
        {'id':'structured','text':'Explicit approval','user_confirmed':True,'predicate':{
            'match':'all','conditions':[{'path':'/PRIVATE_FACT_PATH','operator':'eq','expected':'PRIVATE_EXPECTED'}]}}]
    findings=design_findings(graph)
    assert findings['unconfirmed_rule_ids']==['text']
    assert findings['text_only_rule_ids']==['text']
    assert findings['structured_rule_ids_requiring_source_fact_implementation']==['structured']
    assert findings['unmapped_activity_ids']==[]
    assert findings['activity_ids_without_declared_responsibility']==[]
    assert findings['manual_review_activity_ids']==['open']
    assert findings['terminal_ids_without_confirmed_source_expectation']==['close']
    assert 'PRIVATE_' not in json.dumps(findings)
    graph['outcome_expectations']=[{'terminal_activity_id':'close','operation':'jira_issue_resolved','user_confirmed':True}]
    assert design_findings(graph)['terminal_ids_without_confirmed_source_expectation']==[]
    graph['outcome_expectations'][0]['user_confirmed']=False
    assert design_findings(graph)['terminal_ids_without_confirmed_source_expectation']==['close']


def test_readiness_pages_owner_filters_archive_review_and_private_knowledge_isolation(store):
    first=store.save(person(),write('Shared first'));second=store.save(person(),write('Shared second'))
    other=store.save(person('bob'),write('Other owner'))
    wid=first['process']['workflow_id']
    grant(store,wid,members=[ProcessMember(actor_id='bob',role='reviewer')])
    store.review(person('bob'),wid,review(first['process']))
    store.knowledge.save('private-owner',KnowledgeWrite(workflow_id='private',title='PRIVATE_KNOWLEDGE',
        procedure='PRIVATE_EVIDENCE',source_client='test',user_confirmed=True))
    store.save(person(org='other-org'),write('PRIVATE_OTHER_TENANT'))
    page=store.readiness_report(person('reader'),limit=2)
    assert page['page_process_count']==2 and page['has_more']
    next_page=store.readiness_report(person('reader'),limit=2,after=page['next_after'])
    assert next_page['page_process_count']==1 and not next_page['has_more']
    rows=page['processes']+next_page['processes']
    assert sorted(row['workflow_id'] for row in rows)==sorted([wid,second['process']['workflow_id'],other['process']['workflow_id']])
    assert sum(row['current_independent_design_review'] for row in rows)==1
    assert page['summary_scope']=='returned_page_only_not_organization_totals'
    filtered=store.readiness_report(person(),owner_actor_id='bob')
    assert [row['title'] for row in filtered['processes']]==['Other owner']
    assert 'PRIVATE_' not in json.dumps(page)+json.dumps(next_page)
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=2,archived=True,user_confirmed=True))
    assert store.readiness_report(person())['page_process_count']==2
    archived=store.readiness_report(person(),include_archived=True)
    archived_row=next(row for row in archived['processes'] if row['workflow_id']==wid)
    assert archived_row['review_status']=='reviewed_design' and not archived_row['current_independent_design_review']
    assert archived['page_inventory_counts']['archived']==1
    assert sum(archived['page_process_counts_with_findings'].values())>0
    for field in ('deployment_health_checked','customer_tool_bindings_checked','business_policy_compliance_verified',
                  'business_outcomes_verified','execution_authorized','personal_knowledge_scanned','raw_evidence_scanned'):
        assert archived[field] is False
    audits=[row for row in store.db.audit_rows('acme') if row['action']=='process.readiness.read']
    assert audits and all(set(row['details'])=={'returned','has_more'} for row in audits)
    with pytest.raises(ProcessCollaborationError):store.readiness_report(person(scopes=frozenset()))
    for values in ({'limit':True},{'limit':101},{'owner_actor_id':'x'*513},{'after':'x'*81}):
        with pytest.raises(ProcessCollaborationError):store.readiness_report(person(),**values)


def test_readiness_revision_review_and_owner_share_a_snapshot_during_edit(store,monkeypatch):
    first=store.save(person(),write('Before update'));wid=first['process']['workflow_id']
    grant(store,wid,members=[ProcessMember(actor_id='bob',role='reviewer')])
    store.review(person('bob'),wid,review(first['process']))
    original=store._review_state;changed=[False]
    def state(conn,p,workflow_id,revision):
        if not changed[0]:
            changed[0]=True
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(store.save,person(),write('After update',1),wid).result(timeout=10)
        return original(conn,p,workflow_id,revision)
    monkeypatch.setattr(store,'_review_state',state)
    prior=store.readiness_report(person())['processes'][0]
    assert prior['title']=='Before update' and prior['revision']==1 and prior['current_independent_design_review']
    current=store.readiness_report(person())['processes'][0]
    assert current['title']=='After update' and current['revision']==2 and not current['current_independent_design_review']


def test_readiness_http_authorization_strict_filters_and_no_store(tmp_path):
    with TestClient(app(tmp_path)) as client:
        path='/v1/human/process-readiness';auth=lambda who:{'Authorization':'Bearer '+who}
        assert client.get(path).status_code==401
        assert client.get(path,headers=auth('admin')).status_code==401
        assert client.get(path,headers=auth('no-process')).status_code==403
        saved=client.post('/v1/human/processes',headers=auth('alice'),json=write().model_dump()).json()
        response=client.get(path,headers=auth('reader'))
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert response.json()['processes'][0]['content_sha256']==saved['process']['content_sha256']
        assert client.get(path,headers=auth('foreign')).json()['processes']==[]
        for query in ['limit=0','limit=101','after='+'x'*81,'owner_actor_id='+'x'*513]:
            assert client.get(path+'?'+query,headers=auth('alice')).status_code==422
