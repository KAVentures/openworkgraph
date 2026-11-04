"""Decision dependencies: one table's selected step is a fact for another (#370 R21)."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from shared.process_catalog import process_bpmn
from shared.process_decision_tables import evaluate_decisions
from shared.process_model import ProcessModel
from shared.process_rule_checks import RuleCaseBatch, check_case_rules


def rule(rid, path, op, expected):
    return {'id': rid, 'text': rid.replace('_', ' '), 'user_confirmed': True,
            'predicate': {'conditions': [{'path': path, 'operator': op, 'expected': expected}]}}


def table(tid, activity, rows, **extra):
    return {'id': tid, 'decision_activity_id': activity, 'effective_from': '2026-01-01',
            'source_basis': {'label': 'Credit policy', 'document_sha256': 'a' * 64},
            'rows': [{'id': f'{tid}_{i}', 'rule_id': r, 'target_activity_id': t} for i, (r, t) in enumerate(rows)],
            'user_confirmed': True, **extra}


def graph(dependency=True):
    """Credit check decides first; the approval decision uses its outcome."""
    value = {'goal': 'Approve a loan', 'baseline_source': 'user_declared', 'baseline_path': ['Credit', 'Approve'],
             'baseline_coverage': 'user_declared_design',
             'activities': [{'id': 'credit', 'label': 'Credit', 'kind': 'decision'},
                            {'id': 'good', 'label': 'Good credit'}, {'id': 'poor', 'label': 'Poor credit'},
                            {'id': 'approval', 'label': 'Approval', 'kind': 'decision'},
                            {'id': 'approve', 'label': 'Approve'}, {'id': 'decline', 'label': 'Decline'}],
             'transitions': [{'source': 'credit', 'target': 'good', 'rule_id': 'score_high'},
                             {'source': 'credit', 'target': 'poor', 'rule_id': 'score_low'},
                             {'source': 'good', 'target': 'approval'}, {'source': 'poor', 'target': 'approval'},
                             {'source': 'approval', 'target': 'approve', 'rule_id': 'credit_good'},
                             {'source': 'approval', 'target': 'decline', 'rule_id': 'credit_poor'}],
             'entry_activity_id': 'credit', 'terminal_activity_ids': ['approve', 'decline'],
             'business_rules': [rule('score_high', '/score', 'gte', 700), rule('score_low', '/score', 'lt', 700),
                                rule('credit_good', '/decisions/credit', 'eq', 'good'),
                                rule('credit_poor', '/decisions/credit', 'eq', 'poor')],
             'decision_tables': [
                 table('approval_table', 'approval', [('credit_good', 'approve'), ('credit_poor', 'decline')],
                       **({'required_decisions': [{'table_id': 'credit_table', 'fact_path': '/decisions/credit'}]}
                          if dependency else {})),
                 table('credit_table', 'credit', [('score_high', 'good'), ('score_low', 'poor')])]}
    return value


def model(**kw):
    return ProcessModel.model_validate(graph(**kw))


def test_tables_without_dependencies_serialize_as_before():
    dumped = model(dependency=False).model_dump()
    assert all('required_decisions' not in t for t in dumped['decision_tables'])


@pytest.mark.parametrize('score,credit,approval', [(720, 'good', 'approve'), (650, 'poor', 'decline')])
def test_required_decision_feeds_the_dependent_table(score, credit, approval):
    results = {r['table_id']: r for r in evaluate_decisions(model(), {'score': score}, '2026-06-01')}
    assert results['credit_table']['selected_target_activity_id'] == credit
    assert results['approval_table']['selected_target_activity_id'] == approval
    assert results['approval_table']['required_decision_table_ids'] == ['credit_table']


def test_unresolved_requirement_withholds_the_dependent_decision():
    results = {r['table_id']: r for r in evaluate_decisions(model(), {}, '2026-06-01')}
    assert results['credit_table']['status'] == 'unknown'
    assert results['approval_table']['status'] == 'dependency_unresolved'
    assert results['approval_table']['blocking_decision_table_id'] == 'credit_table'
    assert results['approval_table']['selected_target_activity_id'] is None


def test_supplied_facts_cannot_override_a_required_decision():
    results = {r['table_id']: r for r in evaluate_decisions(model(), {'score': 650, 'decisions': {'credit': 'good'}},
                                                            '2026-06-01')}
    assert results['approval_table']['status'] == 'dependency_fact_conflict'
    assert results['approval_table']['selected_target_activity_id'] is None


def test_unconfirmed_dependent_table_stays_unconfirmed():
    value = graph()
    value['decision_tables'][0]['user_confirmed'] = False
    results = {r['table_id']: r for r in evaluate_decisions(ProcessModel.model_validate(value), {}, '2026-06-01')}
    assert results['approval_table']['status'] == 'unconfirmed'


@pytest.mark.parametrize('mutate', [
    lambda v: v['decision_tables'][1].update(required_decisions=[{'table_id': 'approval_table', 'fact_path': '/d/a'}]),
    lambda v: v['decision_tables'][0].update(required_decisions=[{'table_id': 'missing', 'fact_path': '/d/a'}]),
    lambda v: v['decision_tables'][0].update(required_decisions=[{'table_id': 'approval_table', 'fact_path': '/d/a'}]),
    lambda v: v['decision_tables'][0].update(required_decisions=[{'table_id': 'credit_table', 'fact_path': 'decisions'}]),
    lambda v: v['decision_tables'][0].update(required_decisions=[{'table_id': 'credit_table', 'fact_path': '/a'},
                                                                 {'table_id': 'credit_table', 'fact_path': '/b'}]),
])
def test_invalid_dependencies_are_refused(mutate):
    value = graph()
    mutate(value)
    with pytest.raises(ValidationError):
        ProcessModel.model_validate(value)


def test_nested_fact_paths_are_refused():
    value = graph()
    value['decision_tables'].append(table('extra', 'credit', [('score_high', 'good'), ('score_low', 'poor')]))
    value['decision_tables'][0]['required_decisions'] = [{'table_id': 'credit_table', 'fact_path': '/a'},
                                                         {'table_id': 'extra', 'fact_path': '/a/b'}]
    with pytest.raises(ValidationError):
        ProcessModel.model_validate(value)


def test_case_checks_use_dependency_order():
    record = {'workflow_id': 'loan', 'revision': 1, 'content_sha256': 'b' * 64, 'process_model': model().model_dump()}
    batch = RuleCaseBatch.model_validate({'as_of_date': '2026-06-01', 'cases': [
        {'case_id': 'case_1', 'facts': {'score': 720}, 'activity_ids': ['credit', 'good', 'approval', 'approve']},
        {'case_id': 'case_2', 'facts': {'score': 720}, 'activity_ids': ['credit', 'good', 'approval', 'decline']}]})
    report = check_case_rules(record, batch.cases, batch.as_of_date)
    first, second = ({t['table_id']: t for t in c['decision_tables']} for c in report['cases'])
    assert first['approval_table']['proposed_branch_agrees'] is True
    assert second['approval_table']['proposed_branch_agrees'] is False


def test_dependencies_survive_bpmn_round_trip_and_block_generated_projects():
    from shared.process_bpmn_import import import_bpmn
    from shared.process_agent_project import build_runtime_spec
    packet = {'workflow_id': 'loan', 'revision': 1, 'content_sha256': 'b' * 64, 'process_model': model().model_dump()}
    imported = ProcessModel.model_validate(import_bpmn(process_bpmn(packet).encode())['process_model'])
    [approval] = [t for t in imported.decision_tables if t.id == 'approval_table']
    assert approval.required_decisions[0].table_id == 'credit_table'
    with pytest.raises(ValueError, match='dependent decision'):
        build_runtime_spec(dict(deepcopy(packet), format='openworkgraph.versioned-process-export.v1',
                                execution_authorization=False))
