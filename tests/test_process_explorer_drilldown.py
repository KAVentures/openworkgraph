"""Exact-variant summaries must never be computed from displayed samples."""
import json

from server.process_mining_csv import mine_csv_events


def mine(cases):
    lines = ['case_id,activity,timestamp']
    # Interleave cases deliberately; each tuple supplies its own elapsed seconds.
    for index in range(max(map(len, cases.values()))):
        for case, events in cases.items():
            if index < len(events):
                activity, seconds = events[index]
                lines.append(f'{case},{activity},2026-10-20T10:{seconds // 60:02}:{seconds % 60:02}Z')
    return mine_csv_events(('\n'.join(lines) + '\n').encode())


def test_variant_gaps_counts_and_examples_use_exact_full_cases():
    result = mine({
        'secret-first': [('Open', 0), ('Review', 10), ('Review', 20), ('Close', 40)],
        'secret-second': [('Open', 0), ('Review', 30), ('Review', 60), ('Close', 100)],
        'secret-third': [('Open', 0), ('Close', 300)],
    })
    variant = result['variants'][0]
    assert variant['case_count'] == 2
    assert variant['activities'][1] == {'activity': 'Review', 'event_count': 4, 'case_count': 2}
    edges = {(edge['from'], edge['to']): edge for edge in variant['transitions']}
    assert edges['Open', 'Review']['median_elapsed_gap_seconds'] == 20
    assert edges['Review', 'Review']['max_elapsed_gap_seconds'] == 30
    assert ('Open', 'Close') not in edges
    assert variant['median_case_elapsed_seconds'] == 70
    examples = variant['case_examples']
    assert len(examples) == 2
    assert all(case['variant_id'] == variant['variant_id'] for case in examples)
    assert [event['position'] for event in examples[0]['events']] == [1, 2, 3, 4]
    assert examples[0]['events'][0]['elapsed_since_previous_seconds'] is None
    assert examples[0]['events'][2]['elapsed_since_previous_seconds'] == 10
    assert 'secret-' not in json.dumps(result)


def test_case_examples_are_independent_of_global_sample_and_summary_counts():
    # The second variant begins after the global 30-case sample ends.
    cases = {f'private-{i}': [('Open', 0), ('Close', i)] for i in range(35)}
    cases.update({f'z-hidden-{i}': [('Review', 0), ('Close', 300 + i)] for i in range(6)})
    result = mine(cases)
    assert result['cases_truncated']
    assert all(case['path'][0] == 'Open' for case in result['cases'])
    second = result['variants'][1]
    assert second['case_count'] == 6
    assert len(second['case_examples']) == 5
    assert second['case_examples_truncated']
    assert second['activities'][0]['event_count'] == 6
    assert second['transitions'][0]['median_elapsed_gap_seconds'] == 302.5
    assert len(result['cases']) == 30
    assert 'hidden-' not in json.dumps(second)


def test_identical_truncated_prefixes_remain_different_variants():
    prefix = [('Review', i) for i in range(24)]
    result = mine({'first': prefix + [('Approve', 30)],
                   'second': prefix + [('Reject', 60)]})
    a, b = result['variants']
    assert a['observed_path'] == b['observed_path']
    assert a['variant_id'] != b['variant_id']
    assert a['path_truncated'] and b['path_truncated']
    assert a['activities'][-1]['activity'] == 'Approve'
    assert b['activities'][-1]['activity'] == 'Reject'
    assert len(a['case_examples'][0]['events']) == 24
    assert a['activities'][0]['event_count'] == 24


def test_repeated_transition_counts_and_case_counts_have_distinct_meanings():
    result = mine({'private': [('A', 0), ('A', 2), ('A', 6)]})
    edge = result['variants'][0]['transitions'][0]
    assert edge['transition_count'] == 2
    assert edge['case_count'] == 1
    assert edge['median_elapsed_gap_seconds'] == 3
    assert edge['gap_is_verified_waiting'] is False
