"""End-to-end tabless desktop-app focus reconstruction and conservative abstention."""
from __future__ import annotations

import json

from server import workflow_evidence
from server.work_item_links import linked_work_items

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32


def event(n, ref=None, *, app="Terminal", source="desktop",
          tab=None, transfer=None, scope=True, action="desktop_action"):
    metadata = {"target": {"label": "PRIVATE CUSTOMER"},
                "page": {"pathname": "/PRIVATE"}}
    if ref is not None:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira",
            "resource_kind": "issue", "host": "private.atlassian.net",
            "resolver_locator": "PRIVATE",
        }
    if tab is not None:
        metadata["tab_context_id"] = tab
    if transfer is not None:
        metadata["clipboard_transfer_id"] = transfer
    row = {
        "event_id": f"e{n:02d}",
        "observed_at": f"2026-11-02T10:{n:02d}:00+00:00",
        "app": app, "source": source, "event_type": action,
        "window_title": "PRIVATE CUSTOMER", "metadata": metadata,
    }
    if scope:
        row.update(organization_id="tenant-a", actor_id="actor-a",
                   device_id="device-a", session_id="session-a")
    return row


def groups(result):
    return {tuple(item["event_ids"]): item for item in result["items"]}


def browser(n, ref=None):
    return event(n, ref, app="Browser", source="browser_extension", tab="browser-b")


def test_tabless_two_anchor_app_focus_recovers_existing_record_via_actual_api():
    response = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[event(1, A), event(2), event(3, A)],
    )
    linked = response["linked_work_items"]
    item = groups(linked)[("e01", "e02", "e03")]
    assert item["link_basis"] == {
        "same_record": 1, "same_app_bracketed_record": 1,
    }
    assert item["confidence"] == 0.64
    assert not item["cross_record_join"]
    assert linked["observed_bracketed_app_focus_links"] == 1
    assert linked["observed_cross_app_bracketed_app_focus_links"] == 0
    assert linked["unlinked_observation_count"] == 0
    assert linked["link_contract"]["tabless_app_focus_requires_two_sided_exact_record"]
    assert linked["link_contract"]["tabless_app_focus_requires_complete_scope"]
    assert linked["read_only"] and not linked["authoritative"]
    serialized = json.dumps(response)
    for secret in ("PRIVATE", "tenant-a", "actor-a", "device-a",
                   "session-a", "atlassian", "Terminal"):
        assert secret not in serialized


def test_cross_app_interleaving_recovers_only_same_app_steps_not_other_case():
    response = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[event(1, A), event(2), browser(3, B),
                     event(4), event(5, A), browser(6, B)],
    )
    result = response["linked_work_items"]
    items = groups(result)
    assert set(items) == {
        ("e01", "e02", "e04", "e05"), ("e03", "e06"),
    }
    a = items[("e01", "e02", "e04", "e05")]
    assert a["link_basis"] == {
        "same_record": 1, "same_app_cross_app_bracketed_record": 2,
    }
    assert a["confidence"] == 0.58
    assert a["activity_segment_count"] == 2
    assert not a["cross_record_join"]
    assert result["observed_bracketed_app_focus_links"] == 2
    assert result["observed_cross_app_bracketed_app_focus_links"] == 2
    assert result["link_contract"]["tabless_app_focus_cross_app_rows_are_negative_only"]


def test_no_return_and_same_app_competing_record_abstain():
    without_return = linked_work_items([event(1, A), browser(2, B), event(3)])
    assert without_return["observed_bracketed_app_focus_links"] == 0
    assert without_return["total_items"] == 0
    same_app_b = linked_work_items([
        event(1, A), event(2), event(3, B), event(4, A),
    ])
    assert same_app_b["observed_bracketed_app_focus_links"] == 0
    assert set(groups(same_app_b)) == {("e01", "e04")}
    assert same_app_b["unlinked_observation_count"] == 2


def test_incomplete_or_changed_capture_identity_never_grants_app_membership():
    for field in ("organization_id", "actor_id", "device_id", "session_id"):
        trace = [event(1, A), event(2), event(3, A)]
        trace[1][field] = "different"
        result = linked_work_items(trace)
        assert result["observed_bracketed_app_focus_links"] == 0, field
    trace = [event(1, A, scope=False), event(2, scope=False),
             event(3, A, scope=False)]
    assert linked_work_items(trace)["observed_bracketed_app_focus_links"] == 0
    trace = [event(1, A), event(2, source="different"), event(3, A)]
    assert linked_work_items(trace)["observed_bracketed_app_focus_links"] == 0


def test_unrelated_tenant_cannot_create_a_bridge_or_suppress_valid_bracket():
    foreign = browser(3, B)
    foreign["organization_id"] = "tenant-b"
    result = linked_work_items([
        event(1, A), event(2), foreign, event(4, A),
    ])
    assert ("e01", "e02", "e04") in groups(result)
    assert result["observed_bracketed_app_focus_links"] == 1
    assert result["observed_cross_app_bracketed_app_focus_links"] == 0
    assert result["unlinked_observation_count"] == 1


def test_unknown_tainted_clipboard_and_tab_evidence_fail_closed():
    variants = [
        event(3, None, app="unknown", source="desktop"),
        event(3, None, app="Other app", source="desktop"),
        event(3, None, app="Browser", source="unknown"),
        event(3, None, app="Browser", source="browser_extension"),
    ]
    for foreign in variants:
        result = linked_work_items([
            event(1, A), event(2), foreign, event(4, A),
        ])
        assert result["observed_bracketed_app_focus_links"] == 0
    for mutation in ("tab", "transfer", "malformed", "reference"):
        trace = [event(1, A), event(2), event(3, A)]
        if mutation == "tab":
            trace[1]["metadata"]["tab_context_id"] = "browser-tab"
        elif mutation == "transfer":
            trace[1]["metadata"]["clipboard_transfer_id"] = "transfer-x"
        elif mutation == "malformed":
            trace[1]["event_type"] = True
        else:
            trace[1]["metadata"]["resource_reference"] = True
        assert linked_work_items(trace)["observed_bracketed_app_focus_links"] == 0, mutation


def test_timestamp_ties_and_clipboard_anchors_abstain():
    for tie in (1, 3):
        trace = [event(1, A), event(2), event(3, A)]
        trace[1]["observed_at"] = trace[tie - 1]["observed_at"]
        assert linked_work_items(trace)["observed_bracketed_app_focus_links"] == 0
    for position in (0, 2):
        trace = [event(1, A), event(2), event(3, A)]
        trace[position]["event_type"] = "copy"
        assert linked_work_items(trace)["observed_bracketed_app_focus_links"] == 0


def test_span_bounds_and_revocation_recompute_without_stale_membership():
    nine_loose = [event(1, A)] + [event(i) for i in range(2, 11)] + [event(11, A)]
    assert linked_work_items(nine_loose)["observed_bracketed_app_focus_links"] == 0
    seventeen_foreign = ([event(1, A), event(2)]
                         + [browser(i, B) for i in range(3, 20)]
                         + [event(20, A)])
    assert linked_work_items(seventeen_foreign)["observed_bracketed_app_focus_links"] == 0
    trace = [event(1, A), event(2), browser(3, B),
             event(4), event(5, A)]
    assert linked_work_items(trace)["observed_bracketed_app_focus_links"] == 2
    retained = [row for row in trace if row["event_id"] != "e05"]
    after = linked_work_items(retained)
    assert after["observed_bracketed_app_focus_links"] == 0
    assert "e02" not in {id for item in after["items"] for id in item["event_ids"]}


def test_competing_transfer_component_cannot_be_laundered_into_app_focus():
    # A transfer-bearing loose event may independently belong to B, but
    # must not be attributed to A merely because Terminal resumes A.
    trace = [event(1, A), event(2, transfer="x"),
             browser(3, B), event(4, A)]
    result = linked_work_items(trace)
    assert result["observed_bracketed_app_focus_links"] == 0
    assert ("e01", "e02", "e04") not in groups(result)
