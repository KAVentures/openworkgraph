"""Synthetic acceptance for observed source-record paths (roadmap #326 A)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from server import workflow_evidence
from server.work_item_links import linked_work_items

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32


def event(n: int, ref: str | None = A, *, action: str = "browser_click",
          transfer: str | None = None, tab: str | None = None,
          actor: str = "actor-a", session: str = "session-a") -> dict:
    metadata: dict = {}
    if ref is not None:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira",
            "resource_kind": "issue", "host": "work.atlassian.net",
        }
    result = {
        "event_id": f"e{n:04d}",
        "observed_at": (datetime(2026, 10, 20, tzinfo=timezone.utc)
                        + timedelta(minutes=n)).isoformat(),
        "event_type": action,
        "source": "browser_extension",
        "app": "Private Patient Portal",
        "metadata": metadata,
        "organization_id": "private-tenant",
        "actor_id": actor,
        "device_id": "device-a",
        "session_id": session,
        "window_title": "SECRET PRIVATE TITLE",
    }
    if transfer is not None:
        result["clipboard_transfer_id"] = transfer
    if tab is not None:
        result["tab_context_id"] = tab
    return result


def test_cross_record_path_requires_real_transfer_membership():
    linked = linked_work_items([
        event(1, A, action="copy", transfer="transfer-one"),
        event(2, B, action="paste", transfer="transfer-one"),
        event(3, B), event(4, A),
    ])
    assert linked["total_items"] == 1
    item = linked["items"][0]
    assert item["cross_record_join"] is True
    assert item["observed_record_run_count"] == 3
    assert item["observed_record_change_count"] == 2
    assert item["observed_record_scope_boundary_count"] == 0
    assert item["observed_unknown_record_observation_count"] == 0
    runs = item["observed_record_runs"]
    assert [r["observed_event_count"] for r in runs] == [1, 2, 1]
    assert [r["record_id"] for r in runs] == [
        "record_01", "record_02", "record_01",
    ]
    assert [r["boundary_before"] for r in runs] == [
        [], ["observed_record_change"], ["observed_record_change"],
    ]
    assert [r["first_event_id"] for r in runs] == ["e0001", "e0002", "e0004"]
    assert linked["link_contract"]["observed_record_paths_are_not_verified_business_transitions"]
    assert linked["link_contract"]["observed_record_changes_require_same_exact_capture_scope"]


def test_unknown_tab_member_is_visible_not_an_invented_record_change():
    linked = linked_work_items([
        event(1, A, tab="single-tab"),
        event(2, None, tab="single-tab"),
        event(3, A, tab="single-tab"),
    ])
    assert linked["total_items"] == 1
    item = linked["items"][0]
    assert item["observed_record_run_count"] == 3
    assert item["observed_record_change_count"] == 0
    assert item["observed_unknown_record_observation_count"] == 1
    assert [r["record_id"] for r in item["observed_record_runs"]] == [
        "record_01", None, "record_01",
    ]
    assert [r["boundary_before"] for r in item["observed_record_runs"]] == [
        [], ["unknown_record_boundary"], ["unknown_record_boundary"],
    ]
    assert linked["link_contract"]["unknown_record_runs_do_not_create_transitions"]


def test_scope_boundaries_are_not_cross_actor_record_transitions():
    linked = linked_work_items([
        event(1, A, action="copy", transfer="transfer-two"),
        event(2, A, actor="actor-b"),
        event(3, B, action="paste", transfer="transfer-two"),
        event(4, B),
    ])
    assert linked["total_items"] == 1
    item = linked["items"][0]
    assert item["observed_record_run_count"] == 3
    assert item["observed_record_change_count"] == 0
    assert item["observed_record_scope_boundary_count"] == 2
    assert [r["boundary_before"] for r in item["observed_record_runs"]] == [
        [], ["capture_scope_change_or_unverified"],
        ["capture_scope_change_or_unverified"],
    ]
    assert "private-tenant" not in json.dumps(item["observed_record_runs"])
    assert "actor-b" not in json.dumps(item["observed_record_runs"])


def test_bounded_paths_preserve_exact_counts_and_never_leak_raw_records():
    events = [
        event(1, A, action="copy", transfer="transfer-three"),
        event(2, B, action="paste", transfer="transfer-three"),
    ] + [event(n, A if n % 2 else B) for n in range(3, 43)]
    linked = linked_work_items(events)
    item = linked["items"][0]
    assert item["observed_record_run_count"] == 42
    assert item["observed_record_change_count"] == 41
    assert item["observed_record_runs_truncated"] is True
    assert len(item["observed_record_runs"]) == 24
    assert [r["run_index"] for r in item["observed_record_runs"]] == list(range(1, 25))
    serialized = json.dumps(item["observed_record_runs"])
    assert A not in serialized and B not in serialized
    assert "SECRET PRIVATE TITLE" not in serialized
    assert "Private Patient Portal" not in serialized


def test_no_time_only_link_or_transition_and_existing_api_is_read_only():
    separate = linked_work_items([
        event(1, A), event(2, B), event(3, A), event(4, B),
    ])
    assert separate["total_items"] == 2
    assert all(item["observed_record_change_count"] == 0
               for item in separate["items"])
    result = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[
            event(1, A, action="copy", transfer="transfer-four"),
            event(2, B, action="paste", transfer="transfer-four"),
            event(3, A),
        ],
    )
    linked = result["linked_work_items"]
    assert linked["items"][0]["observed_record_change_count"] == 2
    assert linked["read_only"] is True
    assert linked["authoritative"] is False
    assert "record_case_lanes" in result
