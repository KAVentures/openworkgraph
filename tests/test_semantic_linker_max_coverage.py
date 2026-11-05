"""Regression tests for deterministic coverage-first semantic subgroup extraction."""
from server.semantic_linker import _compatible_groups


def _groups(count):
    return [[f"e{i}"] for i in range(1, count + 1)]


def _pairs(*edges):
    return {frozenset(edge) for edge in edges}


def test_bridge_does_not_destroy_larger_supported_group():
    # Lowest-ID greedy used to take 1-2, leaving 3-4 as only a pair.
    # The mutually supported 2-3-4 clique should be preserved.
    pairs = _pairs((1, 2), (2, 3), (2, 4), (3, 4))
    result, refused = _compatible_groups(pairs, _groups(4), {})
    assert result == [[2, 3, 4]]
    assert refused == 1


def test_no_transitive_bridge_and_deterministic_order():
    pairs = _pairs((1, 2), (2, 3), (4, 5))
    expected = _compatible_groups(pairs, _groups(5), {})
    assert expected == ([[1, 2], [4, 5]], 1)
    for _ in range(10):
        assert _compatible_groups(set(reversed(sorted(pairs, key=lambda x: sorted(x)))), _groups(5), {}) == expected


def test_conflicting_record_references_never_join():
    events = {
        "e1": {"metadata": {"resource_reference": {"provider": "jira", "resource_kind": "issue", "resource_ref": "A"}}},
        "e2": {"metadata": {"resource_reference": {"provider": "jira", "resource_kind": "issue", "resource_ref": "B"}}},
        "e3": {"metadata": {}},
    }
    groups, refused = _compatible_groups(
        _pairs((1, 2), (1, 3), (2, 3)), _groups(3), events
    )
    assert groups == [[1, 3]]
    assert refused == 1
