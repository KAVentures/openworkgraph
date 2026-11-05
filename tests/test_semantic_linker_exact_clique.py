"""Bounded exact semantic grouping: compare against a small independent oracle."""
from itertools import combinations
from random import Random

from server.semantic_linker import _compatible_groups


def _oracle(pairs, count):
    for size in range(count, 1, -1):
        for group in combinations(range(1, count + 1), size):
            if all(frozenset(edge) in pairs for edge in combinations(group, 2)):
                return list(group)
    return []


def test_exact_clique_matches_exhaustive_optimum_on_interleaved_graphs():
    rng = Random(326)
    for count in (5, 7, 9):
        for _ in range(30):
            pairs = {frozenset((a, b)) for a, b in combinations(range(1, count + 1), 2)
                     if rng.random() < 0.55}
            if not pairs:
                continue
            groups = [[str(n)] for n in range(1, count + 1)]
            by_id = {str(n): {"metadata": {}} for n in range(1, count + 1)}
            actual, _ = _compatible_groups(pairs, groups, by_id)
            expected = _oracle(pairs, count)
            if expected:
                assert actual
                assert len(actual[0]) == len(expected)
                assert actual[0] == expected
            assert all(frozenset(edge) in pairs
                       for group in actual for edge in combinations(group, 2))


def test_exact_clique_never_crosses_conflicting_business_records():
    pairs = {frozenset(edge) for edge in combinations(range(1, 5), 2)}
    groups = [[str(n)] for n in range(1, 5)]
    by_id = {str(n): {"metadata": {}} for n in range(1, 5)}
    for n, ref in ((1, "account-a"), (2, "account-b")):
        by_id[str(n)]["metadata"]["resource_reference"] = {
            "provider": "crm", "resource_kind": "account", "resource_ref": ref}
    result, _ = _compatible_groups(pairs, groups, by_id)
    assert len(result[0]) == 3
    assert not any(1 in group and 2 in group for group in result)
