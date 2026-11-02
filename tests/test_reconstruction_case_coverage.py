"""End-to-end evidence-only record/participant/surface coverage acceptance."""
from __future__ import annotations

import json

from server import workflow_evidence
from server.reconstruction_case_coverage import observed_record_participant_surfaces
from server.work_item_links import linked_work_items

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32


def event(n, ref=A, *, actor="alice-private", app="BrowserSecret",
          org="private-tenant", complete=True):
    metadata = {"resource_reference": {
        "resource_ref": ref, "provider": "jira", "resource_kind": "issue",
        "host": "private.atlassian.net",
    }, "target": {"label": "PRIVATE CUSTOMER"}}
    row = {
        "event_id": f"e{n:02d}",
        "observed_at": f"2026-11-01T10:{n:02d}:00+00:00",
        "app": app, "source": "desktop", "event_type": "click",
        "metadata": metadata, "window_title": "PRIVATE CUSTOMER",
    }
    if complete:
        row.update(organization_id=org, actor_id=actor,
                   device_id="private-device", session_id="private-session")
    return row


def item_for(result, event_id):
    return next(item for item in result["items"] if event_id in item["event_ids"])


def test_two_interleaved_cases_two_actors_two_apps_keep_separate_record_coverage():
    trace = [
        event(1, A), event(2, B, actor="bob-private"),
        event(3, A, actor="bob-private", app="TerminalSecret"),
        event(4, B, app="TerminalSecret"),
        event(5, A), event(6, B, actor="bob-private"),
    ]
    result = linked_work_items(trace)
    assert len(result["items"]) == 2
    a = item_for(result, "e01")
    b = item_for(result, "e02")
    assert set(a["event_ids"]) == {"e01", "e03", "e05"}
    assert set(b["event_ids"]) == {"e02", "e04", "e06"}
    for item in (a, b):
        assert item["observed_record_coverage_count"] == 1
        assert item["observed_multi_participant_record_count"] == 1
        record = item["observed_record_coverage"][0]
        assert record["distinct_observed_participants"] == 2
        assert record["distinct_observed_surfaces"] == 2
        assert record["participant_surface_pair_count"] == 2
        assert record["cross_actor_presence_observed"]
        assert not record["handoff_verified"]
        assert not record["business_outcome_verified"]
        assert all(not witness["continuous_activity_inferred"]
                   for witness in record["participant_surface_witnesses"])
    witnesses = a["observed_record_coverage"][0]["participant_surface_witnesses"]
    assert {(w["first_event_id"], w["last_event_id"]) for w in witnesses} == {
        ("e01", "e05"), ("e03", "e03"),
    }
    assert result["link_contract"]["record_participant_surface_coverage_does_not_add_membership"]
    assert result["link_contract"]["record_participant_surface_coverage_is_presence_not_productivity"]
    payload = json.dumps(result)
    for secret in ("alice-private", "bob-private", "private-tenant",
                   "private-device", "private-session", "BrowserSecret",
                   "TerminalSecret", "PRIVATE CUSTOMER", "atlassian.net"):
        assert secret not in payload


def test_incomplete_capture_scope_and_unknown_surface_excluded_without_erasing_valid_record():
    trace = [event(1), event(2, complete=False),
             event(3, app="unknown"), event(4, app="BrowserSecret")]
    result = linked_work_items(trace)
    item = item_for(result, "e01")
    assert item["observed_event_count"] == 4
    assert item["observed_record_coverage_count"] == 1
    assert item["incomplete_scope_record_observations_excluded"] == 1
    assert item["unknown_surface_record_observations_excluded"] == 1
    witnesses = item["observed_record_coverage"][0]["participant_surface_witnesses"]
    assert len(witnesses) == 1
    assert witnesses[0]["first_event_id"] == "e01"
    assert witnesses[0]["last_event_id"] == "e04"
    assert not item["observed_record_coverage_truncated"]


def test_cross_tenant_record_conflict_and_time_only_adjacency_never_create_coverage():
    conflicting = linked_work_items([
        event(1, A, org="tenant-one"), event(2, A, org="tenant-two"),
    ])
    assert conflicting["items"] == []
    assert conflicting["conflicting_reference_groups_suppressed"] == 1

    adjacent = linked_work_items([event(1, A), event(2, B)])
    assert adjacent["items"] == []
    assert adjacent["total_items"] == 0


def test_removing_actor_observations_recomputes_coverage_without_stale_participation():
    full = [event(1), event(2, actor="bob-private"), event(3)]
    assert item_for(linked_work_items(full), "e01")[
        "observed_multi_participant_record_count"] == 1
    retained = [full[0], full[2]]
    after = item_for(linked_work_items(retained), "e01")
    assert after["observed_multi_participant_record_count"] == 0
    record = after["observed_record_coverage"][0]
    assert record["distinct_observed_participants"] == 1
    assert record["participant_surface_pair_count"] == 1
    assert "e02" not in json.dumps(after)


def test_exact_counts_and_truncation_are_not_partial_coverage_claims():
    def row(n, ref="r", actor="actor", app="browser", org="tenant"):
        return {
            "event_id": f"e{n:02d}", "observed_at": f"2026-11-01T10:{n:02d}:00+00:00",
            "ref": ref, "reference_tainted": False,
            "scope": (org, actor, "device", "session"), "app": app,
        }

    many = [row(1, "r1"), row(2, "r2"), row(3, "r3")]
    capped = observed_record_participant_surfaces(many, max_records=2)
    assert capped["observed_record_coverage_count"] == 3
    assert len(capped["observed_record_coverage"]) == 2
    assert capped["observed_record_coverage_truncated"]

    pairs = [row(1, app="app1"), row(2, app="app2"), row(3, app="app3")]
    capped_pairs = observed_record_participant_surfaces(pairs, max_pairs_per_record=2)
    record = capped_pairs["observed_record_coverage"][0]
    assert record["participant_surface_pair_count"] == 3
    assert len(record["participant_surface_witnesses"]) == 2
    assert record["participant_surface_witnesses_truncated"]

    unsafe = observed_record_participant_surfaces([
        row(1, org="tenant-one"), row(2, org="tenant-two"),
    ])
    assert unsafe["observed_record_coverage_count"] == 0
    assert unsafe["cross_tenant_record_coverage_suppressed"] == 1


def test_existing_read_only_workflow_evidence_api_exposes_record_coverage():
    result = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[event(1), event(2, actor="bob-private"), event(3)],
    )
    linked = result["linked_work_items"]
    item = item_for(linked, "e01")
    assert item["observed_record_coverage_count"] == 1
    assert item["observed_multi_participant_record_count"] == 1
    assert linked["read_only"] and not linked["authoritative"]
    assert not linked["link_contract"]["linked_items_are_tasks"]
