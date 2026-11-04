"""Switch-aware segmentation acceptance and refusal cases."""
import pytest
from server.reconstruction_switch_segments import switch_segments


def event(n, ref, second, *, org="org1"):
    return {"event_id": f"event-{n}", "event_type": "page_view", "source": "browser",
            "observed_at": f"2026-11-04T08:00:{second:02d}+00:00",
            "organization_id": org,
            "metadata": {"resource_reference": {
                "resource_ref": "owg:r:" + ref.lower() * 32,
                "provider": "github", "resource_kind": "issue",
                "host": "jira.example.com"}}}


def test_short_aba_produces_three_segments_and_observed_return():
    result = switch_segments([event(1, "A", 0), event(2, "B", 1),
                              event(3, "A", 2)], consent=True)
    assert result["total_segments"] == 3
    a, b, resumed_a = result["segments"]
    assert a["resource_ref"] == resumed_a["resource_ref"]
    assert b["resource_ref"] != a["resource_ref"]
    assert resumed_a["split_reason"] == "record_switch"
    assert resumed_a["observed_record_switch_return"] is True
    assert resumed_a["previous_same_record_segment_index"] == 1
    assert resumed_a["intervening_resource_refs"] == [b["resource_ref"]]
    assert result["canonical_membership_unchanged"] is True
    assert result["cross_record_links_inferred"] is False


def test_abc_never_reports_return():
    result = switch_segments([event(1, "A", 0), event(2, "B", 1),
                              event(3, "C", 2)], consent=True)
    assert len(result["segments"]) == 3
    assert not any(s["observed_record_switch_return"] for s in result["segments"])


def test_contiguous_same_record_remains_one_segment():
    result = switch_segments([event(1, "A", 0), event(2, "A", 1)], consent=True)
    assert result["total_segments"] == 1
    assert result["segments"][0]["observed_event_count"] == 2


def test_idle_gap_splits_without_inventing_intervening_record():
    events = [event(1, "A", 0), event(2, "A", 30)]
    result = switch_segments(events, consent=True, idle_seconds=10)
    assert result["total_segments"] == 2
    assert result["segments"][1]["split_reason"] == "idle_gap"
    assert result["segments"][1]["possible_resumption"] is True
    assert result["segments"][1]["observed_record_switch_return"] is False


def test_ties_duplicates_and_conflicting_scopes_abstain():
    tied = switch_segments([event(1, "A", 0), event(2, "B", 0)], consent=True)
    assert tied["total_segments"] == 0
    assert tied["ambiguous_timestamp_observations_suppressed"] == 2
    duplicated = switch_segments([event(1, "A", 0), event(1, "B", 1)], consent=True)
    assert duplicated["total_segments"] == 0
    assert duplicated["duplicate_event_ids_suppressed"] == 1
    conflicting = switch_segments([event(1, "A", 0), event(2, "A", 1, org="other")], consent=True)
    assert conflicting["total_segments"] == 0
    assert conflicting["conflicting_reference_groups_suppressed"] == 1


def test_consent_and_bounds_fail_closed():
    with pytest.raises(PermissionError):
        switch_segments([])
    with pytest.raises(ValueError):
        switch_segments([], consent=True, max_segments=0)
    with pytest.raises(ValueError):
        switch_segments([None], consent=True)
    with pytest.raises(ValueError):
        switch_segments([], consent=True, idle_seconds=True)
