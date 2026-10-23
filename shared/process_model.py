"""Bounded, portable process graphs. Reviewed designs never grant execution rights."""
from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator, model_serializer

from .process_opportunities import OpportunityScenario
from .process_rule_conditions import RulePredicate

Identifier = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')]
ActivityLabel = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9 _-]{0,63}$')]


class StructuralModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Activity(StructuralModel):
    id: Identifier
    label: ActivityLabel
    kind: Literal['activity', 'decision', 'routing', 'start', 'end', 'parallel_split', 'parallel_join'] = 'activity'
    implementation: Literal['unmapped', 'api', 'deterministic', 'ai_agent', 'computer_use', 'human'] = 'unmapped'
    responsibility_label: str = Field(default='', max_length=120)

    @field_validator('responsibility_label')
    @classmethod
    def responsibility_is_single_line(cls, value):
        value=value.strip()
        if any(ord(char)<32 or 127<=ord(char)<=159 or 0xD800<=ord(char)<=0xDFFF
               or ord(char) in {0xFFFE,0xFFFF,0x2028,0x2029} for char in value):
            raise ValueError('Responsibility must be a short single-line label')
        return value

    @model_serializer(mode='wrap')
    def preserve_unspecified_responsibility(self, handler):
        result=handler(self)
        # Optional descriptive metadata must not silently change the digest of
        # existing reviewed models, exported specs or implementation gates.
        if not self.responsibility_label:
            result.pop('responsibility_label',None)
        return result


class BusinessRule(StructuralModel):
    id: Identifier
    text: str = Field(min_length=1, max_length=2000)
    user_confirmed: StrictBool = False
    predicate:RulePredicate | None = None

    @model_serializer(mode='wrap')
    def preserve_unstructured_rule_hashes(self,handler):
        result=handler(self)
        if self.predicate is None:result.pop('predicate',None)
        return result

    @field_validator('text')
    @classmethod
    def nonblank_rule(cls, value):
        if not value.strip():
            raise ValueError('Rule text must not be blank')
        return value


class Transition(StructuralModel):
    source: Identifier
    target: Identifier
    rule_id: Identifier | None = None


class OutcomeExpectation(StructuralModel):
    terminal_activity_id: Identifier
    operation: Literal['github_pr_merged', 'stripe_invoice_paid', 'stripe_refund_processor_succeeded', 'jira_issue_resolved']
    user_confirmed: StrictBool = False


class ManualReviewCondition(StructuralModel):
    activity_id: Identifier
    kind: Literal['decision_point', 'exception_condition']
    condition_label: ActivityLabel
    response: Literal['pause_for_review', 'request_source_recheck', 'escalate_to_owner']
    source: Literal['user_declared_unverified'] = 'user_declared_unverified'
    evaluated_by_owg: Literal[False] = False
    automatic_branch_authorized: Literal[False] = False
    executable: Literal[False] = False


class ProcessModel(StructuralModel):
    format: Literal['openworkgraph.process-model.v1'] = 'openworkgraph.process-model.v1'
    goal: str = Field(min_length=3, max_length=500)
    owner_label: str = Field(default='', max_length=120)
    baseline_path: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(min_length=1, max_length=40)
    baseline_source: Literal['selected_csv_variant', 'selected_observed_variant', 'selected_source_audit', 'user_declared']
    baseline_coverage: Literal['complete_supplied_path', 'bounded_candidate_sequence', 'partial_source_audit', 'user_declared_design'] = 'complete_supplied_path'
    baseline_support_unit: Literal['declared_cases', 'candidate_executions'] = 'declared_cases'
    supporting_case_count: int = Field(default=0, ge=0, le=1000000)
    activities: list[Activity] = Field(min_length=1, max_length=40)
    transitions: list[Transition] = Field(default_factory=list, max_length=100)
    business_rules: list[BusinessRule] = Field(default_factory=list, max_length=40)
    entry_activity_id: Identifier
    terminal_activity_ids: list[Identifier] = Field(min_length=1, max_length=40)
    outcome_expectations: list[OutcomeExpectation] = Field(default_factory=list, max_length=40)
    manual_review_conditions: list[ManualReviewCondition] = Field(default_factory=list, max_length=12)
    opportunity_scenario: OpportunityScenario | None = None
    # These fields are deliberately immutable semantic assertions in every model.
    observed_path_is_policy: Literal[False] = False
    execution_authorized: Literal[False] = False
    outcomes_verified: Literal[False] = False

    @field_validator('goal')
    @classmethod
    def nonblank_goal(cls, value):
        if len(value.strip()) < 3:
            raise ValueError('Business goal must contain at least three non-padding characters')
        return value

    @field_validator('baseline_path')
    @classmethod
    def nonblank_baseline(cls, value):
        if any(not label.strip() for label in value):
            raise ValueError('Baseline labels must not be blank')
        return value

    @model_validator(mode='after')
    def graph_integrity(self):
        ids = [a.id for a in self.activities]
        rules = [r.id for r in self.business_rules]
        if len(set(ids)) != len(ids) or len(set(rules)) != len(rules):
            raise ValueError('Activity and rule IDs must be unique')
        known = set(ids)
        conditions = [(c.activity_id, c.kind, c.condition_label.casefold()) for c in self.manual_review_conditions]
        if len(set(conditions)) != len(conditions) or any(c[0] not in known for c in conditions):
            raise ValueError('Manual review conditions must be unique and reference existing activities')
        if self.entry_activity_id not in known or not set(self.terminal_activity_ids) <= known:
            raise ValueError('Entry and terminal activities must exist')
        if len(set(self.terminal_activity_ids)) != len(self.terminal_activity_ids):
            raise ValueError('Terminal IDs must be unique')
        expected = [(e.terminal_activity_id, e.operation) for e in self.outcome_expectations]
        if len(set(expected)) != len(expected) or any(e[0] not in self.terminal_activity_ids for e in expected):
            raise ValueError('Outcome expectations must be unique and reference terminal activities')
        edges = set()
        outgoing = defaultdict(list)
        incoming = defaultdict(list)
        for edge in self.transitions:
            if edge.source not in known or edge.target not in known or (edge.rule_id and edge.rule_id not in rules):
                raise ValueError('Transitions must reference existing activities and rules')
            pair = (edge.source, edge.target)
            if pair in edges:
                raise ValueError('Duplicate transitions are ambiguous')
            edges.add(pair)
            outgoing[edge.source].append(edge.target)
            incoming[edge.target].append(edge.source)
        if any(outgoing[t] for t in self.terminal_activity_ids):
            raise ValueError('Terminal activities cannot have outgoing transitions')
        for activity in self.activities:
            if activity.id not in self.terminal_activity_ids and not outgoing[activity.id]:
                raise ValueError('Nonterminal activities must have an outgoing transition')
            if activity.kind == 'decision':
                branches = [e for e in self.transitions if e.source == activity.id]
                if len(branches) < 2 or any(not e.rule_id for e in branches):
                    raise ValueError('Decision branches require at least two explicitly referenced rules')
            elif activity.kind != 'parallel_split' and len(outgoing[activity.id]) > 1:
                raise ValueError('Branching activities must be marked as decisions')
            if activity.kind == 'routing' and (activity.id in self.terminal_activity_ids or len(outgoing[activity.id]) != 1):
                raise ValueError('Routing nodes must have exactly one outgoing transition and cannot be terminal')
            if activity.kind == 'end' and activity.id not in self.terminal_activity_ids:
                raise ValueError('End events must be terminal activities')
            if activity.kind == 'start' and (activity.id != self.entry_activity_id or incoming[activity.id]):
                raise ValueError('Start events must be the entry activity and have no incoming transitions')
        def reachable(starts, adjacency):
            seen = set(starts)
            pending = list(starts)
            while pending:
                for node in adjacency[pending.pop()]:
                    if node not in seen:
                        seen.add(node)
                        pending.append(node)
            return seen
        if reachable([self.entry_activity_id], outgoing) != known:
            raise ValueError('Every activity must be reachable from the entry')
        if reachable(self.terminal_activity_ids, incoming) != known:
            raise ValueError('Every activity must have a path to a terminal')
        if any(node.kind in {'parallel_split','parallel_join'} for node in self.activities):
            from .process_parallel import parallel_regions
            parallel_regions(self)
        return self

    def linear_path(self) -> list[str] | None:
        """Only return a path when no loop, decision or branch would be lost."""
        outgoing = {edge.source: edge.target for edge in self.transitions}
        if any(a.kind in {'decision','parallel_split','parallel_join'} for a in self.activities):
            return None
        by_id = {a.id: a.label for a in self.activities}
        path, seen = [], set()
        node = self.entry_activity_id
        while node not in seen:
            seen.add(node)
            path.append(by_id[node])
            if node in self.terminal_activity_ids:
                return path if len(seen) == len(by_id) else None
            node = outgoing[node]
        return None
