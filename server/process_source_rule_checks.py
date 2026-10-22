"""Explicit current-source comparisons, never historical-case or execution authority."""
from collections import Counter
from datetime import datetime,timezone
import re

from pydantic import BaseModel,ConfigDict,Field,model_validator

from shared.process_model import ProcessModel
from shared.process_rule_checks import RuleCase,check_case_rules
from shared.process_rule_conditions import evaluate_predicate,combine_condition_statuses
from .record_graph import record_graph
from .local_reference_lookup import resolve_resource_reference
from .source_http_logging import private_source_http_logs

_TYPES={
    'github_pull_request':('github','pull_request','github.com',{'open','closed_unmerged','merged'},
        'explicit_opt_in_authenticated_local_github_cli_read'),
    'jira_issue':('jira','issue',None,{'new','indeterminate','done'},
        'explicit_opt_in_site_verified_oauth_jira_issue_status_read'),
    'stripe_invoice':('stripe','invoice','dashboard.stripe.com',{'draft','open','paid','uncollectible','void'},
        'opted_in_restricted_key_stripe_account_and_record_get'),
    'stripe_refund':('stripe','refund','dashboard.stripe.com',{'pending','requires_action','succeeded','failed','canceled'},
        'opted_in_restricted_key_stripe_account_and_record_get'),
}
_PATHS=frozenset({'/source/provider','/source/record_kind','/source/state'})


class SourceRuleCheck(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    case_id:str=Field(default='case_1',pattern=r'^case_[1-9][0-9]{0,3}$')
    resource_ref:str=Field(pattern=r'^owg:r:[0-9a-f]{32}$')
    source_type:str
    activity_ids:list[str] | None=Field(default=None,min_length=1,max_length=80)

    @model_validator(mode='after')
    def supported_selection(self):
        if self.source_type not in _TYPES:raise ValueError('Choose a supported source record type')
        RuleCase(case_id=self.case_id,facts={},activity_ids=self.activity_ids)
        return self


def _source_facts(graph,selection,now):
    """Accept only a complete observed identity and the known adapter receipt."""
    provider,kind,_host,states,method=_TYPES[selection.source_type]
    if (not isinstance(graph,dict) or graph.get('resource_ref')!=selection.resource_ref
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


def check_current_source_rules(record,selection,*,record_reader=record_graph,
                               resolver=resolve_resource_reference,now=None):
    model=ProcessModel.model_validate(record['process_model'])
    result=check_case_rules(record,[RuleCase(case_id=selection.case_id,facts={},activity_ids=selection.activity_ids)])
    row=result['cases'][0];by_id={rule.id:rule for rule in model.business_rules}
    evaluated={'matched','not_matched','unknown'}
    eligible=any(item['status'] in evaluated and any(condition.path in _PATHS
        for condition in by_id[item['rule_id']].predicate.conditions) for item in row['rules'])
    facts={};verified_at=None;refresh_requested=False;reason='no_confirmed_source_conditions_on_proposed_selection'
    if eligible:
        provider,kind,host,_states,_method=_TYPES[selection.source_type]
        if host is None:
            # Never accept a tenant/URL from the browser. Pin the canonical
            # observed host to the same local Jira locator used by its adapter.
            try:info=resolver(selection.resource_ref)
            except Exception:info=None
            if (isinstance(info,dict) and info.get('provider')==provider and info.get('resource_kind')==kind
                and isinstance(info.get('host'),str) and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net',info['host'])):
                host=info['host']
        if host is None:reason='approved_local_source_locator_unavailable'
        else:
            refresh_requested=True
            try:
                with private_source_http_logs():
                    graph=record_reader(selection.resource_ref,include_source_status=True,
                        required_source_type=(provider,kind,host))
            except Exception:
                # An adapter failure must not echo native IDs, URL/token details
                # or turn missing status into a successful absence predicate.
                graph=None
            facts,verified_at,reason=_source_facts(graph,selection,now or datetime.now(timezone.utc))
    counts=Counter()
    for item in row['rules']:
        if item['status'] in evaluated:
            predicate=by_id[item['rule_id']].predicate;conditions=[]
            for condition in predicate.conditions:
                if condition.path not in _PATHS:
                    conditions.append({'status':'unknown','reason':'source_fact_path_not_supported'})
                elif not facts:
                    conditions.append({'status':'unknown','reason':'authoritative_source_fact_unavailable'})
                else:
                    conditions.append(evaluate_predicate({'match':'all','conditions':[condition.model_dump()]},facts)['conditions'][0])
            item['conditions']=conditions
            item['status']=combine_condition_statuses([entry['status'] for entry in conditions],predicate.match)
        counts[item['status']]+=1
    row['counts']=dict(counts);result['rule_result_counts']=dict(counts)
    result.pop('source_reads_performed')
    result.update(format='openworkgraph.current-source-rule-check.v1',
        case_association_basis='caller_selected_record_and_optional_proposed_route',
        selected_record_reference=selection.resource_ref,selected_source_type=selection.source_type,
        facts_source_authoritativeness_verified=bool(facts),source_state_verified_at=verified_at,
        source_state_scope='authenticated_provider_current_record_state_only',source_status_reason=reason,
        source_status_refresh_requested=refresh_requested,outbound_source_reads_may_have_occurred=refresh_requested,
        case_association_verified=False,historical_case_facts_verified=False,
        generated_agent_source_fact_guard_resolved=False)
    return result
