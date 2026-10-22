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

from shared.process_source_state import SOURCE_TYPES as _TYPES,SOURCE_PATHS as _PATHS,facts_from_graph,state_receipt


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


def read_current_source_state(selection,*,record_reader=record_graph,resolver=resolve_resource_reference,now=None,evidence_window=None):
    provider,kind,host,_states,_method=_TYPES[selection.source_type]
    if host is None:
        try:info=resolver(selection.resource_ref)
        except Exception:info=None
        if (isinstance(info,dict) and info.get('provider')==provider and info.get('resource_kind')==kind
            and isinstance(info.get('host'),str) and re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net',info['host'])):
            host=info['host']
    if host is None:
        return {},None,'approved_local_source_locator_unavailable',False
    try:
        with private_source_http_logs():
            graph=record_reader(selection.resource_ref,include_source_status=True,
                required_source_type=(provider,kind,host),**(evidence_window or {}))
    except Exception:
        graph=None
    facts,stamp,reason=facts_from_graph(graph,selection.resource_ref,selection.source_type,now or datetime.now(timezone.utc))
    return facts,stamp,reason,True


def current_source_state_receipt(selection,**kwargs):
    facts,stamp,reason,requested=read_current_source_state(selection,**kwargs)
    return state_receipt(selection.resource_ref,selection.source_type,facts,stamp,reason,requested)


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
        facts,verified_at,reason,refresh_requested=read_current_source_state(selection,
            record_reader=record_reader,resolver=resolver,now=now)
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
