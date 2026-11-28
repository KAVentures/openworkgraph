from itertools import permutations
from random import Random

from shared.process_source_provenance import summarize


def rows(items):
    return [{'text': text, 'document_title': title} for text, title in items]


def oracle(a, b):
    # Enumerate injections of the smaller occurrence set into the larger.
    # Lexicographically maximize exact matches, then matches with equal title.
    if len(a) > len(b):
        a, b = b, a
    return max((sum(x[0] == y[0] for x, y in zip(a, p)),
                sum(x == y for x, y in zip(a, p)))
               for p in permutations(b, len(a)))


def test_random_exhaustive_matching_oracle():
    rng = Random(21)
    universe = [(s, t) for s in 'xyz' for t in 'ABC']
    for _ in range(500):
        a = rng.choices(universe, k=rng.randrange(7))
        b = rng.choices(universe, k=rng.randrange(7))
        report, groups = summarize(rows(a), rows(b))
        retained, same = oracle(a, b)
        assert report['retained_occurrences'] == retained
        assert report['same_title_retained_maximum'] == same
        assert report['cross_title_retained_minimum'] == retained - same
        assert sum(t['baseline_occurrences'] for t in report['titles']) == len(a)
        assert sum(t['candidate_occurrences'] for t in report['titles']) == len(b)
        assert sum(t['same_title_retained'] for t in report['titles']) == same
        assert (report, groups) == summarize(rows(a[::-1]), rows(b[::-1]))


def test_duplicate_occurrences_and_ambiguous_titles():
    a = rows([('same', 'A'), ('same', 'A'), ('same', 'B'), ('gone', 'B')])
    b = rows([('same', 'B'), ('same', 'B'), ('same', 'C'), ('new', 'A')])
    report, groups = summarize(a, b)
    assert report['same_title_retained_maximum'] == 1
    assert report['cross_title_retained_minimum'] == 2
    assert groups['same']['title_counts'] == [
        {'document_title': 'A', 'baseline_occurrences': 2, 'candidate_occurrences': 0},
        {'document_title': 'B', 'baseline_occurrences': 1, 'candidate_occurrences': 2},
        {'document_title': 'C', 'baseline_occurrences': 0, 'candidate_occurrences': 1}]
    assert report['distinct_text_groups_requiring_cross_title_match'] == 1
    assert not report['document_identity_verified'] and not report['policy_authority_verified']


def test_copy_growth_without_forced_cross_title_matching():
    report, groups = summarize(rows([('x', 'A')]), rows([('x', 'A'), ('x', 'B')]))
    assert report['retained_occurrences'] == 1
    assert report['cross_title_retained_minimum'] == 0
    assert groups['x']['title_distribution_changed']
    assert report['distinct_text_groups_requiring_cross_title_match'] == 0


def test_empty_and_max_indexed_population():
    report, groups = summarize([], [])
    assert report['retained_occurrences'] == 0 and not groups
    a = rows([('statement', 'A')] * 1500)
    b = rows([('statement', 'B')] * 1500)
    report, groups = summarize(a, b)
    assert report['cross_title_retained_minimum'] == 1500
