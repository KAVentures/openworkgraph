"""Executable tests for exact-record interruption and interleaving segmentation."""
import pytest

from server.reconstruction_task_segments import task_segments


def event(identifier, minute, ref, *, org="org-a", session="s1", host="example.com"):
    return {
        "event_id": identifier,
        "observed_at": f"2026-11-04T00:{minute:02d}:00+00:00",
        "event_type": "page_view",
        "source": "browser",
        "organization_id": org,
        "session_id": session,
        "metadata": {"resource_reference": {
            "resource_ref": "owg:r:" + ref * 32,
            "provider": "github", "resource_kind": "issue", "host": host,
        }},
    }


def test_interleaved_record_segments_and_resumption():
    rows = [
        event("a1", 0, "a"), event("b1", 1, "b"),
        event("a2", 2, "a"), event("b2", 3, "b"),
        event("a3", 12, "a", session="s2"),
    ]
    result = task_segments(rows, consent=True, idle_seconds=180)
    a = [x for x in result["segments"] if x["resource_ref"].endswith("a" * 32)]
    b = [x for x in result["segments"] if x["resource_ref"].endswith("b" * 32)]
    assert [x["observed_event_count"] for x in a] == [2, 1]
    assert [x["observed_event_count"] for x in b] == [2]
    assert a[1]["possible_resumption"] is True
    assert a[1]["gap_from_previous_segment_seconds"] == 600
    assert a[0]["interleaved_with_other_records"] is True
    assert result["cross_record_links_inferred"] is False
    assert result["canonical_membership_unchanged"] is True


def test_conflicting_scope_suppresses_entire_reference():
    rows = [event("a1", 0, "a"), event("a2", 1, "a", org="other")]
    result = task_segments(rows, consent=True)
    assert result["segments"] == []
    assert result["conflicting_reference_groups_suppressed"] == 1


def test_duplicate_identity_suppresses_all_duplicate_events():
    rows = [event("same", 0, "a"), event("same", 1, "a"),
            event("unique", 2, "a")]
    result = task_segments(rows, consent=True)
    assert result["duplicate_event_ids_suppressed"] == 1
    assert result["segments"][0]["observed_event_count"] == 1


def test_missing_consent_and_invalid_bounds():
    with pytest.raises(PermissionError):
        task_segments([])
    with pytest.raises(ValueError):
        task_segments([], consent=True, idle_seconds=0)
    with pytest.raises(ValueError):
        task_segments([], consent=True, max_segments=501)


def test_input_order_independent_and_truncation_explicit():
    rows = [event("a1", 0, "a"), event("a2", 5, "a")]
    first = task_segments(rows, consent=True, idle_seconds=60, max_segments=1)
    second = task_segments(list(reversed(rows)), consent=True, idle_seconds=60, max_segments=1)
    assert first == second
    assert first["total_segments"] == 2
    assert first["truncated"] is True
