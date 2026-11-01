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
    assert result["link_contract"]["multiplexed_tab_focus_requires_global_two_sided_same_record_evidence"]
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
