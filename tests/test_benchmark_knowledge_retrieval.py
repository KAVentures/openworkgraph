import pytest
from scripts.benchmark_knowledge_retrieval import run


@pytest.mark.parametrize('every,expected', [(0,0), (1,351), (17,21)])
def test_exact_coverage_and_no_model_claims(every, expected):
    r = run(351, every, 100, 20)
    assert r['expected_matches'] == r['returned_matches'] == expected
    assert r['duplicate_matches'] == 0
    assert r['discovery']['selected_exact_reads'] == min(3, expected)
    assert r['discovery']['scanned_record_occurrences'] >= 351
    assert r['discovery']['total_local_read_json_bytes'] >= r['discovery']['local_read_json_bytes']
    assert not r['model_token_savings_verified'] and not r['model_quality_verified']
    assert not r['live_transport_latency_measured']


def test_dense_page_sizing_avoids_repeated_scan():
    wide = run(351, 1, 100, 20)
    narrow = run(351, 1, 20, 20)
    assert wide['discovery']['scanned_record_occurrences'] > 351
    assert narrow['discovery']['scanned_record_occurrences'] == 351
    assert wide['discovery']['calls'] == narrow['discovery']['calls']
    assert wide['discovery']['total_local_read_json_bytes'] > narrow['discovery']['total_local_read_json_bytes']


@pytest.mark.parametrize('args', [(0,1,100,20), (10001,1,100,20), (100,-1,100,20), (100,1,101,20)])
def test_bounds(args):
    with pytest.raises(ValueError):
        run(*args)


def test_adaptive_hint_reduces_dense_rereads_without_losing_matches():
    fixed = run(351, 1, 100, 20)
    adaptive = run(351, 1, 100, 20, True)
    assert adaptive['returned_matches'] == fixed['returned_matches'] == 351
    assert adaptive['duplicate_matches'] == 0
    assert adaptive['discovery']['scanned_record_occurrences'] == 431
    assert adaptive['discovery']['calls'] == fixed['discovery']['calls']
    assert adaptive['discovery']['total_local_read_json_bytes'] < fixed['discovery']['total_local_read_json_bytes']
