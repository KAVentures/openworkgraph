import copy
import json

import pytest

from shared import ai_implementation_batch as batch
from shared import ai_implementation_review as contract
from test_ai_implementation_review import source, proposal


def setup():
    s = [source()]
    p = proposal(s)
    q = copy.deepcopy(p)
    q['title'] = 'Different proposed workflow'
    bad = copy.deepcopy(p)
    bad['outcome']['evidence_refs'] = ['invented']
    return s, contract.prepare(s)['packet_sha256'], [p, bad, q, p, None, bad]


def test_full_population_and_selection():
    s, pin, proposals = setup()
    report = batch.review_batch(s, pin, proposals)
    assert report['item_count'] == 6
    assert report['counts'] == {'structurally_reviewed': 2, 'structural_review_refused': 2, 'exact_duplicate': 2}
    assert report['items'][3]['duplicate_of_index'] == 0
    assert report['items'][5]['duplicate_of_index'] == 1
    assert report['distinct_cited_event_count'] == report['selected_event_count'] == 1
    assert not report['uncited_run_refs']
    assert report['coverage_gaps'] == contract.prepare(s)['coverage_gaps']
    assert not report['portfolio_written'] and not report['execution_authorized']
    assert report['batch_review_sha256'] == contract._digest({k:v for k,v in report.items() if k != 'batch_review_sha256'})
    selected = batch.select(s, pin, proposals, report['batch_review_sha256'], 2)
    assert selected == proposals[2]
    assert contract.review(s, selected)['proposal_sha256'] == report['items'][2]['proposal_sha256']


@pytest.mark.parametrize('index', [1, 3, 4, 5, -1, 6, True])
def test_refuses_unreviewed_duplicate_and_invalid_selection(index):
    s, pin, proposals = setup()
    r = batch.review_batch(s, pin, proposals)
    with pytest.raises(ValueError):
        batch.select(s, pin, proposals, r['batch_review_sha256'], index)


def test_stale_batch_and_packet():
    s, pin, proposals = setup()
    report = batch.review_batch(s, pin, proposals)
    with pytest.raises(ValueError): batch.select(s, pin, proposals[::-1], report['batch_review_sha256'], 0)
    with pytest.raises(ValueError): batch.review_batch(s, '0'*64, proposals)
    with pytest.raises(ValueError): batch.select(s, pin, proposals, '0'*64, 0)


def test_bounds_and_all_invalid():
    s, pin, _ = setup()
    for value in ([], {}, [None]*21, ['x'*batch.MAX_BATCH_BYTES]):
        with pytest.raises(ValueError): batch.review_batch(s, pin, value)
    r = batch.review_batch(s, pin, [None, {}])
    assert r['counts']['structural_review_refused'] == 2
    assert r['distinct_cited_event_count'] == 0 and len(r['uncited_run_refs']) == 1


def test_cli_preserves_exact_selected_proposal(tmp_path):
    s, pin, proposals = setup()
    src, items, report, selected = [tmp_path / f'{n}.json' for n in ('src','items','report','selected')]
    src.write_text(json.dumps(s[0])); items.write_text(json.dumps(proposals))
    base = ['--source', str(src), '--packet-sha256', pin, '--proposals', str(items), '--confirm-local']
    assert batch.main(['review', *base, '--output', str(report)]) == 0
    r = json.loads(report.read_text())
    args = ['select', *base, '--batch-review-sha256', r['batch_review_sha256'], '--item-index', '2', '--output', str(selected)]
    assert batch.main(args) == 0
    assert selected.read_bytes() == contract._json(proposals[2])
    with pytest.raises(SystemExit) as exc: batch.main(args)
    assert exc.value.code == 2
