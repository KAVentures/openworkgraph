"""Global semantic grouping chooses safe coverage, not just one maximum clique."""
from server.semantic_linker import _compatible_groups


def _pairs(*edges):
    return {frozenset(edge) for edge in edges}


def test_partition_covers_more_items_than_largest_clique():
    # Greedy maximum-clique selection takes 1,2,3, stranding 4 and 5.
    pairs = _pairs((1, 2), (1, 3), (2, 3), (1, 4), (2, 5))
    groups = [[str(n)] for n in range(1, 6)]
    by_id = {str(n): {} for n in range(1, 6)}
    accepted, vetoed = _compatible_groups(pairs, groups, by_id)
    # Both partitions cover four nodes and two edges; lexical tie-break wins.
    assert accepted == [[1, 3], [2, 5]]
    assert vetoed == 1


def test_conflicting_records_never_join_even_with_full_pair_support():
    pairs = _pairs((1, 2), (1, 3), (2, 3))
    groups = [[str(n)] for n in range(1, 4)]
    by_id = {
        "1": {"metadata": {"resource_reference": {"provider": "crm", "resource_kind": "account", "resource_ref": "a"}}},
        "2": {"metadata": {"resource_reference": {"provider": "crm", "resource_kind": "account", "resource_ref": "b"}}},
        "3": {},
    }
    accepted, _ = _compatible_groups(pairs, groups, by_id)
    assert len(accepted) == 1
    assert len(accepted[0]) == 2
    assert not {1, 2}.issubset(accepted[0])


def test_unsupported_transitive_edge_never_forms_group():
    pairs = _pairs((1, 2), (2, 3))
    groups = [[str(n)] for n in range(1, 4)]
    accepted, _ = _compatible_groups(pairs, groups, {str(n): {} for n in range(1, 4)})
    assert all(len(group) == 2 for group in accepted)
    assert accepted == [[1, 2]]


def test_order_independent_deterministic_tie_break():
    edges = [(1, 2), (1, 3), (2, 3), (1, 4), (2, 5)]
    groups = [[str(n)] for n in range(1, 6)]
    expected = _compatible_groups(_pairs(*edges), groups, {})
    assert expected == _compatible_groups(_pairs(*reversed(edges)), groups, {})
