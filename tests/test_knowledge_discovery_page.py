import pytest
from mcp_server import knowledge_discovery_page as page
from test_knowledge_discovery import rec, response


def make(rows, more=False):
    return {**response(*rows, more=more), 'next_after': rows[-1]['workflow_id'] if more else None}


def test_truncated_matches_resume_without_skipping():
    rows = [rec(i, 'Invoice') for i in range(1, 7)]
    a = page.discover(make(rows, True), ['invoice'], 2, '', 6)
    assert a['next_after'] == 'process_2' and a['matches_truncated']
    b = page.discover(make(rows[2:], False), ['invoice'], 10, a['next_after'], 6)
    assert [r['workflow_id'] for r in a['candidates'] + b['candidates']] == [r['workflow_id'] for r in rows]
    assert not b['has_more'] and not b['atomic_catalog_snapshot_verified']


def test_zero_match_page_still_advances():
    r = page.discover(make([rec(1, 'Renewals'), rec(2, 'Renewals')], True), ['invoice'], 4, '', 20)
    assert not r['candidates'] and r['next_after'] == 'process_2'
    assert r['has_more'] and r['source_inventory_truncated']


def test_final_page_with_truncated_shortlist_can_continue():
    r = page.discover(make([rec(1, 'A'), rec(2, 'B')]), [], 1, '', 20)
    assert r['has_more'] and r['next_after'] == 'process_1'
    assert not r['source_inventory_truncated']


def test_exact_request_and_bad_inputs():
    args, terms = page.request_args('', 5, 'Invoice', False, 'process_1', 20)
    assert args == {'workflow_id':'', 'after':'process_1', 'limit':20} and terms == ['invoice']
    for cursor, size in [('bad/id', 20), ('', 101), ('', True), ('', 0)]:
        with pytest.raises(ValueError): page.request_args('', 5, '', True, cursor, size)
    with pytest.raises(ValueError): page.request_args('process_1', 5, '', True, '', 20)


@pytest.mark.parametrize('bad',['order','old','cursor','missing','oversize','empty_more'])
def test_refuses_bad_pages(bad):
    rows = [rec(2, 'A'), rec(3, 'B')]; r = make(rows, True); after = 'process_1'; size = 20
    if bad == 'order': r = make(rows[::-1], True)
    elif bad == 'old': after = 'process_2'
    elif bad == 'cursor': r['next_after'] = 'process_9'
    elif bad == 'missing': del r['next_after']
    elif bad == 'oversize': size = 1
    elif bad == 'empty_more': r = {**response(more=True), 'next_after':'process_9'}
    with pytest.raises(ValueError): page.discover(r, [], 5, after, size)


def test_sparse_search_across_large_catalog_no_skips_or_duplicate_matches():
    rows = sorted([rec(f'{i:04d}', 'Invoice' if i % 9 == 0 else 'Renewal')
                   for i in range(1, 352)], key=lambda r:r['workflow_id'])
    expected = [r['workflow_id'] for r in rows if r['title'] == 'Invoice']
    seen, after, iterations = [], '', 0
    while True:
        remaining = [r for r in rows if r['workflow_id'] > after]
        current = remaining[:23]
        result = page.discover(make(current, len(remaining) > len(current)), ['invoice'], 2, after, 23)
        seen.extend(r['workflow_id'] for r in result['candidates'])
        iterations += 1
        if not result['has_more']:
            break
        assert result['next_after'] > after and iterations < 100
        after = result['next_after']
    assert seen == expected and len(seen) == len(set(seen)) and iterations > 10
