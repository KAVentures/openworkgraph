"""Multi-signal work-item links with explicit uncertainty (roadmap #326 A1)."""
from __future__ import annotations

import json

from evals.reconstruction.fixtures import load_cases
from evals.reconstruction.owg_native import score_linked_items, score_record_lanes
from evals.reconstruction.prepare import split_cases
from server import workflow_evidence
from server.work_item_links import linked_work_items

A, B, C = ("owg:r:" + c * 32 for c in "abc")


def _event(n, ref=None, *, tab=None, transfer=None, app="Google Chrome", provider="jira",
           kind="issue", host="work.atlassian.net", action="browser_click"):
    metadata = {"target": {"label": "PRIVATE LABEL"}, "page": {"pathname": "/SECRET/path"}}
    if ref:
        metadata["resource_reference"] = {"resource_ref": ref, "provider": provider, "resource_kind": kind,
                                          "host": host, "resolver_locator": "SECRET-LOCATOR"}
    if tab:
        metadata["tab_context_id"] = tab
    if transfer:
        metadata["clipboard_transfer_id"] = transfer
    return {"event_id": f"e{n:02d}", "observed_at": f"2026-10-20T10:{n:02d}:00+00:00", "app": app,
            "event_type": action, "source": "browser_extension", "window_title": "PRIVATE TITLE",
            "target_label": "PRIVATE LABEL", "metadata": metadata}


def _items(result):
    return {tuple(item["event_ids"]): item for item in result["items"]}


def test_interleaved_records_stay_separate_and_name_their_basis():
    result = linked_work_items([_event(1, A), _event(2, B), _event(3, A), _event(4, B)])
    items = _items(result)
    assert set(items) == {("e01", "e03"), ("e02", "e04")}
    assert items[("e01", "e03")]["link_basis"] == {"same_record": 1}
    assert items[("e01", "e03")]["cross_record_join"] is False and items[("e01", "e03")]["confidence"] == 0.95
    assert result["candidate_links"] == []


def test_observed_clipboard_transfer_joins_two_records():
    result = linked_work_items([
        _event(1, A), _event(2, A, transfer="x-1", action="copy"),
        _event(3, B, provider="salesforce", kind="record", host="acme.salesforce.com", transfer="x-1", action="paste"),
        _event(4, C), _event(5, C),
    ])
    items = _items(result)
    joined = items[("e01", "e02", "e03")]
    assert joined["cross_record_join"] is True and joined["resource_refs"] == [A, B]
    assert joined["link_basis"] == {"clipboard_transfer": 1, "same_record": 1}
    assert joined["confidence"] == 0.85
    assert joined["record_kinds"] == ["jira:issue", "salesforce:record"]
    assert ("e04", "e05") in items


def test_tabs_merge_only_when_they_showed_a_single_record():
    single = linked_work_items([_event(1, A, tab="t-1"), _event(2, tab="t-1"), _event(3, A, tab="t-1")])
    assert _items(single)[("e01", "e02", "e03")]["link_basis"] == {"same_record": 1, "same_tab_single_record": 1}
    multiplexed = linked_work_items([_event(1, A, tab="t-1"), _event(2, B, tab="t-1"), _event(3, tab="t-1"),
                                     _event(4, A, tab="t-1"), _event(5, B, tab="t-1")])
    assert set(_items(multiplexed)) == {("e01", "e04"), ("e02", "e05")}
    [candidate] = multiplexed["candidate_links"]
    assert candidate["relation"] == "same_tab_multiplexed" and candidate["event_id"] == "e03"
    assert candidate["toward_event_id"] == "e02" and candidate["merged"] is False
    assert candidate["association_status"] == "ambiguous" and candidate["event_item_id"] is None
    unreferenced = linked_work_items([_event(1, tab="t-9"), _event(2, A), _event(3, tab="t-9"), _event(4, A)])
    assert set(_items(unreferenced)) == {("e02", "e04")}
    assert [c["relation"] for c in unreferenced["candidate_links"]] == ["same_tab_unreferenced"]


def test_app_continuity_is_a_candidate_and_time_alone_links_nothing():
    result = linked_work_items([_event(1, A, app="Excel"), _event(2, app="Excel"), _event(3, A, app="Excel")])
    assert set(_items(result)) == {("e01", "e03")}
    assert {(c["event_id"], c["toward_event_id"], c["relation"]) for c in result["candidate_links"]} == {
        ("e02", "e01", "surface_continuity"), ("e02", "e03", "surface_continuity")}
    alone = linked_work_items([_event(1, app="Excel"), _event(2, app="Outlook"), _event(3, app="Slack")])
    assert alone["items"] == [] and alone["candidate_links"] == [] and alone["unlinked_observation_count"] == 3
    assert alone["link_contract"]["time_alone_never_links"] is True


def test_conflicting_identities_and_bad_rows_fail_closed():
    conflicted = _event(3, A, provider="github")
    result = linked_work_items([_event(1, A), _event(2, B), conflicted, _event(4, B), _event(4, B),
                                {"event_id": "bad time", "observed_at": "nope"}, "not a row"])
    assert set(_items(result)) == {("e02", "e04")}
    assert result["conflicting_reference_groups_suppressed"] == 1


def test_nothing_private_is_returned():
    text = json.dumps(linked_work_items([_event(1, A, tab="t-1", transfer="x-1"), _event(2, tab="t-1"),
                                         _event(3, B, transfer="x-1"), _event(4, app="Excel")]))
    for leak in ("PRIVATE", "SECRET", "atlassian", "Google Chrome", "x-1", "t-1"):
        assert leak not in text


def test_workflow_evidence_carries_linked_items_additively():
    result = workflow_evidence.list_workflow_evidence_candidates(_raw_events=[_event(1, A), _event(2, A)])
    assert result["linked_work_items"]["items"][0]["event_ids"] == ["e01", "e02"]
    assert "record_case_lanes" in result


def _gate(report):
    return report["identifiable_membership_family_macro"], report["family_macro"]


def test_linked_items_beat_record_lanes_without_more_contamination():
    for split in ("all", "holdout"):
        cases = load_cases() if split == "all" else split_cases(load_cases(), split)
        lanes, lanes_all = _gate(score_record_lanes(cases))
        linked, linked_all = _gate(score_linked_items(cases))
        assert linked["workflow_assignment_f1"] > lanes["workflow_assignment_f1"], split
        assert linked["cross_workflow_contamination"] <= min(0.05, lanes["cross_workflow_contamination"])
        assert linked_all["cross_workflow_contamination"] <= lanes_all["cross_workflow_contamination"]
        assert linked["interruption_rejection"] >= 0.95 and linked_all["interruption_rejection"] >= 0.95
    # Regression floors (identifiable-membership family macro) on the committed corpus.
    linked, _ = _gate(score_linked_items(load_cases()))
    assert linked["workflow_assignment_f1"] >= 0.57


def test_accepting_ambiguous_candidates_would_break_the_contamination_gate():
    holdout = split_cases(load_cases(), "holdout")
    accepted, _ = _gate(score_linked_items(holdout, accept_candidates=True))
    linked, _ = _gate(score_linked_items(holdout))
    assert accepted["workflow_assignment_f1"] > linked["workflow_assignment_f1"]
    assert accepted["cross_workflow_contamination"] > 0.05 >= linked["cross_workflow_contamination"]
