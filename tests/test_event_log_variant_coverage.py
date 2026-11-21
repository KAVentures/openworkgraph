from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from server import event_log_workspace as workspace
from server.event_log_variant_coverage import coverage


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'DATA_DIR', tmp_path)


def log(paths):
    rows = ['case,activity,time']
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i, path in enumerate(paths):
        for j, activity in enumerate(path):
            rows.append(f'{i},{activity},{(start + timedelta(seconds=j)).isoformat()}')
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


def test_variant_pages_keep_full_ranks_and_coverage_denominator():
    ident = log([['A']]*5 + [['B']]*3 + [['C']] + [['D']])
    first = coverage(ident, limit=2)
    second = coverage(ident, limit=2, offset=first['page']['next_offset'])
    assert second['coverage_targets'] == first['coverage_targets']
    assert second['page'] == {'offset': 2, 'limit': 2, 'next_offset': None}
    assert [variant['rank'] for variant in second['displayed_variants']] == [3, 4]
    assert [variant['cumulative_cases'] for variant in second['displayed_variants']] == [9, 10]
    assert second['omitted_cases'] == 8 and second['omitted_variants'] == 2


def test_long_variants_with_the_same_preview_select_distinct_cases():
    from server import event_log_cases as evidence
    shared = ['Review'] * 501
    ident = log([shared + ['Accept'], shared + ['Reject'], shared + ['Accept']])
    result = coverage(ident)
    first, second = result['displayed_variants']
    assert first['path'] == second['path'] and first['path_truncated'] and second['path_truncated']
    assert first['variant_id'] != second['variant_id']
    first_page = evidence.cases(ident, {'variant_id': first['variant_id'], 'limit': 1})
    assert first_page['selected_cases'] == first['cases'] == 2 and first_page['next_after'] == '0'
    last_page = evidence.cases(ident, {'variant_id': first['variant_id'], 'after': '0'})
    assert [row['case_id'] for row in last_page['cases']] == ['2'] and last_page['next_after'] is None
    assert last_page['cases'][0]['events'] == 502
    rejected = evidence.cases(ident, {'variant_id': second['variant_id'], 'cohort': {'activity': 'Reject'}})
    assert [row['case_id'] for row in rejected['cases']] == ['1']
    assert evidence.cases(ident, {'variant_id': second['variant_id'], 'cohort': {'activity': 'Accept'}})['selected_cases'] == 0
    with pytest.raises(workspace.EventLogWorkspaceError, match="invalid exact variant"):
        evidence.cases(ident, {'variant_id': 'Review'})
    with pytest.raises(workspace.EventLogWorkspaceError, match="either a path"):
        evidence.cases(ident, {'variant_id': first['variant_id'], 'path': ['Review']})


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
