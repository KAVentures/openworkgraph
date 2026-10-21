"""Organization scenarios share design permissions and exact-revision review."""
import json

from fastapi.testclient import TestClient
import pytest

from gateway.process_collaboration import ProcessCollaborationError, ProcessMember, ProcessArchiveWrite, SharedProcessWrite
from shared.process_opportunities import OpportunityScenario
from test_process_collaboration import store,person,model,grant,review,app,write


def scenario(currency='USD',minutes=10):
    return OpportunityScenario(currency=currency,monthly_cases=100.,minutes_saved_per_case=float(minutes),
        eligible_fraction_percent=100.,loaded_hourly_cost=60.,recurring_monthly_cost=100.,
        one_time_implementation_cost=1800.,sensitivity_percent=25.)


def reviewed(store,title,currency='USD',minutes=10):
    graph=model().model_copy(update={'opportunity_scenario':scenario(currency,minutes)})
    result=store.save(person(),SharedProcessWrite(title=title,process_model=graph,user_confirmed=True,organization_sharing_confirmed=True))
    wid=result['process']['workflow_id']
    grant(store,wid,members=[ProcessMember(actor_id='bob',role='reviewer')])
    store.review(person('bob'),wid,review(result['process']))
    return result


def test_ranking_uses_only_current_active_independently_reviewed_scenarios(store):
    high=reviewed(store,'High');low=reviewed(store,'Low',minutes=1);euro=reviewed(store,'Euro',currency='EUR')
    pending=store.save(person(),write('Pending'))
    archived=reviewed(store,'Archived');wid=archived['process']['workflow_id']
    store.archive(person(),wid,ProcessArchiveWrite(expected_access_revision=2,archived=True,user_confirmed=True))
    report=store.opportunity_report(person('reader'),currency='USD')
    assert [row['title'] for row in report['opportunities']]==['High','Low']
    assert report['opportunities'][0]['projection']['net_monthly_value_before_other_costs']==900
    assert report['opportunities'][0]['projection']['simple_payback_months']==2
    assert report['opportunities'][1]['projection']['simple_payback_months'] is None
    assert report['company_savings_total'] is None and report['scenarios_may_overlap']
    assert report['labor_savings_measured'] is False and report['execution_authorized'] is False
    counts=report['inventory_counts_in_scanned_records']
    assert counts['active']==4 and counts['archived']==1 and counts['needs_review']==1
    assert counts['reviewed_design']==3 and counts['reviewed_scenarios_in_other_currencies']==1
    assert [row['title'] for row in store.opportunity_report(person(),currency='EUR')['opportunities']]==['Euro']
    # A new revision invalidates the old peer review and removes that scenario.
    store.save(person(),SharedProcessWrite(title='High new revision',expected_revision=1,
        process_model=model().model_copy(update={'opportunity_scenario':scenario()}),
        user_confirmed=True,organization_sharing_confirmed=True),high['process']['workflow_id'])
    assert [r['title'] for r in store.opportunity_report(person(),currency='USD')['opportunities']]==['Low']
    store.review(person('bob'),low['process']['workflow_id'],review(low['process'],'changes_requested',1))
    assert not store.opportunity_report(person(),currency='USD')['opportunities']


def test_report_has_tenant_scope_and_minimized_read_audit(store):
    saved=reviewed(store,'Shared design')
    from shared.workflow_knowledge import KnowledgeWrite
    store.knowledge.save(json.dumps(['acme','alice'],separators=(',',':')),KnowledgeWrite(
        workflow_id=saved['process']['workflow_id'],title='PRIVATE DESIGN',procedure='PRIVATE PROCEDURE',
        source_client='test',user_confirmed=True,process_model=model().model_copy(update={'opportunity_scenario':scenario(minutes=100)})))
    with pytest.raises(ProcessCollaborationError):store.opportunity_report(person(scopes=frozenset({'org:evidence:read'})),currency='USD')
    with pytest.raises(ProcessCollaborationError):store.opportunity_report(person(),currency='XYZ')
    foreign=store.opportunity_report(person(org='other'),currency='USD')
    assert foreign['shared_processes_scanned']==0 and not foreign['opportunities']
    result=store.opportunity_report(person('reader'),currency='USD')
    assert result['raw_evidence_scanned'] is False and result['personal_knowledge_scanned'] is False
    text=json.dumps(result)
    assert 'PRIVATE' not in text and result['opportunities'][0]['title']=='Shared design'
    for field in ('owner_actor_id','reviewer_actor_id','comment','record_ref','email'):
        assert field not in text
    audits=[r for r in store.db.audit_rows('acme') if r['action']=='process.opportunities.read']
    assert audits and set(audits[-1]['details'])=={'currency','scanned','returned'}


def test_report_http_permissions_cache_and_currency_validation(tmp_path):
    with TestClient(app(tmp_path)) as client:
        path='/v1/human/process-opportunities'
        assert client.get(path).status_code==401
        assert client.get(path,headers={'Authorization':'Bearer no-process'}).status_code==403
        response=client.get(path,headers={'Authorization':'Bearer reader'})
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert response.json()['currency']=='USD'
        assert client.get(path+'?currency=XYZ',headers={'Authorization':'Bearer reader'}).status_code==422


def test_report_keeps_model_and_review_on_one_snapshot_during_a_concurrent_edit(store,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    first=reviewed(store,'Before edit');wid=first['process']['workflow_id']
    original=store._review_state;changed=[False]
    def state(conn,p,workflow_id,revision):
        if not changed[0]:
            changed[0]=True
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(store.save,person(),SharedProcessWrite(title='After edit',expected_revision=1,
                    process_model=model().model_copy(update={'opportunity_scenario':scenario()}),
                    user_confirmed=True,organization_sharing_confirmed=True),wid).result(timeout=10)
        return original(conn,p,workflow_id,revision)
    monkeypatch.setattr(store,'_review_state',state)
    report=store.opportunity_report(person(),currency='USD')
    assert report['opportunities'][0]['title']=='Before edit' and report['opportunities'][0]['revision']==1
    assert not store.opportunity_report(person(),currency='USD')['opportunities']
