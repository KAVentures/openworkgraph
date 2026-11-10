"""Coverage semantics for the review-only event assignment queue."""
from server.reconstruction_review_cli import build_review_queue


def _response(*, truncated=False):
    return {
        "derived": True,
        "authoritative": False,
        "saved_history_access": "all_saved",
        "coverage": {"scan_truncated": truncated},
        "observed_pair_event_assignments": [{
            "assignment_status": "observed_same_event_pair",
            "proves_same_case": False,
            "resource_pointers": [
                {"provider": "github", "resource_kind": "issue", "resource_ref": "A"},
                {"provider": "github", "resource_kind": "issue", "resource_ref": "B"},
            ],
            "current_evidence_event_ids": ["event-1"],
            "earlier_corroborations": [],
        }],
    }


def test_complete_review_can_identify_unlisted_events_as_unassigned():
    result = build_review_queue(_response())
    assert result["coverage_incomplete"] is False
    assert result["unlisted_events_are_unassigned"] is True
    assert result["event_reviews_count"] == 1


def test_truncated_scan_cannot_label_missing_events_unassigned():
    result = build_review_queue(_response(truncated=True))
    assert result["coverage_incomplete"] is True
    assert result["unlisted_events_are_unassigned"] is False
    assert result["event_reviews_count"] == 1


def test_pair_truncation_also_invalidates_negative_assignment_claims():
    response = _response()
    response["observed_pair_event_assignments"][0]["current_evidence_truncated"] = True
    result = build_review_queue(response)
    assert result["coverage_incomplete"] is True
    assert result["unlisted_events_are_unassigned"] is False
