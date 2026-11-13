"""Evidence fingerprint regression tests for R1/R2 reviewer exports."""
from server.reconstruction_review_cli import build_review_queue


def _response(event_id):
    return {
        "derived": True, "authoritative": False, "saved_history_access": "all_saved",
        "coverage": {"scan_truncated": False},
        "observed_pair_event_assignments": [{
            "assignment_status": "observed_same_event_pair", "proves_same_case": False,
            "resource_pointers": [
                {"provider": "github", "resource_kind": "issue", "resource_ref": "A"},
                {"provider": "github", "resource_kind": "issue", "resource_ref": "B"},
            ],
            "current_evidence_event_ids": [event_id],
            "earlier_corroborations": [],
        }],
    }


def test_review_digest_stable_across_repeated_exports():
    first = build_review_queue(_response("event-1"))
    second = build_review_queue(_response("event-1"))
    assert first["evidence_sha256"] == second["evidence_sha256"]
    assert len(first["evidence_sha256"]) == 64


def test_review_digest_changes_when_evidence_changes():
    first = build_review_queue(_response("event-1"))
    second = build_review_queue(_response("event-2"))
    assert first["evidence_sha256"] != second["evidence_sha256"]
