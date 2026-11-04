"""Bounded UNIQUE/ordered FIRST declarations. No FEEL, source access or execution."""
from datetime import date
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_serializer, model_validator

ID = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')]
UNIQUE_RUNTIME_CONTRACT='openworkgraph.current-source-unique-decision.v1'
ORDERED_RUNTIME_CONTRACT='openworkgraph.current-source-ordered-decision.v1'


def runtime_contract(model):
    return ORDERED_RUNTIME_CONTRACT if any(t.hit_policy=='FIRST' for t in model.decision_tables) else UNIQUE_RUNTIME_CONTRACT


def validate_date(value):
    if type(value) is not str or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('Use an explicit calendar date YYYY-MM-DD')
    date.fromisoformat(value)
    return value


class Declaration(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SourceBasis(Declaration):
    label: str = Field(min_length=1, max_length=120)
    document_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    basis: Literal['human_declared_document_digest'] = 'human_declared_document_digest'
    independently_verified: Literal[False] = False

    @field_validator('label')
    @classmethod
    def single_line(cls, value):
        if not value.strip() or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF for c in value):
            raise ValueError('Use a nonblank single-line source label')
        return value.strip()


class DecisionRow(Declaration):
    id: ID
    rule_id: ID
    target_activity_id: ID


class RequiredDecision(Declaration):
    """Another table's selected next step, supplied to this table as a fact (DMN requiredDecision)."""
    table_id: ID
    fact_path: str = Field(pattern=r'^(/[A-Za-z_][A-Za-z0-9_]{0,63}){1,8}$')


class DecisionTable(Declaration):
    id: ID
    decision_activity_id: ID
    hit_policy: Literal['UNIQUE','FIRST'] = 'UNIQUE'
    effective_from: str
    effective_until: str | None = None
    source_basis: SourceBasis
    rows: list[DecisionRow] = Field(min_length=2, max_length=40)
    required_decisions: list[RequiredDecision] = Field(default_factory=list, max_length=8)
    user_confirmed: StrictBool = False
    execution_authorized: Literal[False] = False

    @model_serializer(mode='wrap')
    def preserve_tables_without_dependencies(self, handler):
        result = handler(self)
        if not self.required_decisions:
            result.pop('required_decisions', None)
        return result

    @field_validator('effective_from', 'effective_until')
    @classmethod
    def dates(cls, value):
        return validate_date(value) if value is not None else None

    @model_validator(mode='after')
    def distinct_rows(self):
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError('Effective end is exclusive and must follow the start')
        if any(len({getattr(row, key) for row in self.rows}) != len(self.rows)
               for key in ('id', 'rule_id', 'target_activity_id')):
            raise ValueError('Decision row IDs, rules and targets must each be unique')
        paths = [r.fact_path for r in self.required_decisions]
        if (len({r.table_id for r in self.required_decisions}) != len(paths)
                or any(b.startswith(a + '/') for a in paths for b in paths)
                or len(set(paths)) != len(paths) or self.id in {r.table_id for r in self.required_decisions}):
            raise ValueError('Required decisions must name distinct other tables at distinct, non-nested fact paths')
        return self


def validate_tables(model):
    tables = model.decision_tables
    if len({t.id for t in tables}) != len(tables) or len({t.decision_activity_id for t in tables}) != len(tables):
        raise ValueError('Each decision has at most one uniquely identified table')
    activities = {a.id: a for a in model.activities}
    rules = {r.id: r for r in model.business_rules}
    for table in tables:
        node = activities.get(table.decision_activity_id)
        if node is None or node.kind != 'decision':
            raise ValueError('Decision tables must reference a decision activity')
        branches = {(e.rule_id, e.target) for e in model.transitions if e.source == node.id}
        if branches != {(r.rule_id, r.target_activity_id) for r in table.rows}:
            raise ValueError('Decision rows must cover each declared outgoing branch exactly once')
        if any(r.rule_id not in rules or rules[r.rule_id].predicate is None for r in table.rows):
            raise ValueError('Every decision row requires an existing structured rule')
        if table.user_confirmed and any(not rules[r.rule_id].user_confirmed for r in table.rows):
            raise ValueError('Confirm each referenced rule before confirming its decision table')
    decision_order(tables)


def decision_order(tables):
    """Tables in dependency order; refuses unknown or cyclic requirements."""
    by_id = {t.id: t for t in tables}
    if any(r.table_id not in by_id for t in tables for r in t.required_decisions):
        raise ValueError('Required decisions must reference decision tables in this design')
    order, state = [], {}
    def visit(table, depth=0):
        if state.get(table.id) == 'done':
            return
        if state.get(table.id) == 'active' or depth > len(tables):
            raise ValueError('Decision dependencies must not form a cycle')
        state[table.id] = 'active'
        for required in table.required_decisions:
            visit(by_id[required.table_id], depth + 1)
        state[table.id] = 'done'
        order.append(table)
    for table in tables:
        visit(table)
    return order


def _with_decision_facts(facts, table, selected):
    """Copy of facts with each required table's selected step at its declared path."""
    import copy
    from .process_rule_conditions import _lookup
    combined = copy.deepcopy(facts)
    for required in table.required_decisions:
        if _lookup(facts, required.fact_path)[0]:
            raise ValueError('Supplied case facts must not contain a required decision path')
        node = combined
        segments = required.fact_path.split('/')[1:]
        for segment in segments[:-1]:
            child = node.get(segment) if type(node) is dict else None
            if child is None:
                child = node[segment] = {}
            elif type(child) is not dict:
                raise ValueError('Supplied case facts must not contain a required decision path')
            node = child
        node[segments[-1]] = selected[required.table_id]
    return combined


def evaluate_decisions(model, facts, as_of_date=None):
    """Evaluate every table in dependency order.

    A table whose required decision did not select a step reports
    ``dependency_unresolved`` and names the blocking table; it is never guessed.
    """
    from .process_rule_conditions import validate_facts
    validate_facts(facts)
    rules = {r.id: r for r in model.business_rules}
    selected, results = {}, {}
    for table in decision_order(model.decision_tables):
        blocking = next((r.table_id for r in table.required_decisions if selected.get(r.table_id) is None), None)
        if blocking is not None:
            check = evaluate_table(table, rules, facts, as_of_date)
            if check['status'] not in {'unconfirmed', 'date_required', 'not_yet_effective', 'expired'}:
                check.update(status='dependency_unresolved', rows=[], selected_target_activity_id=None)
                if table.hit_policy == 'FIRST':
                    check.update(selected_row_id=None, blocking_row_id=None)
            check['blocking_decision_table_id'] = blocking
        else:
            try:
                combined = _with_decision_facts(facts, table, selected)
            except ValueError:
                combined = None
            check = evaluate_table(table, rules, combined if combined is not None else facts, as_of_date)
            if combined is None and check['status'] not in {'unconfirmed', 'date_required', 'not_yet_effective', 'expired'}:
                check.update(status='dependency_fact_conflict', rows=[], selected_target_activity_id=None)
                if table.hit_policy == 'FIRST':
                    check.update(selected_row_id=None, blocking_row_id=None)
        if table.required_decisions:
            check['required_decision_table_ids'] = [r.table_id for r in table.required_decisions]
        selected[table.id] = check['selected_target_activity_id']
        results[table.id] = check
    return [results[t.id] for t in model.decision_tables]


def evaluate_table(table, rules, facts, as_of_date=None):
    """Return codes/IDs only; UNKNOWN withholds UNIQUE or an unresolved FIRST prefix."""
    from .process_rule_conditions import evaluate_predicate, validate_facts
    validate_facts(facts)
    result = {'table_id': table.id, 'status': 'unconfirmed', 'selected_target_activity_id': None,
              'rows': [], 'hit_policy': table.hit_policy, 'facts_authoritativeness_verified': False,
              'business_policy_compliance_verified': False, 'execution_authorized': False}
    if table.hit_policy=='FIRST':
        result.update(selected_row_id=None,blocking_row_id=None,row_precedence='declared_row_order')
    if not table.user_confirmed or any(not rules[r.rule_id].user_confirmed for r in table.rows):
        return result
    if as_of_date is None:
        result['status'] = 'date_required'
        return result
    validate_date(as_of_date)
    if as_of_date < table.effective_from:
        result['status'] = 'not_yet_effective'
        return result
    if table.effective_until is not None and as_of_date >= table.effective_until:
        result['status'] = 'expired'
        return result
    states = []
    for row in table.rows:
        rule = rules[row.rule_id]
        status = evaluate_predicate(rule.predicate, facts)['status'] if rule.predicate is not None else 'unknown'
        states.append(status)
        result['rows'].append({'row_id': row.id, 'rule_id': row.rule_id, 'status': status})
    if table.hit_policy=='FIRST':
        # Unknown earlier rows can still win; later unknowns cannot displace an
        # established first hit. Evaluate all rows for bounded diagnostic codes,
        # while only the ordered prefix determines the selected route.
        result['status']='gap'
        for row,state in zip(table.rows,states):
            if state=='not_matched':continue
            if state=='matched':
                result.update(status='first_match',selected_row_id=row.id,
                              selected_target_activity_id=row.target_activity_id)
            else:result.update(status='unknown',blocking_row_id=row.id)
            break
        return result
    matches = states.count('matched')
    status = ('overlap' if matches > 1 else 'unknown' if 'unknown' in states
              else 'unique_match' if matches == 1 else 'gap')
    result['status'] = status
    if status == 'unique_match':
        result['selected_target_activity_id'] = table.rows[states.index('matched')].target_activity_id
    return result
