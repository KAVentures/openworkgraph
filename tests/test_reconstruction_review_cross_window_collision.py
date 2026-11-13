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
