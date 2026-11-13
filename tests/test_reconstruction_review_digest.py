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


def _envelope(response):
    queue = build_review_queue(response)
    row = queue["event_reviews"][0]
    return {
        "evidence_sha256": queue["evidence_sha256"],
        "annotations": [{
            "window": row["window"], "event_id": row["event_id"],
            "true_resource_pairs": row["candidate_resource_pairs"],
        }],
    }


def test_authorized_adapter_scores_bound_review_without_claiming_validation():
    from server.work_resumption import score_authorized_reconstruction_review
    response = _response("event-1")
    result = score_authorized_reconstruction_review(
        response, _envelope(response), authorize_review=lambda: True
    )
    assert result["true_positive_pairs"] == 1
    assert result["held_out_verified"] is False
    assert result["cross_case_contamination_measured"] is False


def test_authorized_adapter_denies_revoked_or_unverified_grants_before_scoring():
    import pytest
    from server.work_resumption import score_authorized_reconstruction_review
    response = _response("event-1")
    for authorization in (lambda: False, lambda: {"allowed": True}, None):
        with pytest.raises(PermissionError):
            score_authorized_reconstruction_review(
                response, {"invalid": "envelope"}, authorize_review=authorization
            )


def test_authorized_adapter_rejects_stale_evidence_after_live_authorization():
    import pytest
    from server.work_resumption import score_authorized_reconstruction_review
    response = _response("event-1")
    stale = _envelope(response)
    with pytest.raises(ValueError, match="digest mismatch"):
        score_authorized_reconstruction_review(
            _response("event-2"), stale, authorize_review=lambda: True
        )


def test_review_digest_rejects_replay_across_changed_scan_or_session_boundary():
    import pytest
    from server.reconstruction_review_cli import score_bound_review_annotations
    source = _response("event-1")
    source["current_session"] = {"started_at": "2026-11-13T10:00:00+00:00"}
    source["session_gap_minutes"] = 30
    bound = _envelope(source)
    for changed in (
        {**source, "coverage": {"scan_truncated": False, "activity_rows_scanned": 12}},
        {**source, "current_session": {"started_at": "2026-11-13T11:00:00+00:00"}},
        {**source, "session_gap_minutes": 45},
    ):
        with pytest.raises(ValueError, match="digest mismatch"):
            score_bound_review_annotations(changed, bound)
