"""Read-only checks of confirmed predicates against caller-declared case facts."""
from collections import Counter
from typing import Any

from pydantic import BaseModel,ConfigDict,Field,model_validator

from .process_model import ProcessModel
from .process_rule_conditions import evaluate_predicate,validate_facts
from .process_agent_project import process_model_digest


class RuleCase(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    case_id:str=Field(pattern=r'^case_[1-9][0-9]{0,3}$')
    facts:dict[str,Any]
    activity_ids:list[str] | None=Field(default=None,min_length=1,max_length=80)

    @model_validator(mode='after')
    def bounded_facts(self):
        validate_facts(self.facts)
        if self.activity_ids and any(not isinstance(identifier,str) or len(identifier)>64 for identifier in self.activity_ids):
            raise ValueError('Use bounded model activity IDs')
        return self


class RuleCaseBatch(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    cases:list[RuleCase]=Field(min_length=1,max_length=50)

    @model_validator(mode='after')
    def distinct_cases(self):
        if len({case.case_id for case in self.cases})!=len(self.cases):raise ValueError('Case labels must be unique')
        return self


def check_case_rules(record,cases):
    model=ProcessModel.model_validate(record['process_model'])
    nodes={activity.id for activity in model.activities}
    edges={(edge.source,edge.target):edge for edge in model.transitions}
    rows=[];total=Counter()
    for case in cases:
        route=case.activity_ids;selected=None;route_valid=None
        if route is not None:
            route_valid=(all(identifier in nodes for identifier in route) and route[0]==model.entry_activity_id
                and route[-1] in model.terminal_activity_ids and all(pair in edges for pair in zip(route,route[1:])))
            selected={edges[pair].rule_id for pair in zip(route,route[1:]) if edges[pair].rule_id} if route_valid else set()
        rules=[];counts=Counter()
        for rule in model.business_rules:
            if route is not None and not route_valid:status='not_evaluated_invalid_route';result=None
            elif selected is not None and rule.id not in selected:status='not_on_proposed_route';result=None
            elif not rule.user_confirmed:status='not_evaluated_unconfirmed';result=None
            elif rule.predicate is None:status='not_evaluated_unstructured';result=None
            else:
                result=evaluate_predicate(rule.predicate,case.facts);status=result['status']
            counts[status]+=1;total[status]+=1
            rules.append({'rule_id':rule.id,'status':status,
                          'conditions':result['conditions'] if result else []})
        rows.append({'case_id':case.case_id,'proposed_route_supplied':route is not None,
                     'proposed_route_structure_matches':route_valid,
                     'manual_review_condition_count_on_proposed_route':sum(condition.activity_id in route
                        for condition in model.manual_review_conditions) if route_valid else None,
                     'manual_review_conditions_evaluated':False,'counts':dict(counts),'rules':rules})
    return {'format':'openworkgraph.declared-rule-case-check.v1',
        'process_reference':{key:record[key] for key in ('workflow_id','revision','content_sha256')},
        'process_model_sha256':process_model_digest(model.model_dump()),
        'case_count':len(rows),'cases':rows,'rule_result_counts':dict(total),
        'declared_manual_review_condition_count':len(model.manual_review_conditions),
        'case_association_basis':'caller_supplied_opaque_labels_and_optional_proposed_routes',
        'facts_source_authoritativeness_verified':False,'business_policy_compliance_verified':False,
        'observed_execution_conformance_verified':False,'independent_outcomes_verified':False,
        'facts_retained':False,'comparison_report_retained':False,'process_model_updated':False,
        'source_reads_performed':False,'model_provider_calls_made':False,'execution_permissions_granted':False}
