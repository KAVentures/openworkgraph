"""Minimized authenticated current-state contract; no case or execution authority."""
from datetime import datetime,timezone

SOURCE_TYPES={
    'github_pull_request':('github','pull_request','github.com',{'open','closed_unmerged','merged'},
        'explicit_opt_in_authenticated_local_github_cli_read'),
    'jira_issue':('jira','issue',None,{'new','indeterminate','done'},
        'explicit_opt_in_site_verified_oauth_jira_issue_status_read'),
    'stripe_invoice':('stripe','invoice','dashboard.stripe.com',{'draft','open','paid','uncollectible','void'},
        'opted_in_restricted_key_stripe_account_and_record_get'),
    'stripe_refund':('stripe','refund','dashboard.stripe.com',{'pending','requires_action','succeeded','failed','canceled'},
        'opted_in_restricted_key_stripe_account_and_record_get'),
}
SOURCE_PATHS=frozenset({'/source/provider','/source/record_kind','/source/state'})


def facts_from_graph(graph,resource_ref,source_type,now):
    """Accept only a complete observed identity and the known adapter receipt."""
    provider,kind,_host,states,method=SOURCE_TYPES[source_type]
    if (not isinstance(graph,dict) or graph.get('resource_ref')!=resource_ref
        or graph.get('record_observed') is not True
        or graph.get('record_type')!={'provider':provider,'kind':kind}
        or graph.get('record_identity_complete_within_retained_window') is not True
        or graph.get('source_event_limit_reached') is not False
        or graph.get('source_rows_scoped_to_exact_record') is not True):
        return {},None,'observed_record_identity_unavailable'
    source=graph.get('source_evidence')
    if (not isinstance(source,dict) or source.get('availability')!='source_reported'
        or source.get('source_state_reported') is not True or source.get('provider')!=provider
        or source.get('verification_method')!=method
        or ('object_type' in source and source['object_type']!=kind)):
        return {},None,'authenticated_source_state_unavailable'
    state=source.get('source_state')
    if not isinstance(state,str) or state not in states:return {},None,'unsupported_source_state'
    try:
        stamp=datetime.fromisoformat(str(source.get('verified_at') or '').replace('Z','+00:00'))
        if stamp.tzinfo is None or now.tzinfo is None:raise ValueError()
        age=(now-stamp).total_seconds()
        if age < -5 or age > 120:raise ValueError()
    except (ValueError,TypeError,OverflowError):return {},None,'source_state_not_fresh'
    return {'source':{'provider':provider,'record_kind':kind,'state':state}},stamp.astimezone(timezone.utc).isoformat(),'source_state_reported'


_FALSE_FLAGS=('case_association_verified','historical_case_facts_verified',
    'business_policy_compliance_verified','business_outcomes_verified','agent_execution_authorized')


def state_receipt(resource_ref,source_type,facts,verified_at,reason,refresh_requested):
    result={'format':'openworkgraph.source-state-receipt.v1','resource_ref':resource_ref,
        'source_type':source_type,'source_state_verified':bool(facts),'verified_at':verified_at,
        'reason':reason,'read_only':True,
        'verification_scope':'authenticated_provider_current_record_state',
        'source_status_refresh_requested':refresh_requested,
        **{flag:False for flag in _FALSE_FLAGS}}
    if facts:
        row=facts['source']
        result.update(provider=row['provider'],provider_record_kind=row['record_kind'],provider_reported_state=row['state'])
    return result


def facts_from_receipt(receipt,resource_ref,source_type,*,now=None):
    """Recheck identity, provenance, vocabulary and freshness at the consumer."""
    if (not isinstance(receipt,dict) or source_type not in SOURCE_TYPES
        or receipt.get('format')!='openworkgraph.source-state-receipt.v1'
        or receipt.get('resource_ref')!=resource_ref or receipt.get('source_type')!=source_type
        or receipt.get('source_state_verified') is not True or receipt.get('read_only') is not True
        or receipt.get('source_status_refresh_requested') is not True or receipt.get('reason')!='source_state_reported'
        or receipt.get('verification_scope')!='authenticated_provider_current_record_state'
        or any(receipt.get(flag) is not False for flag in _FALSE_FLAGS)):
        return {}
    provider,kind,_host,_states,method=SOURCE_TYPES[source_type]
    # Feed the same strict adapter contract used by the local verifier. This
    # validates a trusted verifier response; it is not a signed attestation.
    graph={'resource_ref':resource_ref,'record_observed':True,
        'record_type':{'provider':receipt.get('provider'),'kind':receipt.get('provider_record_kind')},
        'record_identity_complete_within_retained_window':True,'source_event_limit_reached':False,
        'source_rows_scoped_to_exact_record':True,'source_evidence':{
            'availability':'source_reported','source_state_reported':True,'provider':receipt.get('provider'),
            'object_type':receipt.get('provider_record_kind'),'source_state':receipt.get('provider_reported_state'),
            'verified_at':receipt.get('verified_at'),'verification_method':method}}
    facts,_stamp,_reason=facts_from_graph(graph,resource_ref,source_type,now or datetime.now(timezone.utc))
    return facts
