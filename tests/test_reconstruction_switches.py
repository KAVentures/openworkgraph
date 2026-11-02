"""Synthetic integration acceptance for read-only observed work-item switches."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from server import workflow_evidence
from server.work_item_links import linked_work_items

A, B = ("owg:r:" + letter * 32 for letter in "ab")


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


def test_observed_switches_show_interleaved_return_and_unknown_coverage():
    result = linked_work_items([
        event(1, A), event(2, A), event(3, B), event(4, B),
        event(5), event(6, A), event(7, A),
    ])
    switches = result["observed_switches"]
    assert result["observed_switch_count"] == 2
    assert result["observed_return_count"] == 1
    assert result["observed_switches_with_unattributed_evidence_count"] == 1
    assert result["observed_switches_truncated"] is False
    assert [(s["from_event_id"], s["to_event_id"]) for s in switches] == [
        ("e0002", "e0003"), ("e0004", "e0006")]
    assert [s["return_to_previously_observed_item"] for s in switches] == [
        False, True]
    assert switches[0]["unattributed_observations_between"] == 0
    assert switches[0]["continuity_unverified"] is False
    assert switches[1]["unattributed_observations_between"] == 1
    assert switches[1]["continuity_unverified"] is True
    assert switches[0]["from_item_id"] == switches[1]["to_item_id"]
    assert switches[0]["to_item_id"] == switches[1]["from_item_id"]
    assert all("tenant" not in json.dumps(s) and "SECRET" not in json.dumps(s)
               and A not in json.dumps(s) and B not in json.dumps(s)
               for s in switches)
    assert result["link_contract"]["observed_switches_are_not_task_handoffs"]
    assert result["link_contract"]["observed_switches_require_same_scope_literal_record_endpoints"]


def test_other_tenant_actor_device_or_session_cannot_create_a_switch():
    for override in (
        {"org": "tenant-b"}, {"actor": "actor-b"},
        {"device": "device-b"}, {"session": "session-b"},
    ):
        result = linked_work_items([
            event(1, A), event(2, B, **override),
            event(3, A), event(4, B, **override),
        ])
        assert result["total_items"] == 2
        assert result["observed_switch_count"] == 0
        assert result["observed_return_count"] == 0


def test_unreferenced_candidate_and_time_gap_do_not_create_switches():
    first, second, third = event(1, A), event(2, A), event(3)
    third["observed_at"] = "2026-10-30T00:00:00+00:00"
    result = linked_work_items([first, second, third])
    assert result["observed_switch_count"] == 0
    assert result["observed_switches"] == []
    assert result["link_contract"]["time_alone_never_links"]


def test_unknown_same_scope_events_are_not_hidden_direct_adjacency():
    result = linked_work_items([
        event(1, A), event(2, A), event(3),
        event(4), event(5, B), event(6, B),
    ])
    assert result["observed_switch_count"] == 1
    switch = result["observed_switches"][0]
    assert switch["unattributed_observations_between"] == 2
    assert switch["continuity_unverified"] is True
    assert switch["from_event_id"] == "e0002"
    assert switch["to_event_id"] == "e0005"


def test_switches_are_bounded_but_counts_and_returns_remain_exact():
    result = linked_work_items([event(n, A if n % 2 else B)
                                for n in range(1, 73)])
    assert result["observed_switch_count"] == 71
    assert result["observed_return_count"] == 70
    assert result["observed_switches_with_unattributed_evidence_count"] == 0
    assert result["observed_switches_truncated"] is True
    assert len(result["observed_switches"]) == 32
    assert [s["switch_index"] for s in result["observed_switches"]] == list(range(1, 33))
    assert all(s["unattributed_observations_between"] == 0
               for s in result["observed_switches"])


def test_existing_workflow_evidence_api_includes_switches_without_new_permissions():
    result = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[event(1, A), event(2, B), event(3, A), event(4, B)]
    )
    linked = result["linked_work_items"]
    assert linked["observed_switch_count"] == 3
    assert linked["observed_return_count"] == 2
    assert linked["read_only"] is True
    assert linked["authoritative"] is False
    assert "record_case_lanes" in result
