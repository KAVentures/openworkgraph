"""Read-only checks of confirmed predicates against caller-declared case facts."""
from collections import Counter
from typing import Any

from pydantic import BaseModel,ConfigDict,Field,model_validator,field_validator

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
    as_of_date:str | None = None

    @field_validator('as_of_date')
    @classmethod
    def explicit_date(cls,value):
        from .process_decision_tables import validate_date
        return validate_date(value) if value is not None else None

    cases:list[RuleCase]=Field(min_length=1,max_length=50)

    @model_validator(mode='after')
    def distinct_cases(self):
        if len({case.case_id for case in self.cases})!=len(self.cases):raise ValueError('Case labels must be unique')
        return self


def check_case_rules(record,cases,as_of_date=None):
    model=ProcessModel.model_validate(record['process_model'])
    nodes={activity.id for activity in model.activities}
    edges={(edge.source,edge.target):edge for edge in model.transitions}
    rows=[];total=Counter()
    # Test evidence is a supplied finite case batch, never a proof of complete
    # business-rule coverage. Track the *chosen* branches independently from
    # individual row matches (which may occur in ambiguous UNIQUE tables).
    table_coverage={
        table.id: {
            "table_id":table.id, "hit_policy":table.hit_policy,
            "decision_activity_id":table.decision_activity_id,
            "declared_rows":[{"row_id":r.id,"rule_id":r.rule_id,
                              "target_activity_id":r.target_activity_id} for r in table.rows],
            "status_counts":Counter(), "matched_row_counts":Counter(),
            "selected_row_counts":Counter(),
            "evaluated_case_count":0, "cases_checked":0,
        } for table in model.decision_tables
    }
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
        tables=[]
        if model.decision_tables:
            from .process_decision_tables import evaluate_decisions
            for table,check in zip(model.decision_tables,evaluate_decisions(model,case.facts,as_of_date)):
                if route is not None and (not route_valid or table.decision_activity_id not in route):
                    check.update(status='not_evaluated_invalid_route' if not route_valid else 'not_on_proposed_route',
                                 rows=[],selected_target_activity_id=None)
                    if table.hit_policy=='FIRST':check.update(selected_row_id=None,blocking_row_id=None)
                elif route_valid and check['selected_target_activity_id'] is not None:
                    # A looping route may visit a decision repeatedly: all chosen
                    # targets must agree with the same caller-supplied case facts.
                    targets=[b for a,b in zip(route,route[1:]) if a==table.decision_activity_id]
                    check['proposed_branch_agrees']=all(t==check['selected_target_activity_id'] for t in targets)
                summary=table_coverage[table.id]
                summary["cases_checked"]+=1
                summary["status_counts"][check["status"]]+=1
                if check.get("rows"):
                    summary["evaluated_case_count"]+=1
                    for tested in check["rows"]:
                        if tested["status"]=="matched":
                            summary["matched_row_counts"][tested["row_id"]]+=1
                if check["status"] in {"first_match","unique_match"}:
                    target=check.get("selected_target_activity_id")
                    selected_row=next((row.id for row in table.rows
                                       if row.target_activity_id==target),None)
                    if selected_row is not None:
                        summary["selected_row_counts"][selected_row]+=1
                tables.append(check)
        rows.append({'case_id':case.case_id,'proposed_route_supplied':route is not None,
                     'proposed_route_structure_matches':route_valid,
                     'manual_review_condition_count_on_proposed_route':sum(condition.activity_id in route
                        for condition in model.manual_review_conditions) if route_valid else None,
                     'manual_review_conditions_evaluated':False,'counts':dict(counts),'rules':rules,**({'decision_tables':tables} if model.decision_tables else {})})
    table_summaries=[]
    for table in model.decision_tables:
        values=table_coverage[table.id]
        selected=values["selected_row_counts"]
        declared_ids=[row.id for row in table.rows]
        table_summaries.append({
            "table_id":table.id, "decision_activity_id":table.decision_activity_id,
            "hit_policy":table.hit_policy,
            "cases_checked":values["cases_checked"],
            "evaluated_case_count":values["evaluated_case_count"],
            "not_evaluated_case_count":values["cases_checked"]-values["evaluated_case_count"],
            "status_counts":dict(sorted(values["status_counts"].items())),
            "selected_case_count":sum(selected.values()),
            "selected_row_count":sum(bool(selected.get(row_id)) for row_id in declared_ids),
            "declared_row_count":len(declared_ids),
            "never_selected_row_ids":[row_id for row_id in declared_ids if not selected.get(row_id)],
            "rows":[{"row_id":row.id,"rule_id":row.rule_id,
                     "target_activity_id":row.target_activity_id,
                     "selected_cases":selected.get(row.id,0),
                     "matched_cases_even_if_ambiguous":values["matched_row_counts"].get(row.id,0)}
                    for row in table.rows],
            "coverage_basis":"caller_supplied_case_facts_only",
            "exhaustive_input_domain_coverage_verified":False,
            "source_facts_authoritativeness_verified":False,
            "execution_authorized":False,
        })
    return {'format':'openworkgraph.declared-rule-case-check.v1',
        'process_reference':{key:record[key] for key in ('workflow_id','revision','content_sha256')},
        'process_model_sha256':process_model_digest(model.model_dump()),
        **({'decision_table_as_of_date':as_of_date,'decision_table_coverage':'supplied_cases_only_not_exhaustive'} if model.decision_tables else {}),
        'case_count':len(rows),'cases':rows,'rule_result_counts':dict(total),
        **({'decision_table_test_coverage':table_summaries,
            'decision_table_test_coverage_basis':'caller_supplied_cases_only_not_exhaustive'}
           if model.decision_tables else {}),
        'declared_manual_review_condition_count':len(model.manual_review_conditions),
        'case_association_basis':'caller_supplied_opaque_labels_and_optional_proposed_routes',
        'facts_source_authoritativeness_verified':False,'business_policy_compliance_verified':False,
        'observed_execution_conformance_verified':False,'independent_outcomes_verified':False,
        'facts_retained':False,'comparison_report_retained':False,'process_model_updated':False,
        'source_reads_performed':False,'model_provider_calls_made':False,'execution_permissions_granted':False}
