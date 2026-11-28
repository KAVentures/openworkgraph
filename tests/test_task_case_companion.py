import copy
import hashlib
import pytest
from mcp_server import task_case_companion as case

REF = 'owg:r:' + 'a'*32


def fixture(provider='github', kind='pull_request'):
    return {'format':'openworkgraph.observed-case-context.v1','record_ref':REF,'record_observed':True,
            'record_type':{'provider':provider,'kind':kind},'timeline':[{'event_id':'event1','observed_at':'2026-11-28','observed_action_type':'click','source':'browser'}],
            'timeline_truncated':False,'record_identity_complete_within_retained_window':True,
            'does_not_query_open_work_inventory':True,'canonical_evidence_unchanged':True,
            'source_reported_status':{'availability':'unknown','state':'unknown','verified_at':None,'verified_operational_outcome':None,'end_customer_outcome_verified':False},
            'authorization':{'read_only':True,'may_contact_customer':False,'may_send_invoice':False,'may_issue_refund':False,
                             'agent_execution_authorized':False,'observed_behavior_is_authority':False}}


@pytest.mark.parametrize('provider,kind,operation',[('github','pull_request','github_pr_merged'),('jira','issue','jira_issue_resolved'),('stripe','invoice','stripe_invoice_paid'),('stripe','refund','stripe_refund_processor_succeeded'),('other','record',None)])
def test_exact_case_read_and_navigation(provider,kind,operation):
    data=fixture(provider,kind); calls=[]
    def get(path,params):
        calls.append((path,params)); return data
    result=case.read(REF,get)
    assert calls==[('/v1/case-context',{'resource_ref':REF,'include_source_status':False})]
    assert result['case_observations']==data and result['ai_visible_api_sha256']==hashlib.sha256(case.encoded(data)).hexdigest()
    assert result['verification_navigation']==(None if operation is None else {'tool':'verify_case_operation','expected_operation':operation})
    assert all(result[k] is False for k in ('provider_refresh_requested','source_operation_verified','business_outcome_verified','task_record_relationship_verified','execution_authorized','atomic_snapshot_verified'))
    data['timeline'].clear(); assert result['case_observations']['timeline']


def test_unknown_or_incomplete_identity_is_not_success():
    data=fixture();data.update(record_observed=False,record_type=None,timeline=[])
    r=case.read(REF,lambda *_:data);assert r['verification_navigation'] is None and not r['business_outcome_verified']
    data=fixture();data['record_identity_complete_within_retained_window']=False
    assert case.read(REF,lambda *_:data)['verification_navigation'] is None


@pytest.mark.parametrize('bad',[1,True,{},'native-id','owg:r:ABC','owg:r:'+'a'*33])
def test_invalid_reference_refused_without_read(bad):
    def forbidden(*_):pytest.fail('must not read')
    with pytest.raises(ValueError):case.read(bad,forbidden)


def test_omitted_reference_no_read():
    for value in (None,''):
        assert case.read(value,lambda *_:pytest.fail('must not read')) is None


@pytest.mark.parametrize('change',['wrong_ref','provider_claim','customer_claim','permission','timeline','unknown_record','oversize','missing_status'])
def test_refuses_unexpected_or_oversized_response(change):
    data=fixture()
    if change=='wrong_ref': data['record_ref']='owg:r:'+'b'*32
    elif change=='provider_claim':data['source_reported_status']['availability']='source_reported'
    elif change=='customer_claim':data['source_reported_status']['end_customer_outcome_verified']=True
    elif change=='permission':data['authorization']['may_issue_refund']=True
    elif change=='timeline':data['timeline']*=101
    elif change=='unknown_record':data['record_observed']=False
    elif change=='oversize':data['timeline'][0]['extra']='x'*case.MAX_BYTES
    elif change=='missing_status':del data['source_reported_status']['verified_at']
    with pytest.raises(ValueError):case.read(REF,lambda *_:data)
