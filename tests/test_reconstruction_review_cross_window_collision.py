"""Adversarial regression: identical event IDs must not corroborate two sessions."""
import pytest

from server.reconstruction_review_cli import build_review_queue


def _pointer(ref):
    return {"provider": "github", "resource_kind": "issue", "resource_ref": ref}


def _response(earlier_id, current_id):
    return {
        "derived": True,
        "authoritative": False,
        "saved_history_access": "all_saved",
        "coverage": {"scan_truncated": False},
        "observed_pair_event_assignments": [{
            "assignment_status": "observed_same_event_pair",
            "proves_same_case": False,
            "resource_pointers": [_pointer("A"), _pointer("B")],
            "current_evidence_event_ids": [current_id],
            "earlier_corroborations": [{
                "session_started_at": "2026-11-12T10:00:00Z",
                "evidence_event_ids": [earlier_id],
            }],
        }],
    }


def test_reused_event_id_across_windows_is_rejected():
    with pytest.raises(ValueError, match="event ID reused across session windows"):
        build_review_queue(_response("same-id", "same-id"))


def test_distinct_event_ids_remain_distinct_windows():
    queue = build_review_queue(_response("earlier-id", "current-id"))
    assert queue["event_reviews_count"] == 2
    assert {row["window"] for row in queue["event_reviews"]} == {
        "current", "earlier:2026-11-12T10:00:00+00:00"
    }


def test_interleaved_cases_do_not_gain_transitive_event_assignments():
    """A-B and B-C on separate events must never imply an A-C event/case."""
    from server.reconstruction_review_cli import score_bound_review_annotations

    response = _response("earlier-ab", "current-ab")
    response["observed_pair_event_assignments"].append({
        "assignment_status": "observed_same_event_pair",
        "proves_same_case": False,
        "resource_pointers": [_pointer("B"), _pointer("C")],
        "current_evidence_event_ids": ["current-bc"],
        "earlier_corroborations": [],
    })
    queue = build_review_queue(response)
    rows = {row["event_id"]: row for row in queue["event_reviews"]}
    assert set(rows) == {"earlier-ab", "current-ab", "current-bc"}
    assert rows["current-ab"]["candidate_resource_pairs"] == [[
        ["github", "issue", "A"], ["github", "issue", "B"]
    ]]
    assert rows["current-bc"]["candidate_resource_pairs"] == [[
        ["github", "issue", "B"], ["github", "issue", "C"]
    ]]
    assert all(
        [["github", "issue", "A"], ["github", "issue", "C"]]
        not in row["candidate_resource_pairs"]
        for row in rows.values()
    )
    labels = [
        {"window": row["window"], "event_id": row["event_id"],
         "true_resource_pairs": row["candidate_resource_pairs"]}
        for row in queue["event_reviews"]
    ]
    result = score_bound_review_annotations(response, {
        "evidence_sha256": queue["evidence_sha256"], "annotations": labels
    })
    assert (result["true_positive_pairs"], result["false_positive_pairs"],
            result["false_negative_pairs"]) == (3, 0, 0)
    assert result["held_out_verified"] is False
    assert result["cross_case_contamination_measured"] is False


def test_interleaved_shared_event_is_explicitly_ambiguous_not_confirmed_case():
    response = _response("earlier-ab", "shared")
    response["observed_pair_event_assignments"].append({
        "assignment_status": "observed_same_event_pair",
        "proves_same_case": False,
        "resource_pointers": [_pointer("B"), _pointer("C")],
        "current_evidence_event_ids": ["shared"],
        "earlier_corroborations": [],
    })
    queue = build_review_queue(response)
    shared = next(row for row in queue["event_reviews"] if row["event_id"] == "shared")
    assert shared["review_status"] == "ambiguous_multiple_pairs"
    assert len(shared["candidate_resource_pairs"]) == 2
    assert shared["confirmed_case"] is False
    assert queue["no_case_identity_inferred"] is True
