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


def test_review_digest_rejects_replay_across_changed_group_membership_or_truncation():
    """Current navigation groups are part of the reviewer's evidence basis.

    Direct event-pair labels can remain identical while a different set of
    singleton or transitive groups is visible in the inspection response.
    """
    import pytest
    from server.reconstruction_review_cli import score_bound_review_annotations

    source = _response("event-1")
    source["observed_case_groups"] = [{
        "group_id": "observed_group_01",
        "resource_pointers": [
            {"provider": "github", "resource_kind": "issue", "resource_ref": "A"},
            {"provider": "github", "resource_kind": "issue", "resource_ref": "B"},
        ],
        "proves_same_case": False,
    }]
    source["observed_case_groups_truncated"] = False
    bound = _envelope(source)
    altered_groups = [{**source["observed_case_groups"][0],
                       "resource_pointers": [
                           {"provider": "github", "resource_kind": "issue", "resource_ref": "A"}
                       ]}]
    for changed in (
        {**source, "observed_case_groups": altered_groups},
        {**source, "observed_case_groups_truncated": True},
    ):
        with pytest.raises(ValueError, match="digest mismatch"):
            score_bound_review_annotations(changed, bound)


def test_review_digest_rejects_replay_when_source_pair_metadata_changes():
    """A pair's event rows can be unchanged while its underlying evidence changes."""
    import copy
    import pytest
    from server.reconstruction_review_cli import score_bound_review_annotations

    source = _response("event-1")
    source["observed_pair_event_assignments"][0]["evidence_context"] = {
        "interruption_reason": "tab_switch",
        "confidence": "low",
    }
    bound = _envelope(source)
    changed = copy.deepcopy(source)
    changed["observed_pair_event_assignments"][0]["evidence_context"]["confidence"] = "high"
    assert build_review_queue(changed)["event_reviews"] == build_review_queue(source)["event_reviews"]
    with pytest.raises(ValueError, match="digest mismatch"):
        score_bound_review_annotations(changed, bound)


def test_review_digest_rejects_replay_when_visible_identity_timeline_changes():
    """A reviewer must not reuse labels after source identity context changes."""
    import copy
    import pytest
    from server.reconstruction_review_cli import score_bound_review_annotations

    source = _response("event-1")
    source["identity_timelines"] = [{
        "resource_ref": "A", "observed_event_ids": ["event-1"],
        "interruption_count": 0,
    }]
    bound = _envelope(source)
    changed = copy.deepcopy(source)
    changed["identity_timelines"][0]["interruption_count"] = 1
    assert build_review_queue(changed)["event_reviews"] == build_review_queue(source)["event_reviews"]
    with pytest.raises(ValueError, match="digest mismatch"):
        score_bound_review_annotations(changed, bound)


def test_review_digest_fails_closed_on_malformed_or_unbounded_visible_context():
    """Live inspection metadata must not crash digesting or exhaust the API."""
    import pytest
    for context, error in (
        ({"scan_truncated": False, "unexpected": float("nan")}, "invalid review evidence context"),
        ({"scan_truncated": False, "unexpected": object()}, "invalid review evidence context"),
        ({"scan_truncated": False, "unexpected": "x" * 1048577}, "unbounded review evidence context"),
    ):
        response = _response("event-1")
        response["coverage"] = context
        with pytest.raises(ValueError, match=error):
            build_review_queue(response)


def test_interleaved_unrelated_event_is_not_assigned_to_other_case_pair():
    """An ambiguous shared event is exposed, never promoted to a case merge."""
    from copy import deepcopy
    response = _response("event-shared")
    unrelated = deepcopy(response["observed_pair_event_assignments"][0])
    unrelated["resource_pointers"] = [
        {"provider": "github", "resource_kind": "issue", "resource_ref": "C"},
        {"provider": "github", "resource_kind": "issue", "resource_ref": "D"},
    ]
    unrelated["current_evidence_event_ids"] = ["event-shared", "event-cd-only"]
    response["observed_pair_event_assignments"].append(unrelated)
    review = build_review_queue(response)
    by_event = {row["event_id"]: row for row in review["event_reviews"]}
    assert review["ambiguous_event_reviews_count"] == 1
    assert review["single_pair_event_reviews_count"] == 1
    assert by_event["event-shared"]["review_status"] == "ambiguous_multiple_pairs"
    assert len(by_event["event-shared"]["candidate_resource_pairs"]) == 2
    assert by_event["event-cd-only"]["candidate_resource_pairs"] == [
        [["github", "issue", "C"], ["github", "issue", "D"]]
    ]
    assert all(not row["confirmed_case"] for row in review["event_reviews"])
    assert review["no_case_identity_inferred"] is True
