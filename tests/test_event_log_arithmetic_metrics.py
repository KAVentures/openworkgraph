"""Independent values, dimensional checks and bounded failure semantics for R9 arithmetic."""
from io import BytesIO
import json
import os

import pytest

from server import event_log_query as query
from server import event_log_workspace as workspace


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    doc = b'case,activity,time,amount\na,A,2024-01-01T00:00:00Z,10\na,B,2024-01-01T01:00:00Z,2\na,B,2024-01-01T02:00:00Z,3\nb,A,2024-01-02T00:00:00Z,3\nc,A,2024-01-03T00:00:00Z,\nc,B,2024-01-03T01:00:00Z,\n'
    return workspace.import_log(BytesIO(doc), name='arithmetic', source_format='csv', mapping={
        'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time',
        'attribute_columns': ['amount']})['log_id']


SPEND = {'name': 'spend', 'kind': 'event_attribute_sum', 'attribute': 'amount', 'unit': 'EUR'}
COUNT = {'name': 'items', 'kind': 'activity_count', 'activity': 'B'}


def ref(name):
    return {'kind': 'metric', 'name': name}


def constant(value, unit='ratio'):
    return {'kind': 'constant', 'value': value, 'unit': unit}


def expr(name='result', operation='divide', left=None, right=None, **kw):
    return {'name': name, 'kind': 'arithmetic', 'operation': operation,
            'left': left or ref('spend'), 'right': right or ref('items'), **kw}


def run(log, metrics, **kw):
    return query.run(log, {'level': 'cases', 'metrics': metrics, 'measures': [{'fn': 'sum', 'of': 'metric:result'}], **kw})


def test_ratio_is_per_case_with_missing_zero_and_exact_unit_provenance(log):
    result = run(log, [SPEND, COUNT, expr()])
    # a = (10+2+3)/2; b is a zero divisor; c has no numeric amount.
    assert result['rows'] == [[7.5]]
    cov = result['metrics'][-1]
    assert cov['cases_with_value'] == 1 and cov['cases_without_value'] == 2
    assert cov['cases_with_missing_operand'] == 1 and cov['cases_with_zero_divisor'] == 1
    assert cov['unit_powers'] == {'EUR': 1, 'events': -1}
    assert cov['unit'] == '(EUR) * (events)^-1'
    assert result['units']['sum_metric:result'] == cov['unit']
    assert cov['definition']['expression_version'] == '1'
    # Coverage and aggregates use the same selected full cases.
    filtered = run(log, [SPEND, COUNT, expr()], where={'min_events': 2})
    assert filtered['matching']['cases'] == 2 and filtered['metrics'][-1]['cases_with_zero_divisor'] == 0


@pytest.mark.parametrize(('operation', 'value', 'expected'), [('add', 2, 22), ('subtract', 2, 14), ('multiply', 2, 36), ('divide', 2, 9)])
def test_finite_typed_constants_and_arithmetic(log, operation, value, expected):
    unit = 'EUR' if operation in {'add', 'subtract'} else 'ratio'
    r = run(log, [SPEND, expr(operation=operation, right=constant(value, unit))])
    assert r['rows'] == [[expected]] and r['metrics'][-1]['unit'] == 'EUR'


def test_dimensional_cancellation_and_compound_unit_addition(log):
    ratio = expr('per_item')
    restored = expr(operation='multiply', left=ref('per_item'), right=ref('items'))
    r = run(log, [SPEND, COUNT, ratio, restored])
    assert r['rows'] == [[15.0]] and r['metrics'][-1]['unit_powers'] == {'EUR': 1}
    # Equal units cancel to a dimensionless ratio, with zero still missing.
    r = run(log, [COUNT, expr(left=ref('items'), right=ref('items'))])
    assert r['rows'] == [[2.0]] and r['metrics'][-1]['unit'] == 'ratio'
    doubled = expr(operation='add', left=ref('per_item'), right=ref('per_item'))
    assert run(log, [SPEND, COUNT, ratio, doubled])['rows'] == [[15.0]]


def test_declared_labels_are_atomic_and_no_currency_or_time_conversion(log):
    for unit in ['USD', 'eur', ' EUR', 'minutes']:
        with pytest.raises(workspace.EventLogWorkspaceError, match='identical units'):
            run(log, [SPEND, expr(operation='add', right=constant(1, unit))])
    r = run(log, [expr(left=constant(1, 'EUR/hour'), right=constant(1, 'EUR'))])
    assert r['metrics'][-1]['unit_powers'] == {'EUR/hour': 1, 'EUR': -1}


def test_zero_missing_and_overflow_are_json_safe_and_propagate(log):
    r = run(log, [expr('overflow', operation='multiply', left=constant(1e308), right=constant(1e308)),
                  expr(left=ref('overflow'), right=constant(1))])
    assert r['rows'] == [[None]]
    assert r['metrics'][0]['cases_with_non_finite_value'] == 3
    assert r['metrics'][1]['cases_with_missing_operand'] == 3
    json.dumps(r, allow_nan=False)
    zero = run(log, [expr(left=constant(1), right=constant(-0.0))])
    assert zero['rows'] == [[None]] and zero['metrics'][0]['cases_with_zero_divisor'] == 3


@pytest.mark.parametrize('metrics', [
    [expr(left=ref('result'))],  # self
    [expr(), SPEND, COUNT],  # forward
    [expr('a', left=ref('b')), expr('b', left=ref('a'))],  # cycle
    [expr(left=ref('missing'))],
    [expr(left={'kind': 'metric', 'name': "x); DROP TABLE events;--"})],
    [expr(left=constant('3'))], [expr(left=constant(True))],
    [expr(left=constant(float('nan')))], [expr(left=constant(float('inf')))],
    [expr(left=constant(1), right=constant(2), expression_version='2')],
    [expr(left=constant(1), right=constant(2), sql='SELECT 1')],
    [expr(left=constant(1), right=constant(2), operation='power')],
    [expr(left={'kind': 'arithmetic', 'left': constant(1), 'right': constant(2)})],
    [COUNT, COUNT, COUNT, COUNT, COUNT],
])
def test_invalid_or_unbounded_spec_refused_before_dataset_access(monkeypatch, metrics):
    def no_query(*args, **kw):
        pytest.fail('invalid expression accessed dataset')
    monkeypatch.setattr(workspace, '_query', no_query)
    with pytest.raises(workspace.EventLogWorkspaceError):
        run('unused', metrics)


def test_saved_analysis_pins_complete_dependencies_but_library_is_symbolic(log):
    metrics = [SPEND, COUNT, expr()]
    spec = {'level': 'cases', 'metrics': metrics, 'measures': [{'fn': 'sum', 'of': 'metric:result'}]}
    template = query.save_metric(log, {'metric': expr()})
    saved = query.save(log, {'name': 'Per-item spend', 'spec': spec})
    query.delete_metric(log, template['metric_id'])
    restored = query.list_saved(log)['analyses'][0]
    assert restored['spec'] == saved['spec']
    assert query.run(log, restored['spec'])['rows'] == [[7.5]]
    with pytest.raises(workspace.EventLogWorkspaceError, match='earlier metric'):
        run(log, [template['metric']])
    # Removing a template never changes a pinned analysis.
    assert restored['spec']['metrics'][0]['unit'] == 'EUR'


def test_empty_selection_and_case_units_do_not_invent_values(log):
    r = run(log, [SPEND, COUNT, expr()], where={'ends_with': 'Absent'})
    assert r['rows'] == [[None]] and r['matching']['cases'] == 0
    assert all(m['cases_with_value'] == m['cases_without_value'] == 0 for m in r['metrics'])
    amount = {**SPEND, 'kind': 'case_attribute_number'}
    r = run(log, [amount, expr(right=constant(2))])
    assert r['metrics'][-1]['unit'] == 'EUR'


def test_symbolic_template_validation_does_not_evaluate_or_read_sources(log, monkeypatch):
    def no_query(*args, **kw):
        pytest.fail('saving a symbolic template queried event rows')
    monkeypatch.setattr(workspace, '_query', no_query)
    saved = query.save_metric(log, {'metric': expr()})
    assert query.list_metrics(log)['metrics'][0]['metric'] == saved['metric']


@pytest.mark.skipif(os.environ.get('OWG_SCALE_ACCEPTANCE') != '1', reason='on-demand owned scale acceptance')
def test_arithmetic_large_case_population(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)
    log = workspace.import_log(BytesIO(b'case,activity,time\nseed,A,2024-01-01T00:00:00Z\n'),
        name='owned scale', source_format='csv', mapping={
            'case_column': 'case', 'activity_column': 'activity', 'timestamp_column': 'time'})['log_id']
    cases = 500_000
    conn = workspace._connect()
    try:
        conn.execute('DELETE FROM event_objects WHERE log_id=?', [log])
        conn.execute('DELETE FROM events WHERE log_id=?', [log])
        conn.execute('DELETE FROM objects WHERE log_id=?', [log])
        conn.execute("INSERT INTO objects SELECT ?, 'c'||i, 'case', '{}' FROM range(?) t(i)", [log, cases])
        conn.execute("""INSERT INTO events SELECT ?, 'e'||i||'_'||j,
            CASE WHEN j=0 THEN 'A' ELSE 'B' END,
            TIMESTAMP '2024-01-01' + j * INTERVAL '1 second', '',
            CASE WHEN j=0 THEN '{"amount":6}' ELSE '{"amount":1}' END
            FROM range(?) t(i), range(3) u(j) WHERE j <= i % 3""", [log, cases])
        conn.execute("""INSERT INTO event_objects SELECT log_id, event_id,
            'c'||split_part(substr(event_id,2),'_',1), '' FROM events WHERE log_id=?""", [log])
        events = conn.execute('SELECT count(*) FROM events WHERE log_id=?', [log]).fetchone()[0]
        conn.execute('UPDATE logs SET events=?, objects=? WHERE log_id=?', [events, cases, log])
    finally:
        conn.close()
    started = time.monotonic()
    r = run(log, [SPEND, COUNT, expr('per_item'), expr(operation='multiply', left=ref('per_item'), right=ref('items'))])
    elapsed = time.monotonic() - started
    # Independent population counts, not SQL mirroring the planner.
    frequencies = [len(range(remainder, cases, 3)) for remainder in range(3)]
    assert r['rows'] == [[frequencies[1] * 7 + frequencies[2] * 8]]
    assert r['matching']['cases'] == cases
    assert r['metrics'][2]['cases_with_zero_divisor'] == frequencies[0]
    assert r['metrics'][3]['cases_with_missing_operand'] == frequencies[0]
    assert elapsed < 120  # explicit environment acceptance budget, not a customer SLA
    print(json.dumps({'cases': cases, 'events': events, 'arithmetic_seconds': round(elapsed, 3), 'budget_seconds': 120}))
