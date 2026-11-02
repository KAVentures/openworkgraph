"""Synthetic integration acceptance for surface-aware reconstruction episodes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from server import workflow_evidence
from server.work_item_links import linked_work_items

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32


def event(n: int, *, ref: str = A, app: str | None = "Browser",
          actor: str = "actor-a", session: str = "session-a") -> dict:
    return {
        "event_id": f"e{n:04d}",
        "observed_at": (datetime(2026, 10, 20, tzinfo=timezone.utc)
                        + timedelta(minutes=n)).isoformat(),
        "event_type": "browser_click",
        "source": "browser_extension",
        "app": app,
        "metadata": {"resource_reference": {
            "resource_ref": ref, "provider": "jira",
            "resource_kind": "issue", "host": "work.atlassian.net",
        }},
        "organization_id": "tenant-a",
        "actor_id": actor,
        "device_id": "device-a",
        "session_id": session,
        "window_title": "PRIVATE PATIENT NAME",
    }


def test_observed_app_runs_distinguish_changes_and_unknown_surfaces():
    result = linked_work_items([
        event(1), event(2, app=" browser "), event(3, app="Terminal"),
        event(4, app=None), event(5), event(6),
    ])
    assert result["total_items"] == 1
    item = result["items"][0]
    runs = item["observed_surface_runs"]
    assert item["observed_surface_run_count"] == 4
    assert item["observed_app_identity_change_count"] == 1
    assert item["observed_unknown_surface_observation_count"] == 1
    assert item["observed_surface_scope_boundary_count"] == 0
    assert item["observed_surface_runs_truncated"] is False
    assert [run["observed_event_count"] for run in runs] == [2, 1, 1, 2]
    assert [run["boundary_before"] for run in runs] == [
        [], ["app_identity_change"], ["unknown_surface_boundary"],
        ["unknown_surface_boundary"],
    ]
    assert runs[0]["surface_id"] == runs[3]["surface_id"] == "surface_01"
    assert runs[1]["surface_id"] == "surface_02"
    assert runs[2]["surface_id"] is None
    assert runs[2]["surface_known"] is False
    assert runs[0]["first_event_id"] == "e0001"
    assert runs[0]["last_event_id"] == "e0002"
    assert all(run["capture_scope_verified"] for run in runs)
    assert result["link_contract"]["unknown_surfaces_are_not_other_app"]
    assert result["link_contract"]["observed_surface_runs_are_not_task_memberships"]


def test_capture_scope_changes_cannot_be_counted_as_app_switches():
    result = linked_work_items([
        event(1), event(2, app="Terminal"),
        event(3, app="Terminal", actor="actor-b"),
        event(4, app="Terminal", actor="actor-b"),
    ])
    item = result["items"][0]
    assert item["observed_surface_run_count"] == 3
    assert item["observed_app_identity_change_count"] == 1
    assert item["observed_surface_scope_boundary_count"] == 1
    assert item["observed_surface_runs"][2]["boundary_before"] == [
        "capture_scope_change_or_unverified"]
    assert result["link_contract"]["surface_transitions_require_verified_same_capture_scope"]


def test_missing_scope_never_claims_same_surface_continuity():
    events = [event(1), event(2), event(3)]
    events[1].pop("session_id")
    result = linked_work_items(events)
    item = result["items"][0]
    assert item["observed_surface_run_count"] == 3
    assert item["observed_app_identity_change_count"] == 0
    assert item["observed_surface_scope_boundary_count"] == 2
    assert item["observed_surface_runs"][1]["capture_scope_verified"] is False


def test_raw_app_names_and_private_window_titles_never_leave_surface_projection():
    events = [event(1, app="patient@example.com"),
              event(2, app="Private Customer Portal")]
    result = linked_work_items(events)
    item = result["items"][0]
    assert item["observed_surface_run_count"] == 2
    serialized = json.dumps(result)
    assert "patient@example.com" not in serialized
    assert "Private Customer Portal" not in serialized
    assert "PRIVATE PATIENT NAME" not in serialized
    assert item["observed_surface_runs"][0]["surface_id"] == "surface_01"


def test_surface_runs_bounded_with_exact_counts_and_no_time_based_joins():
    result = linked_work_items([
        event(n, app="Browser" if n % 2 else "Terminal")
        for n in range(1, 32)
    ])
    item = result["items"][0]
    assert item["observed_surface_run_count"] == 31
    assert item["observed_app_identity_change_count"] == 30
    assert item["observed_surface_runs_truncated"] is True
    assert len(item["observed_surface_runs"]) == 24
    assert [run["run_index"] for run in item["observed_surface_runs"]] == list(range(1, 25))
    separated = linked_work_items([
        event(1, ref=A), event(2, ref=B),
        event(3, ref=A), event(4, ref=B),
    ])
    assert separated["total_items"] == 2
    assert all(i["observed_surface_run_count"] == 1 for i in separated["items"])
    assert separated["link_contract"]["time_alone_never_links"]


def test_existing_workflow_evidence_api_exposes_surface_runs_read_only():
    result = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[event(1), event(2, app="Terminal"),
                     event(3, app=None), event(4)]
    )
    linked = result["linked_work_items"]
    assert linked["items"][0]["observed_surface_run_count"] == 4
    assert linked["items"][0]["observed_unknown_surface_observation_count"] == 1
    assert linked["read_only"] is True
    assert linked["authoritative"] is False
    assert "record_case_lanes" in result
