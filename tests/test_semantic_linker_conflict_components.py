"""Conflict-aware semantic grouping: preserve safe proposals, reject transitive bridges."""
import json

from server import semantic_linker as linker


def event(n, title, ref=None):
    metadata = {"target": {"label": title}}
    if ref:
        metadata["resource_reference"] = {"provider": "crm", "resource_kind": "account", "resource_ref": ref}
    return {"event_id": f"evt-{n}", "observed_at": f"2026-10-20T10:{n:02d}:00+00:00",
            "app": "Chrome", "source": "browser_extension", "event_type": "browser_click",
            "session_id": "session", "window_title": "Chrome · " + title,
            "target_label": title, "metadata": metadata}


def test_conflicting_account_does_not_discard_safe_email_account_pair():
    events = [event(1, "Customer email"), event(2, "Account alpha", "account-A"),
              event(3, "Account beta", "account-B")]
    items, members = linker.build_items(events)
    assert len(items) == 3
    result = linker.suggest(events, lambda *_: json.dumps({"groups": [[1, 2, 3]]}))
    assert len(result["suggestions"]) == 1
    assert set(result["suggestions"][0]["event_ids"]) == {"evt-1", "evt-2"}
    assert result["vetoed"] == 1
    assert result["suggestions"][0]["requires_review"] is True
    assert result["merged_into_canonical_evidence"] is False


def test_bridge_without_pair_support_cannot_join_endpoints():
    groups = [["a"], ["b"], ["c"]]
    pairs = {frozenset((1, 2)), frozenset((2, 3))}
    selected, refused = linker._compatible_groups(pairs, groups, {"a": {}, "b": {}, "c": {}})
    assert selected == [[1, 2]] and refused == 1


def test_disjoint_compatible_pairs_survive_and_conflicts_never_join():
    groups = [[str(n)] for n in range(1, 6)]
    by_id = {str(n): {"metadata": {"resource_reference": {"provider": "crm",
             "resource_kind": "account", "resource_ref": "A" if n in (1, 2) else "B"}}}
             for n in (1, 2, 4, 5)}
    by_id["3"] = {}
    pairs = {frozenset((a, b)) for a in (1, 2, 3) for b in (a + 1, 4)}
    pairs.add(frozenset((4, 5)))
    selected, refused = linker._compatible_groups(pairs, groups, by_id)
    assert selected == [[1, 2, 3], [4, 5]] and refused == 0
    for selected_group in selected:
        kinds = linker._record_kinds(by_id, [str(n) for n in selected_group])
        assert all(len(refs) <= 1 for refs in kinds.values())


def test_reproducible_for_different_pair_iteration_order():
    groups = [[str(n)] for n in range(1, 5)]
    edges = [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4)]
    a = linker._compatible_groups(set(map(frozenset, edges)), groups, {})
    b = linker._compatible_groups(set(map(frozenset, reversed(edges))), groups, {})
    assert a == b == ([[1, 2, 3]], 1)
