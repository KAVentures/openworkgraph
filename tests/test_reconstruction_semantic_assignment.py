"""Algorithm and production-queue integration tests for semantic proposals."""
import pytest

from server import reconstruction_semantic_assignment as assignment


@pytest.fixture(autouse=True)
def singleton_components(monkeypatch):
    # Isolated score-manifest tests supply synthetic queue rows, not source events.
    monkeypatch.setattr(assignment, "link_observations", lambda events: {
        "members": {name: [name] for name in ("a", "b", "c", "d")}
    })


def queue_for(*pairs):
    return {
        "version": 1, "evidence_sha256": "a" * 64, "truncated": False,
        "total_candidates": len(pairs),
        "queue": [
            {"event_id": a, "toward_event_id": b,
             "review_status": "inspect", "risk_reasons": []}
            for a, b in pairs
        ],
    }


def manifest(*scores):
    return {
        "version": 1, "evidence_sha256": "a" * 64,
        "pairs": [{"event_id": a, "toward_event_id": b, "score": s}
                  for a, b, s in scores],
    }


def test_global_matching_beats_greedy(monkeypatch):
    # Greedy picks AB=.99 and CD=.80 (1.79); global picks AC+BD=1.80.
    monkeypatch.setattr(assignment, "prioritized_review_queue",
                        lambda events, limit: queue_for(("a", "b"), ("a", "c"),
                                                         ("b", "d"), ("c", "d")))
    scores = manifest(("a", "b", .99), ("a", "c", .90),
                      ("b", "d", .90), ("c", "d", .80))
    result = assignment.semantic_assignment([], scores, consent=True)
    assert {(r["event_id"], r["toward_event_id"]) for r in result["proposals"]} == {
        ("a", "c"), ("b", "d")}
    assert result["global_score_sum"] == 1.8
    assert result["canonical_membership_unchanged"] is True
    assert result["authoritative"] is False


def test_tie_abstains_and_does_not_force_assignment(monkeypatch):
    monkeypatch.setattr(assignment, "prioritized_review_queue",
                        lambda events, limit: queue_for(("a", "b"), ("a", "c")))
    result = assignment.semantic_assignment(
        [], manifest(("a", "b", .91), ("a", "c", .90)), consent=True)
    assert result["proposals"] == []
    assert result["abstained_ambiguous_endpoints"] == 1


def test_explicit_consent_digest_and_score_validation(monkeypatch):
    monkeypatch.setattr(assignment, "prioritized_review_queue",
                        lambda events, limit: queue_for(("a", "b")))
    valid = manifest(("a", "b", .91))
    with pytest.raises(ValueError, match="consent"):
        assignment.semantic_assignment([], valid)
    with pytest.raises(ValueError, match="digest"):
        assignment.semantic_assignment([], {**valid, "evidence_sha256": "b" * 64}, consent=True)
    for bad in (True, float("nan"), -1, 1.1, "0.9"):
        with pytest.raises(ValueError, match="score"):
            assignment.semantic_assignment([], manifest(("a", "b", bad)), consent=True)
    with pytest.raises(ValueError, match="duplicate"):
        assignment.semantic_assignment([], manifest(("a", "b", .9), ("b", "a", .9)), consent=True)


def test_refuses_unsafe_candidate_and_truncated_queue(monkeypatch):
    queue = queue_for(("a", "b"))
    queue["queue"][0]["review_status"] = "conflicted"
    monkeypatch.setattr(assignment, "prioritized_review_queue", lambda events, limit: queue)
    with pytest.raises(ValueError, match="eligible"):
        assignment.semantic_assignment([], manifest(("a", "b", .9)), consent=True)
    queue["truncated"] = True
    with pytest.raises(ValueError, match="truncated"):
        assignment.semantic_assignment([], manifest(), consent=True)


def test_actual_production_queue_digest_and_empty_candidates():
    from server.reconstruction_review_queue import prioritized_review_queue
    queue = prioritized_review_queue([])
    result = assignment.semantic_assignment(
        [], {"version": 1, "evidence_sha256": queue["evidence_sha256"], "pairs": []},
        consent=True)
    assert result["proposals"] == []
    assert result["evidence_sha256"] == queue["evidence_sha256"]


def test_competing_only_status_remains_eligible(monkeypatch):
    queue = queue_for(("a", "b"))
    queue["queue"][0]["review_status"] = "conflicted"
    queue["queue"][0]["risk_reasons"] = ["competing_candidate_components"]
    monkeypatch.setattr(assignment, "prioritized_review_queue", lambda events, limit: queue)
    result = assignment.semantic_assignment([], manifest(("a", "b", .93)), consent=True)
    assert [(r["event_id"], r["toward_event_id"]) for r in result["proposals"]] == [("a", "b")]


def test_conflicted_status_without_declared_safe_reason_is_refused(monkeypatch):
    queue = queue_for(("a", "b"))
    queue["queue"][0]["review_status"] = "conflicted"
    monkeypatch.setattr(assignment, "prioritized_review_queue", lambda events, limit: queue)
    with pytest.raises(ValueError, match="eligible"):
        assignment.semantic_assignment([], manifest(("a", "b", .93)), consent=True)


def test_subthreshold_distractor_does_not_veto_strong_match(monkeypatch):
    # A .79 distractor is below the minimum review-proposal confidence.
    # It must not veto an otherwise eligible .84 link via the .08 margin.
    monkeypatch.setattr(assignment, "prioritized_review_queue",
                        lambda events, limit: queue_for(("a", "b"), ("a", "c"), ("c", "d")))
    result = assignment.semantic_assignment(
        [], manifest(("a", "b", .84), ("a", "c", .79), ("c", "d", .94)), consent=True)
    assert {(p["event_id"], p["toward_event_id"]) for p in result["proposals"]} == {
        ("a", "b"), ("c", "d")}
    assert result["abstained_ambiguous_endpoints"] == 0
    assert result["requires_human_review"] is True


def test_above_floor_near_tie_still_abstains(monkeypatch):
    monkeypatch.setattr(assignment, "prioritized_review_queue",
                        lambda events, limit: queue_for(("a", "b"), ("a", "c")))
    result = assignment.semantic_assignment(
        [], manifest(("a", "b", .84), ("a", "c", .81)), consent=True)
    assert result["proposals"] == []
    assert result["abstained_ambiguous_endpoints"] == 1
