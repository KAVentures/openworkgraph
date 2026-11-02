"""End-to-end conservative tab-focus recovery in multiplexed case traces."""
from __future__ import annotations

import json

from server import workflow_evidence
from server.work_item_links import linked_work_items

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32
C = "owg:r:" + "c" * 32


def event(n, ref=None, *, tab="shared", app="Browser", source="browser_extension",
          transfer=None, scope=True):
    metadata = {"target": {"label": "PRIVATE CUSTOMER"}, "page": {"pathname": "/PRIVATE"}}
    if ref is not None:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira", "resource_kind": "issue",
            "host": "private.atlassian.net", "resolver_locator": "PRIVATE",
        }
    if tab is not None:
        metadata["tab_context_id"] = tab
    if transfer is not None:
        metadata["clipboard_transfer_id"] = transfer
    row = {
        "event_id": f"e{n:02d}", "observed_at": f"2026-11-01T10:{n:02d}:00+00:00",
        "app": app, "source": source, "event_type": "browser_click",
        "window_title": "PRIVATE CUSTOMER", "metadata": metadata,
    }
    if scope:
        row.update(organization_id="tenant-a", actor_id="actor-a",
                   device_id="device-a", session_id="session-a")
    return row


def groups(result):
    return {tuple(item["event_ids"]): item for item in result["items"]}


def test_two_sided_focus_attaches_only_loose_observation_to_existing_record():
    trace = [event(1, A), event(2), event(3, A), event(4, B)]
    result = linked_work_items(trace)
    item = groups(result)[("e01", "e02", "e03")]
    assert item["link_basis"] == {"same_record": 1, "same_tab_bracketed_record": 1}
    assert item["confidence"] == 0.72
    assert item["cross_record_join"] is False
    assert result["observed_bracketed_tab_focus_links"] == 1
    assert not result["link_contract"]["multiplexed_tab_focus_requires_global_two_sided_same_record_evidence"]
    assert result["link_contract"]["multiplexed_tab_focus_requires_two_sided_same_tab_record_evidence"]
    assert result["unlinked_observation_count"] == 1
    assert "PRIVATE" not in json.dumps(result)
    assert "atlassian" not in json.dumps(result)


def test_multiple_consecutive_loose_steps_attach_without_bridging_other_record():
    trace = [event(1, A), event(2), event(3), event(4, A), event(5, B)]
    result = linked_work_items(trace)
    assert set(groups(result)) == {("e01", "e02", "e03", "e04")}
    assert result["observed_bracketed_tab_focus_links"] == 2
    assert groups(result)[("e01", "e02", "e03", "e04")]["link_basis"]["same_tab_bracketed_record"] == 2


def test_interleaved_foreign_case_and_nonmember_event_break_global_bracket():
    for foreign in (event(3, B), event(3, None, tab=None, app="Terminal")):
        trace = [event(1, A), event(2), foreign, event(4, A), event(5, B)]
        result = linked_work_items(trace)
        assert ("e01", "e02", "e04") not in groups(result)
        assert result["observed_bracketed_tab_focus_links"] == 0
        assert result["unlinked_observation_count"] >= 1


def test_bracketing_different_records_or_single_record_tab_never_recovers():
    differing = linked_work_items([event(1, A), event(2), event(3, B), event(4, A)])
    assert differing["observed_bracketed_tab_focus_links"] == 0
    assert ("e01", "e02", "e04") not in groups(differing)
    # Existing single-record tab behavior is unchanged; it uses its own basis.
    single = linked_work_items([event(1, A), event(2), event(3, A)])
    assert groups(single)[("e01", "e02", "e03")]["link_basis"] == {
        "same_record": 1, "same_tab_single_record": 1,
    }


def test_incomplete_scope_changed_surface_or_clock_tie_abstains():
    variants = [
        [event(1, A, scope=False), event(2, scope=False), event(3, A, scope=False), event(4, B, scope=False)],
        [event(1, A), event(2, app="Terminal"), event(3, A), event(4, B)],
        [event(1, A), event(2, source="desktop"), event(3, A), event(4, B)],
        [event(1, A), event(2), event(3, A, tab="other"), event(4, B)],
    ]
    tied = [event(1, A), event(2), event(3, A), event(4, B)]
    tied[1]["observed_at"] = tied[0]["observed_at"]
    variants.append(tied)
    for trace in variants:
        result = linked_work_items(trace)
        assert result["observed_bracketed_tab_focus_links"] == 0


def test_clipboard_claim_on_loose_event_cannot_gain_extra_tab_authority():
    trace = [event(1, A), event(2, transfer="x"), event(3, A),
             event(4, B, transfer="x")]
    result = linked_work_items(trace)
    assert result["observed_bracketed_tab_focus_links"] == 0
    assert ("e01", "e02", "e03", "e04") not in groups(result)


def test_existing_read_only_workflow_evidence_exposes_focus_without_new_endpoint():
    result = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[event(1, A), event(2), event(3, A), event(4, B)],
    )
    linked = result["linked_work_items"]
    assert linked["observed_bracketed_tab_focus_links"] == 1
    assert groups(linked)[("e01", "e02", "e03")]["association_status"] == "linked"
    assert linked["read_only"] and not linked["authoritative"]
    assert not linked["link_contract"]["linked_items_are_tasks"]


def test_bounded_focus_refuses_nine_unreferenced_steps():
    # The cap is a completeness/ambiguity guard, not a time threshold.
    trace = [event(1, A)] + [event(i) for i in range(2, 11)] + [
        event(11, A), event(12, B),
    ]
    result = linked_work_items(trace)
    assert result["observed_bracketed_tab_focus_links"] == 0
    assert ("e01", "e11") in groups(result)
    assert result["unlinked_observation_count"] == 10


def test_conflicting_tab_claim_vetoes_focus_without_erasing_same_record():
    trace = [event(1, A), event(2), event(3, A), event(4, B)]
    trace[1]["tab_context_id"] = "contradictory-tab"
    result = linked_work_items(trace)
    assert result["observed_bracketed_tab_focus_links"] == 0
    assert ("e01", "e03") in groups(result)
    assert result["unlinked_observation_count"] == 2


def test_interleaved_other_tab_recovers_same_record_without_merging_foreign_case():
    trace = [
        event(1, A), event(2), event(3, C, tab="other"),
        event(4, C, tab="other"), event(5, A), event(6, B),
    ]
    # The real workflow-evidence API is the public integration surface.
    response = workflow_evidence.list_workflow_evidence_candidates(_raw_events=trace)
    result = response["linked_work_items"]
    items = groups(result)
    assert set(items) == {("e01", "e02", "e05"), ("e03", "e04")}
    assert items[("e01", "e02", "e05")]["link_basis"] == {
        "same_record": 1, "same_tab_interleaved_bracketed_record": 1,
    }
    assert items[("e01", "e02", "e05")]["confidence"] == 0.68
    assert items[("e01", "e02", "e05")]["activity_segment_count"] == 2
    assert result["observed_bracketed_tab_focus_links"] == 1
    assert result["observed_interleaved_tab_focus_links"] == 1
    assert result["unlinked_observation_count"] == 1
    assert result["link_contract"]["interleaved_tab_focus_requires_explicit_distinct_tab"]
    assert result["link_contract"]["interleaved_tab_focus_has_bounded_foreign_and_loose_events"]
    assert result["read_only"] and not result["authoritative"]
    serialized = json.dumps(response)
    for secret in ("PRIVATE", "tenant-a", "actor-a", "device-a", "session-a", "atlassian"):
        assert secret not in serialized


def test_interleaved_tab_recovers_multiple_loose_steps_but_no_foreign_membership():
    result = linked_work_items([
        event(1, A), event(2), event(3, C, tab="other"),
        event(4), event(5, A), event(6, B),
    ])
    item = groups(result)[("e01", "e02", "e04", "e05")]
    assert item["link_basis"]["same_tab_interleaved_bracketed_record"] == 2
    assert result["observed_interleaved_tab_focus_links"] == 2
    assert result["unlinked_observation_count"] == 2
    assert not item["cross_record_join"]


def test_interleaved_tab_abstains_on_unknown_competing_or_untrusted_foreign_rows():
    foreign_rows = [
        event(3, C, tab=None),
        event(3, C, tab="shared"),
        event(3, C, tab="other", app="Different Browser"),
        event(3, C, tab="other", source="desktop_sensor"),
        event(3, C, tab="other", transfer="ambiguous"),
    ]
    wrong_tenant = event(3, C, tab="other")
    wrong_tenant["organization_id"] = "tenant-b"
    foreign_rows.append(wrong_tenant)
    conflicting_tab = event(3, C, tab="other")
    conflicting_tab["tab_context_id"] = "contradictory-tab"
    foreign_rows.append(conflicting_tab)
    malformed_action = event(3, C, tab="other")
    malformed_action["event_type"] = None
    foreign_rows.append(malformed_action)
    for foreign in foreign_rows:
        result = linked_work_items([event(1, A), event(2), foreign, event(4, A), event(5, B)])
        assert result["observed_interleaved_tab_focus_links"] == 0
        assert result["observed_bracketed_tab_focus_links"] == 0
        assert ("e01", "e02", "e04") not in groups(result)


def test_interleaved_tab_timestamp_ties_and_clipboard_anchors_abstain():
    for tie_to in (2, 4):
        trace = [event(1, A), event(2), event(3, C, tab="other"),
                 event(4, A), event(5, B)]
        trace[2]["observed_at"] = trace[tie_to - 1]["observed_at"]
        result = linked_work_items(trace)
        assert result["observed_interleaved_tab_focus_links"] == 0
    for transfer_at in (0, 3):
        trace = [event(1, A), event(2), event(3, C, tab="other"),
                 event(4, A), event(5, B)]
        trace[transfer_at]["metadata"]["clipboard_transfer_id"] = "x"
        result = linked_work_items(trace)
        assert result["observed_interleaved_tab_focus_links"] == 0


def test_interleaved_tab_refuses_unbounded_foreign_or_loose_span():
    too_many_foreign = ([event(1, A), event(2)]
                        + [event(i, C, tab="other") for i in range(3, 20)]
                        + [event(20, A), event(21, B)])
    assert linked_work_items(too_many_foreign)["observed_interleaved_tab_focus_links"] == 0
    too_many_loose = ([event(1, A)] + [event(i) for i in range(2, 11)]
                      + [event(11, C, tab="other"), event(12, A), event(13, B)])
    assert linked_work_items(too_many_loose)["observed_interleaved_tab_focus_links"] == 0


def test_interleaved_tab_recomputes_from_retained_events_not_stale_membership():
    trace = [event(1, A), event(2), event(3, C, tab="other"),
             event(4, A), event(5, B)]
    assert linked_work_items(trace)["observed_interleaved_tab_focus_links"] == 1
    retained = [row for row in trace if row["event_id"] != "e04"]
    result = linked_work_items(retained)
    assert result["observed_interleaved_tab_focus_links"] == 0
    assert ("e01", "e02") not in groups(result)
    assert result["unlinked_observation_count"] >= 2


def test_single_record_tab_foreign_case_without_return_abstains_instead_of_absorbing():
    # A's tab is nominally single-record, but a different scoped case B was
    # explicitly observed between A and the otherwise loose tab event.
    trace = [event(1, A), event(2, B, tab="other"), event(3)]
    result = linked_work_items(trace)
    assert result["total_items"] == 0
    assert result["unlinked_observation_count"] == 3
    assert result["ambiguous_single_record_tab_interleaving_abstentions"] == 1
    assert result["observed_single_record_interleaved_focus_links"] == 0
    assert result["link_contract"]["single_record_tab_foreign_case_requires_two_sided_focus"]


def test_single_record_tab_interleaved_return_recovers_only_witnessed_loose_step():
    trace = [event(1, A), event(2), event(3, B, tab="other"), event(4, A)]
    response = workflow_evidence.list_workflow_evidence_candidates(_raw_events=trace)
    result = response["linked_work_items"]
    item = groups(result)[("e01", "e02", "e04")]
    assert item["link_basis"] == {
        "same_record": 1, "same_tab_single_record_interleaved_bracketed": 1,
    }
    assert item["confidence"] == 0.66
    assert item["cross_record_join"] is False
    assert result["observed_single_record_interleaved_focus_links"] == 1
    assert result["ambiguous_single_record_tab_interleaving_abstentions"] == 0
    assert result["unlinked_observation_count"] == 1
    assert result["read_only"] and not result["authoritative"]
    serialized = json.dumps(response)
    for secret in ("PRIVATE", "tenant-a", "actor-a", "device-a", "session-a", "atlassian"):
        assert secret not in serialized


def test_single_record_tab_return_after_other_case_is_also_recoverable():
    result = linked_work_items([
        event(1, A), event(2, B, tab="other"), event(3), event(4, A),
    ])
    assert ("e01", "e03", "e04") in groups(result)
    assert result["observed_single_record_interleaved_focus_links"] == 1
    assert result["unlinked_observation_count"] == 1


def test_single_record_tab_foreign_case_from_other_tenant_is_not_a_veto():
    foreign = event(2, B, tab="other")
    foreign["organization_id"] = "tenant-b"
    result = linked_work_items([event(1, A), foreign, event(3)])
    assert ("e01", "e03") in groups(result)
    assert result["observed_single_record_interleaved_focus_links"] == 0
    assert result["ambiguous_single_record_tab_interleaving_abstentions"] == 0


def test_single_record_tab_refuses_unproven_or_ambiguous_return():
    cases = []
    missing_return = [event(1, A), event(2, B, tab="other"), event(3)]
    cases.append(missing_return)
    wrong_app = [event(1, A), event(2, B, tab="other"),
                 event(3, app="Terminal"), event(4, A)]
    cases.append(wrong_app)
    transfer = [event(1, A), event(2), event(3, B, tab="other"),
                event(4, A, transfer="transfer-1")]
    cases.append(transfer)
    tied = [event(1, A), event(2), event(3, B, tab="other"), event(4, A)]
    tied[3]["observed_at"] = tied[2]["observed_at"]
    cases.append(tied)
    too_long = ([event(1, A), event(2)]
                + [event(i, B, tab="other") for i in range(3, 20)]
                + [event(20, A)])
    cases.append(too_long)
    for trace in cases:
        result = linked_work_items(trace)
        assert result["observed_single_record_interleaved_focus_links"] == 0
        assert result["ambiguous_single_record_tab_interleaving_abstentions"] >= 1


def test_single_record_tab_two_independent_interleaved_returns_remain_separate():
    result = linked_work_items([
        event(1, A), event(2), event(3, B, tab="other"), event(4, A),
        event(5, B, tab="other"), event(6), event(7, A),
    ])
    item = groups(result)[("e01", "e02", "e04", "e06", "e07")]
    assert item["link_basis"]["same_tab_single_record_interleaved_bracketed"] == 2
    assert result["observed_single_record_interleaved_focus_links"] == 2
    assert ("e03", "e05") in groups(result)
    assert not groups(result)[("e03", "e05")]["cross_record_join"]
