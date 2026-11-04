"""Observed switch boundaries must not invent cross-record membership."""
import pytest
from server.reconstruction_switch_boundaries import switch_boundaries


def event(n, ref, second, *, org="org1"):
    return {"event_id": f"event-{n}", "event_type": "page_view",
            "observed_at": f"2026-11-04T08:00:{second:02d}+00:00",
            "organization_id": org,
            "metadata": {"resource_reference": {
                "reference": ref, "provider": "jira", "kind": "issue",
                "host": "jira.example.com"}}}


def test_short_interruption_detected_without_idle_threshold():
    result = switch_boundaries([event(1, "A", 0), event(2, "B", 1),
                                event(3, "A", 2)], consent=True)
    assert len(result["boundaries"]) == 1
    boundary = result["boundaries"][0]
    assert boundary["elapsed_seconds"] == 2
    assert boundary["requires_review"] is True
    assert result["canonical_membership_unchanged"] is True
    assert result["cross_record_links_inferred"] is False


def test_unreturned_switch_does_not_propose_resumption():
    result = switch_boundaries([event(1, "A", 0), event(2, "B", 1),
                                event(3, "C", 2)], consent=True)
    assert result["boundaries"] == []


def test_same_timestamp_abstains():
    result = switch_boundaries([event(1, "A", 0), event(2, "B", 0),
                                event(3, "A", 1)], consent=True)
    assert result["boundaries"] == []
    assert result["ambiguous_timestamp_observations_suppressed"] == 2


def test_duplicate_event_id_suppresses_evidence():
    events = [event(1, "A", 0), event(2, "B", 1), event(1, "A", 2)]
    result = switch_boundaries(events, consent=True)
    assert result["boundaries"] == []
    assert result["duplicate_event_ids_suppressed"] == 1


def test_conflicting_scope_suppresses_reference():
    events = [event(1, "A", 0), event(2, "B", 1), event(3, "A", 2, org="other")]
    result = switch_boundaries(events, consent=True)
    assert result["boundaries"] == []
    assert result["conflicting_reference_groups_suppressed"] == 1


def test_consent_and_bounds_fail_closed():
    with pytest.raises(PermissionError):
        switch_boundaries([])
    with pytest.raises(ValueError):
        switch_boundaries([], consent=True, max_boundaries=0)
    with pytest.raises(ValueError):
        switch_boundaries([None], consent=True)
