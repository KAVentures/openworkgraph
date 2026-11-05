"""Source-component exclusivity regressions for review-only semantic matching."""
from server import reconstruction_semantic_assignment as assignment


def _queue(*pairs):
    return {"evidence_sha256": "a" * 64, "truncated": False,
            "total_candidates": len(pairs),
            "queue": [{"event_id": a, "toward_event_id": b,
                       "review_status": "inspect", "risk_reasons": []}
                      for a, b in pairs]}


def _run(monkeypatch, members, pairs):
    monkeypatch.setattr(assignment, "link_observations",
                        lambda events: {"members": members})
    monkeypatch.setattr(assignment, "prioritized_review_queue",
                        lambda events, limit: _queue(*[(a, b) for a, b, _ in pairs]))
    return assignment.semantic_assignment([], {
        "version": 1, "evidence_sha256": "a" * 64,
        "pairs": [{"event_id": a, "toward_event_id": b, "score": score}
                  for a, b, score in pairs]}, consent=True)


def test_same_source_component_cannot_be_assigned_twice(monkeypatch):
    result = _run(monkeypatch,
                  {"source": ["a1", "a2"], "target1": ["b"], "target2": ["c"],
                   "independent1": ["d"], "independent2": ["e"]},
                  [("a1", "b", .99), ("a2", "c", .85), ("d", "e", .90)])
    assert {(p["event_id"], p["toward_event_id"]) for p in result["proposals"]} == {
        ("a1", "b"), ("d", "e")}
    assert result["global_score_sum"] == 1.89
    assert result["canonical_membership_unchanged"] is True


def test_component_competitor_abstains_across_distinct_endpoints(monkeypatch):
    result = _run(monkeypatch,
                  {"source": ["a1", "a2"], "target1": ["b"], "target2": ["c"]},
                  [("a1", "b", .91), ("a2", "c", .90)])
    assert result["proposals"] == []
    assert result["abstained_ambiguous_endpoints"] == 1


def test_duplicate_endpoint_edges_do_not_fake_competition(monkeypatch):
    result = _run(monkeypatch,
                  {"source": ["a1", "a2"], "target": ["b1", "b2"]},
                  [("a1", "b1", .94), ("a2", "b2", .93)])
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["event_id"] == "a1"
    assert result["abstained_ambiguous_endpoints"] == 0
