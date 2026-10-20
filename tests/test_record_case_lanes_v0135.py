"""Case-lane tests: exact identity, interleaving, privacy and additive API."""
from __future__ import annotations

import json

from server.record_case_lanes import record_case_lanes

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32


def _event(n: int, ref: str = A, *, kind: str = "issue",
           provider: str = "jira", host: str = "work.atlassian.net",
           org: str = "org-1", actor: str = "person1", session: str = "s1",
           action: str = "browser_open") -> dict:
    return {
        "event_id": f"event-{n}", "organization_id": org,
        "actor_id": actor, "session_id": session,
        "source": "browser", "event_type": action,
        "observed_at": f"2026-10-20T10:00:{n:02d}+00:00",
        "window_title": "PRIVATE DOCUMENT TITLE",
        "metadata": {
            "resource_reference": {
                "resource_ref": ref, "provider": provider,
                "resource_kind": kind, "host": host,
                "resolver_locator": "SECRET-LOCATOR",
            },
            "private_field": "HIDDEN CUSTOMER NAME",
        },
    }


def test_interleaved_references_stay_in_separate_chronological_lanes():
    result = record_case_lanes([
        _event(1, A, action="browser_open"),
        _event(2, B, action="browser_open"),
        _event(3, A, actor="person2", session="s2", action="browser_submit"),
        _event(4, B, action="browser_submit"),
    ])
    assert result["total_lanes"] == 2
    lanes = {item["resource_ref"]: item for item in result["lanes"]}
    assert set(lanes) == {A, B}
    assert lanes[A]["observed_event_count"] == 2
    assert lanes[A]["observed_actor_count"] == 2
    assert lanes[A]["observed_session_count"] == 2
    assert [step["action"] for step in lanes[A]["observed_steps"]] == [
        "browser_open", "browser_submit",
    ]
    assert [step["action"] for step in lanes[B]["observed_steps"]] == [
        "browser_open", "browser_submit",
    ]
    assert lanes[A]["actor_handoff_verified"] is False
    assert lanes[A]["business_case_boundary_inferred"] is False
    assert lanes[A]["business_outcome_verified"] is False
    assert result["authoritative"] is False and result["read_only"] is True


def test_conflicting_provider_host_kind_or_organization_suppresses_entire_lane():
    for changed in (
        {"provider": "github"}, {"kind": "pull_request"},
        {"host": "other.atlassian.net"}, {"org": "org-2"},
        {"host": ""},
    ):
        output = record_case_lanes([_event(1), _event(2, **changed), _event(3)])
        assert output["lanes"] == []
        assert output["conflicting_reference_groups_suppressed"] == 1
    # Similar title and close timestamps cannot join two different references.
    output = record_case_lanes([_event(1, A), _event(2, B)])
    assert output["lanes"] == []


def test_dedup_bad_timestamp_invalid_identity_and_no_sensitive_content():
    items = [
        _event(1),
        _event(1),
        _event(2, action="<script>secret</script>"),
        _event(3, ref="ticket-1234"),
        {**_event(4), "observed_at": "not a date"},
    ]
    result = record_case_lanes(items)
    assert result["total_lanes"] == 1
    assert result["duplicate_event_ids_suppressed"] == 1
    assert result["invalid_timestamps_omitted"] == 1
    assert [s["action"] for s in result["lanes"][0]["observed_steps"]] == [
        "browser_open", "unknown",
    ]
    encoded = json.dumps(result)
    for secret in ("PRIVATE DOCUMENT TITLE", "HIDDEN CUSTOMER NAME",
                   "SECRET-LOCATOR", "event-1", "<script>"):
        assert secret not in encoded, secret


def test_bounded_lanes_and_step_truncation_without_erasing_counts():
    rows = [_event(n, action="browser_open") for n in range(1, 20)]
    rows += [_event(20, B), _event(21, B)]
    out = record_case_lanes(rows, max_lanes=1, max_steps=4)
    assert out["total_lanes"] == 2
    assert len(out["lanes"]) == 1
    lane = out["lanes"][0]
    assert lane["resource_ref"] == A
    assert lane["observed_event_count"] == 19
    assert len(lane["observed_steps"]) == 4
    assert lane["steps_truncated"] is True


def test_existing_workflow_discovery_returns_lanes_additively(monkeypatch):
    from server import workflow_evidence

    monkeypatch.setattr(workflow_evidence, "derive_executions", lambda _raw: [])
    result = workflow_evidence.list_workflow_evidence_candidates(_raw_events=[
        _event(1, A), _event(2, B), _event(3, A), _event(4, B),
    ])
    assert result["candidate_clusters"] == []
    assert result["families"] == []
    assert result["record_case_lanes"]["total_lanes"] == 2
    assert result["record_linked_activity"]["total_groups"] == 2
    assert result["returned_clusters_are_verified_tasks"] is False


def test_record_lane_ui_is_visible_even_without_legacy_repeated_card():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = (root / "dashboard/workflow_evidence.js").read_text(encoding="utf-8")
    dashboard = (root / "dashboard/index.html").read_text(encoding="utf-8")
    assert 'id="patternCard"' in dashboard
    assert "document.querySelector('#panel-overview #patternCard')" in script
    assert "workflowRecordCaseLanes" in script
    assert "payload.record_case_lanes" in script
    assert "renderRecordLanes()" in script
    assert "recordLaneCoverageLimited" in script
    assert "payload.families" in script  # retained existing workflow candidate path
