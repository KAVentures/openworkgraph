"""End-to-end observed activity episodes on the real work-item linker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from server import workflow_evidence
from server.work_item_links import linked_work_items

A, B, C = ("owg:r:" + letter * 32 for letter in "abc")


def event(n: int, ref: str | None = None, *, org: str = "tenant-a",
          actor: str = "actor-a", device: str = "device-a",
          session: str = "session-a") -> dict:
    metadata = {}
    if ref is not None:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira",
            "resource_kind": "issue", "host": "work.atlassian.net",
        }
    return {
        "event_id": f"e{n:04d}",
        "observed_at": (datetime(2026, 10, 20, tzinfo=timezone.utc)
                        + timedelta(minutes=n)).isoformat(),
        "event_type": "browser_click",
        "source": "browser_extension",
        "app": "Browser",
        "metadata": metadata,
        "organization_id": org,
        "actor_id": actor,
        "device_id": device,
        "session_id": session,
        "window_title": "SECRET PRIVATE",
    }


def item(result: dict, ref: str) -> dict:
    return next(entry for entry in result["items"] if ref in entry["resource_refs"])


def test_interleaved_cases_have_distinct_observed_episodes_not_time_joins():
    result = linked_work_items([event(1, A), event(2, B), event(3, A),
                                event(4, B), event(5, A)])
    a, b = item(result, A), item(result, B)
    assert a["event_ids"] == ["e0001", "e0003", "e0005"]
    assert b["event_ids"] == ["e0002", "e0004"]
    assert a["activity_segment_count"] == 3
    assert b["activity_segment_count"] == 2
    assert [s["boundary_before"] for s in a["activity_segments"]] == [
        [], ["interleaved_other_record"], ["interleaved_other_record"]]
    assert [s["event_ids"] for s in a["activity_segments"]] == [
        ["e0001"], ["e0003"], ["e0005"]]
    assert result["link_contract"]["activity_segments_are_observed_episodes_not_tasks"]
    assert result["link_contract"]["time_alone_never_links"]


def test_unreferenced_or_other_tenant_activity_does_not_claim_an_interruption():
    result = linked_work_items([event(1, A), event(2), event(3, A),
                                event(4, B, org="tenant-b"), event(5, A)])
    a = item(result, A)
    assert a["activity_segment_count"] == 1
    assert a["activity_segments"][0]["event_ids"] == ["e0001", "e0003", "e0005"]
    assert "tenant-b" not in json.dumps(result)
    assert "SECRET" not in json.dumps(result)


def test_same_record_across_capture_sessions_is_linked_but_episode_split():
    result = linked_work_items([
        event(1, A, session="morning"),
        event(2, B, session="other"),
        event(3, A, session="afternoon"),
    ])
    a = item(result, A)
    assert a["activity_segment_count"] == 2
    assert a["activity_segments"][1]["boundary_before"] == ["capture_scope_change"]
    assert a["link_basis"] == {"same_record": 1}
    assert "morning" not in json.dumps(result)
    assert "afternoon" not in json.dumps(result)


def test_clock_gap_alone_never_proves_interruption():
    first, second = event(1, A), event(2, A)
    second["observed_at"] = "2026-10-27T10:00:00+00:00"
    result = linked_work_items([first, second])
    a = item(result, A)
    assert a["activity_segment_count"] == 1
    assert a["activity_segments"][0]["observed_event_count"] == 2
    assert result["link_contract"]["clock_gaps_alone_do_not_split_activity_segments"]


def test_foreign_record_only_interrupts_within_exact_capture_scope():
    result = linked_work_items([
        event(1, A), event(2, B, actor="actor-b"),
        event(3, A), event(4, C, device="device-b"), event(5, A),
        event(6, B), event(7, A),
    ])
    a = item(result, A)
    assert a["activity_segment_count"] == 2
    assert [s["boundary_before"] for s in a["activity_segments"]] == [
        [], ["interleaved_other_record"]]
    assert a["activity_segments"][0]["event_ids"] == ["e0001", "e0003", "e0005"]
    assert a["activity_segments"][1]["event_ids"] == ["e0007"]


def test_segment_counts_and_truncation_are_exact_for_long_interleaving():
    events = []
    for n in range(1, 82):
        events.append(event(n, A if n % 2 else B))
    result = linked_work_items(events)
    a = item(result, A)
    assert a["observed_event_count"] == 41
    assert a["activity_segment_count"] == 41
    assert a["activity_segments_truncated"] is True
    assert len(a["activity_segments"]) == 16
    assert all(s["observed_event_count"] == 1 for s in a["activity_segments"])
    assert all(s["event_ids_truncated"] is False for s in a["activity_segments"])


def test_existing_workflow_evidence_api_carries_additive_segments():
    result = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[event(1, A), event(2, B), event(3, A)]
    )
    linked = result["linked_work_items"]
    assert item(linked, A)["activity_segment_count"] == 2
    assert "record_case_lanes" in result
