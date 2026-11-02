from io import BytesIO

import pytest

from server import event_log_workspace as workspace
from server.event_log_variant_coverage import coverage


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def log(paths):
    rows = ['case,activity,time']
    for i, path in enumerate(paths):
        for j, activity in enumerate(path):
            rows.append(f'{i},{activity},2024-01-01T00:{j:02d}:00Z')
    return workspace.import_log(BytesIO(('\n'.join(rows)).encode()), name='coverage', source_format='csv',
        mapping={'case_column':'case','activity_column':'activity','timestamp_column':'time'})['log_id']


def test_exact_thresholds_and_omitted_long_tail():
    ident = log([['A','B']]*8 + [['A','C']] + [['D']])
    r = coverage(ident, limit=1)
    assert r['cases'] == 10 and r['distinct_variants'] == 3
    assert r['coverage_targets'] == [
        {'target_percent':50,'variants_needed':1}, {'target_percent':80,'variants_needed':1},
        {'target_percent':90,'variants_needed':2}, {'target_percent':95,'variants_needed':3}]
    assert r['displayed_variants'][0]['cumulative_share'] == .8
    assert (r['omitted_variants'], r['omitted_cases']) == (2,2)


def test_tied_sequences_have_stable_literal_order_and_no_sampling():
    ident = log([['Z'], ['A'], ['A','A'], ['M']])
    r = coverage(ident)
    assert [v['path'] for v in r['displayed_variants']] == [['A'], ['A','A'], ['M'], ['Z']]
    assert [v['cumulative_cases'] for v in r['displayed_variants']] == [1,2,3,4]
    assert r['omitted_cases'] == 0


def test_whole_case_filter_changes_denominator_and_thresholds():
    ident = log([['A','B']]*8 + [['A','C']] + [['D']])
    r = coverage(ident, cohort={'activity':'C'})
    assert r['cases'] == 1 and r['cases_before_filter'] == 10
    assert r['displayed_variants'][0]['path'] == ['A','C']
    assert all(t['variants_needed'] == 1 for t in r['coverage_targets'])


def test_empty_filtered_population_has_no_invented_thresholds():
    ident = log([['A']])
    r = coverage(ident, cohort={'activity':'missing'})
    assert r['cases'] == 0 and r['distinct_variants'] == 0
    assert r['displayed_variants'] == []
    assert all(t['variants_needed'] is None for t in r['coverage_targets'])


@pytest.mark.parametrize('limit',[True,0,201,'20'])
def test_invalid_limits_refused(limit):
    with pytest.raises(workspace.EventLogWorkspaceError):
        coverage('not-a-log', limit=limit)


def test_targets_include_variants_beyond_maximum_display():
    ident = log([[f'Activity-{i:03d}'] for i in range(250)])
    r = coverage(ident, limit=200)
    assert r['coverage_targets'][-1] == {'target_percent':95,'variants_needed':238}
    assert r['omitted_variants'] == 50 and r['omitted_cases'] == 50
    assert r['displayed_variants'][-1]['cumulative_share'] == .8
