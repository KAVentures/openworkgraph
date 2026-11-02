"""Synthetic integration checks for read-only assignment explanations (#326 A, #370 R1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from server import workflow_evidence
from server.reconstruction_assignment_proof import accepted_assignment_proofs
from server.work_item_links import linked_work_items

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32


def event(n: int, ref: str | None, *, tab: str | None = None,
          transfer: str | None = None, action: str = "browser_click",
          actor: str = "actor-a", tenant: str = "secret-tenant") -> dict:
    metadata = {}
    if ref:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira",
            "resource_kind": "issue", "host": "private.atlassian.net",
        }
    row = {
        "event_id": f"e{n:04d}",
        "observed_at": (datetime(2026, 10, 20, tzinfo=timezone.utc)
                        + timedelta(minutes=n)).isoformat(),
        "metadata": metadata,
        "event_type": action, "source": "browser_extension",
        "app": "PRIVATE SECRET APP", "window_title": "SECRET PATIENT",
        "organization_id": tenant, "actor_id": actor,
        "device_id": "secret-device", "session_id": "secret-session",
    }
    if tab is not None:
        row["tab_context_id"] = tab
    if transfer is not None:
        row["clipboard_transfer_id"] = transfer
    return row


def test_tab_recovered_action_has_shortest_accepted_evidence_path_and_api_parity():
    events = [event(1, A, tab="tab-a"), event(2, None, tab="tab-a"),
              event(3, A, tab="tab-a")]
    for source in (events, list(reversed(events))):
        [item] = linked_work_items(source)["items"]
        assert item["unreferenced_member_count"] == 1
        assert item["record_anchored_unreferenced_member_count"] == 1
        assert item["unanchored_unreferenced_member_count"] == 0
        [proof] = item["assignment_proofs"]
        assert proof["event_id"] == "e0002"
        assert proof["status"] == "anchored_by_accepted_links"
        assert proof["anchor_event_id"] in ("e0001", "e0003")
        assert proof["evidence_event_path"][0] == "e0002"
        assert proof["evidence_event_path"][-1] == proof["anchor_event_id"]
        assert proof["accepted_link_basis_path"] == ["same_tab_single_record"]
        via_api = workflow_evidence.list_workflow_evidence_candidates(_raw_events=source)
        assert via_api["linked_work_items"]["items"][0]["assignment_proofs"] == [proof]
        assert via_api["linked_work_items"]["link_contract"][
            "assignment_proofs_do_not_add_membership_or_verify_case_identity"
        ]
        exposed = json.dumps(via_api["linked_work_items"])
        for secret in ("PRIVATE", "SECRET", "secret-tenant", "secret-device",
                       "private.atlassian.net", "tab-a"):
            assert secret not in exposed


def test_cross_record_transfer_does_not_claim_unique_business_case():
    events = [event(1, A, transfer="copy-1", action="copy"),
              event(2, B, transfer="copy-1", action="paste"),
              event(3, None, tab="tab-b"),
              event(4, B, tab="tab-b")]
    result = linked_work_items(events)
    assert result["total_items"] == 1
    [item] = result["items"]
    assert item["multiple_exact_record_anchors_present"] is True
    assert item["cross_record_join"] is True
    [proof] = item["assignment_proofs"]
    assert proof["event_id"] == "e0003"
    assert proof["status"] == "anchored_by_accepted_links"
    assert proof["anchor_event_id"] == "e0004"
    assert proof["accepted_link_basis_path"] == ["same_tab_single_record"]
    assert item["task_identity_inferred"] is False
    assert item["business_outcome_verified"] is False


def test_unanchored_generic_transfer_is_not_presented_as_record_evidence():
    events = [event(1, None, transfer="legacy"), event(2, None, transfer="legacy")]
    [item] = linked_work_items(events)["items"]
    assert item["unreferenced_member_count"] == 2
    assert item["record_anchored_unreferenced_member_count"] == 0
    assert item["unanchored_unreferenced_member_count"] == 2
    assert all(p["status"] == "no_exact_record_anchor" for p in item["assignment_proofs"])
    assert all(p["anchor_event_id"] is None for p in item["assignment_proofs"])


def test_foreign_tenant_does_not_become_assignment_anchor():
    events = [event(1, A, tab="shared"), event(2, None, tab="shared"),
              event(3, B, tab="shared", tenant="other-tenant")]
    result = linked_work_items(events)
    assert result["total_items"] == 1
    [item] = result["items"]
    assert item["multiple_exact_record_anchors_present"] is False
    assert item["resource_refs"] == [A]
    assert item["assignment_proofs"][0]["anchor_event_id"] == "e0001"


def test_proof_display_is_bounded_but_counts_cover_all_members():
    events = [event(1, A, tab="tab-many")] + [
        event(n, None, tab="tab-many") for n in range(2, 42)
    ]
    [item] = linked_work_items(events)["items"]
    assert item["unreferenced_member_count"] == 40
    assert item["record_anchored_unreferenced_member_count"] == 40
    assert len(item["assignment_proofs"]) == 24
    assert item["assignment_proofs_truncated"] is True
    assert all(p["status"] == "anchored_by_accepted_links" for p in item["assignment_proofs"])


def test_long_paths_and_hidden_ids_do_not_expose_partial_proof():
    rows = [{"event_id": f"e{i}", "ref": A if i == 0 else "",
             "observed_at": str(i)} for i in range(12)]
    edges = [(f"e{i}", f"e{i+1}", "clipboard_transfer") for i in range(11)]
    result = accepted_assignment_proofs(rows, edges, visible_event_ids=[f"e{i}" for i in range(12)])
    by_id = {p["event_id"]: p for p in result["assignment_proofs"]}
    assert by_id["e9"]["status"] == "path_exceeds_display_hop_limit"
    assert by_id["e9"]["evidence_event_path"] == []
    assert by_id["e2"]["evidence_event_path"] == ["e2", "e1", "e0"]
    hidden = accepted_assignment_proofs(rows, edges, visible_event_ids=["e1", "e2"])
    assert hidden["assignment_proofs"][0]["status"] == "path_outside_visible_event_window"
    assert hidden["assignment_proofs"][0]["anchor_event_id"] is None
    assert hidden["record_anchored_unreferenced_member_count"] == 11


def test_retention_removes_source_anchor_without_fabricating_one():
    before = [event(1, A, transfer="legacy"), event(2, None, transfer="legacy"),
              event(3, None, transfer="legacy")]
    [anchored] = linked_work_items(before)["items"]
    assert anchored["record_anchored_unreferenced_member_count"] == 2
    after = before[1:]  # deletion/revocation projection, no retained anchor
    [unanchored] = linked_work_items(after)["items"]
    assert unanchored["unanchored_unreferenced_member_count"] == 2
    assert all(p["status"] == "no_exact_record_anchor" for p in unanchored["assignment_proofs"])


# Ambiguous-assignment review queue: reviewable weak evidence, never a join.
from server.reconstruction_candidate_review import ambiguous_assignment_review


def test_review_queue_groups_real_multiplexed_tab_candidate_without_linking():
    events = [
        event(1, A, tab="shared"), event(2, A, tab="shared"),
        event(3, B, tab="shared"), event(4, B, tab="shared"),
        event(5, None, tab="shared"),
    ]
    result = linked_work_items(events)
    assert result["total_items"] == 2
    assert result["unlinked_observation_count"] == 1
    assert result["ambiguous_assignment_review_total"] == 1
    assert result["ambiguous_assignment_review_competing_count"] == 0
    [review] = result["ambiguous_assignment_review_queue"]
    assert review["event_id"] == "e0005"
    assert review["review_priority"] == "weak_evidence_only"
    assert review["auto_assigned"] is False
    assert review["requires_human_review"] is True
    assert review["candidate_item_count"] == 1
    [option] = review["candidate_items"]
    assert option["weak_relations"] == ["same_tab_multiplexed"]
    assert option["distinct_candidate_edge_count"] == 1
    assert option["item_id"] == next(
        item["item_id"] for item in result["items"] if B in item["resource_refs"]
    )
    assert all("e0005" not in item["event_ids"] for item in result["items"])
    via_api = workflow_evidence.list_workflow_evidence_candidates(_raw_events=events)
    assert via_api["linked_work_items"]["ambiguous_assignment_review_queue"] == [review]
    serialized = json.dumps(via_api["linked_work_items"])
    for secret in ("PRIVATE", "SECRET", "secret-tenant", "secret-device",
                   "private.atlassian.net", "shared"):
        assert secret not in serialized


def test_review_queue_recomputes_after_retention_without_stale_candidates():
    events = [
        event(1, A, tab="shared"), event(2, A, tab="shared"),
        event(3, B, tab="shared"), event(4, B, tab="shared"),
        event(5, None, tab="shared"),
    ]
    before = linked_work_items(events)
    assert before["ambiguous_assignment_review_total"] == 1
    # Revocation/removal of B's independent anchors changes the evidence.
    after = linked_work_items([events[0], events[1], events[4]])
    assert after["ambiguous_assignment_review_total"] == 0
    assert after["ambiguous_assignment_review_queue"] == []
    assert "e0005" in after["items"][0]["event_ids"]


def test_review_queue_competing_targets_dedupe_and_direction_are_deterministic():
    edges = [
        {"event_id": "loose", "toward_event_id": "a1",
         "relation": "same_tab_multiplexed"},
        {"event_id": "loose", "toward_event_id": "a1",
         "relation": "same_tab_multiplexed"},
        {"event_id": "b1", "toward_event_id": "loose",
         "relation": "surface_continuity"},
        {"event_id": "loose", "toward_event_id": "b2",
         "relation": "surface_continuity"},
        {"event_id": "loose", "toward_event_id": "missing",
         "relation": "same_tab_multiplexed"},
        {"event_id": "loose", "toward_event_id": "a1",
         "relation": "accepted_same_record"},
        {"event_id": "loose", "toward_event_id": "loose",
         "relation": "same_tab_multiplexed"},
        {"event_id": "loose", "toward_event_id": "a1",
         "relation": ["malformed", "unhashable"]},
    ]
    kwargs = dict(
        item_of={"a1": "linked_item_01", "b1": "linked_item_02",
                 "b2": "linked_item_02"},
        singleton_ids={"loose", "missing"},
        anchored_item_ids={"linked_item_01", "linked_item_02"},
    )
    first = ambiguous_assignment_review(edges, **kwargs)
    assert first == ambiguous_assignment_review(list(reversed(edges)), **kwargs)
    assert first["ambiguous_assignment_review_total"] == 1
    assert first["ambiguous_assignment_review_competing_count"] == 1
    [review] = first["ambiguous_assignment_review_queue"]
    assert review["review_priority"] == "competing_visible_items"
    assert [item["item_id"] for item in review["candidate_items"]] == [
        "linked_item_02", "linked_item_01",
    ]
    assert [item["distinct_candidate_edge_count"] for item in review["candidate_items"]] == [2, 1]
    assert review["candidate_item_count"] == 2


def test_review_queue_never_discloses_hidden_or_unanchored_linked_components():
    edges = [
        {"event_id": "loose", "toward_event_id": "visible",
         "relation": "same_tab_multiplexed"},
        {"event_id": "loose", "toward_event_id": "hidden",
         "relation": "same_tab_multiplexed"},
        {"event_id": "loose", "toward_event_id": "unanchored",
         "relation": "surface_continuity"},
        {"event_id": "hidden", "toward_event_id": "visible",
         "relation": "surface_continuity"},
    ]
    result = ambiguous_assignment_review(
        edges, {"visible": "linked_item_01", "unanchored": "linked_item_02"},
        {"loose"}, {"linked_item_01"},
    )
    assert result["ambiguous_assignment_review_total"] == 1
    [review] = result["ambiguous_assignment_review_queue"]
    assert review["candidate_item_count"] == 1
    assert review["candidate_items"][0]["item_id"] == "linked_item_01"
    assert "hidden" not in json.dumps(result)
    assert "linked_item_02" not in json.dumps(result)


def test_review_queue_counts_and_bounded_projection_with_many_ambiguous_rows():
    edges = [
        {"event_id": f"loose{i:03d}", "toward_event_id": f"anchor{j}",
         "relation": "same_tab_multiplexed"}
        for i in range(30) for j in range(6)
    ]
    result = ambiguous_assignment_review(
        edges, {f"anchor{j}": f"linked_item_{j:02d}" for j in range(6)},
        {f"loose{i:03d}" for i in range(30)},
        {f"linked_item_{j:02d}" for j in range(6)},
    )
    assert result["ambiguous_assignment_review_total"] == 30
    assert result["ambiguous_assignment_review_competing_count"] == 30
    assert result["ambiguous_assignment_review_truncated"] is True
    assert len(result["ambiguous_assignment_review_queue"]) == 24
    assert [row["event_id"] for row in result["ambiguous_assignment_review_queue"]] == [
        f"loose{i:03d}" for i in range(24)
    ]
    assert all(row["candidate_item_count"] == 6 for row in result["ambiguous_assignment_review_queue"])
    assert all(row["candidate_items_truncated"] for row in result["ambiguous_assignment_review_queue"])
    assert all(len(row["candidate_items"]) == 4 for row in result["ambiguous_assignment_review_queue"])


def test_review_queue_does_not_promote_hidden_item_or_cross_tenant_tab():
    events = [
        event(1, A, tab="shared"), event(2, A, tab="shared"),
        event(3, B, tab="shared"), event(4, B, tab="shared"),
        event(5, None, tab="shared"),
    ]
    # B is a genuine linked component but not visible at max_items=1.
    # The candidate toward B must not be shown as an unlinked case target.
    limited = linked_work_items(events, max_items=1)
    assert limited["total_items"] == 2
    assert limited["ambiguous_assignment_review_total"] == 0
    assert limited["ambiguous_assignment_review_queue"] == []
    # A foreign tenant cannot introduce a competing tab reference.
    other_tenant = [event(1, A, tab="shared"),
                    event(2, B, tab="shared", tenant="another-tenant"),
                    event(3, A, tab="shared"),
                    event(4, None, tab="shared")]
    isolated = linked_work_items(other_tenant)
    assert isolated["ambiguous_assignment_review_total"] == 0
    assert isolated["ambiguous_assignment_review_queue"] == []


# Two-hop review suggestions: actual weak evidence paths, never membership.
from server.reconstruction_candidate_fragments import ambiguous_fragment_review


def test_two_hop_fragment_competition_is_exposed_through_real_linker_and_api():
    # Two source-anchored items, separated by three unreferenced actions.
    # Middle e0004 has no direct item candidate; its immediate neighbours do.
    events = [event(1, A), event(2, A), event(3, None),
              event(4, None), event(5, None), event(6, B), event(7, B)]
    for ordered in (events, list(reversed(events))):
        result = linked_work_items(ordered)
        assert result["total_items"] == 2
        assert result["unlinked_observation_count"] == 3
        assert result["ambiguous_fragment_review_total"] == 1
        assert result["ambiguous_fragment_review_competing_count"] == 1
        assert result["ambiguous_fragment_review_scan_complete"] is True
        [review] = result["ambiguous_fragment_review_queue"]
        assert review["event_id"] == "e0004"
        assert review["candidate_item_count"] == 2
        assert review["review_priority"] == "competing_visible_items"
        assert review["evidence_hops"] == 2
        assert review["auto_assigned"] is False
        assert review["requires_human_review"] is True
        assert {tuple(c["witness_event_path"]) for c in review["candidate_items"]} == {
            ("e0004", "e0003", "e0002"),
            ("e0004", "e0005", "e0006"),
        }
        assert all(c["weak_relation_path"] ==
                   ["surface_continuity", "surface_continuity"]
                   for c in review["candidate_items"])
        assert all("e0004" not in item["event_ids"] for item in result["items"])
        api = workflow_evidence.list_workflow_evidence_candidates(_raw_events=ordered)
        assert api["linked_work_items"]["ambiguous_fragment_review_queue"] == [review]
        assert api["linked_work_items"]["link_contract"][
            "ambiguous_fragment_review_never_creates_membership"
        ]
        disclosed = json.dumps(api["linked_work_items"])
        for secret in ("PRIVATE", "SECRET", "secret-tenant",
                       "secret-device", "private.atlassian.net"):
            assert secret not in disclosed


def test_two_hop_review_recomputes_after_retention_and_does_not_promote_membership():
    events = [event(1, A), event(2, A), event(3, None),
              event(4, None), event(5, None), event(6, B), event(7, B)]
    assert linked_work_items(events)["ambiguous_fragment_review_competing_count"] == 1
    after = linked_work_items([events[0], *events[2:5], events[6]])
    # Neither source record now has two admitted observations. There is no
    # visible source-anchored item, so no review target may be invented.
    assert after["ambiguous_fragment_review_total"] == 0
    assert after["ambiguous_fragment_review_queue"] == []
    assert after["unlinked_observation_count"] == 5


def test_two_hop_review_rejects_cross_tenant_session_and_source_changes():
    base = [event(1, A), event(2, A), event(3, None),
            event(4, None), event(5, None), event(6, B), event(7, B)]
    variants = []
    foreign = [dict(x) for x in base]
    foreign[3] = event(4, None, tenant="other-tenant")
    variants.append(foreign)
    no_session = [dict(x) for x in base]
    no_session[3].pop("session_id")
    variants.append(no_session)
    other_source = [dict(x) for x in base]
    other_source[3]["source"] = "unknown"
    variants.append(other_source)
    other_app = [dict(x) for x in base]
    other_app[3]["app"] = "Terminal"
    variants.append(other_app)
    for rows in variants:
        result = linked_work_items(rows)
        assert result["ambiguous_fragment_review_total"] == 0
        assert result["ambiguous_fragment_review_queue"] == []


def test_two_hop_review_refuses_time_only_cycles_and_hidden_targets():
    scope = ("tenant", "actor", "device", "session")
    rows = {name: {"scope": scope, "app": "browser", "source": "extension",
                   "reference_tainted": False, "ephemeral_claim_tainted": False}
            for name in ("loose", "middle", "anchor", "hidden")}
    edges = [
        {"event_id": "loose", "toward_event_id": "middle",
         "relation": "same_tab_unreferenced"},
        {"event_id": "middle", "toward_event_id": "loose",
         "relation": "same_tab_unreferenced"},
        {"event_id": "middle", "toward_event_id": "anchor",
         "relation": "surface_continuity"},
        {"event_id": "middle", "toward_event_id": "hidden",
         "relation": "surface_continuity"},
    ]
    kwargs = dict(rows=rows, singleton_ids={"loose", "middle"},
                  item_of={"anchor": "linked_item_01"},
                  anchored_item_ids={"linked_item_01"})
    a = ambiguous_fragment_review(edges, **kwargs)
    b = ambiguous_fragment_review(list(reversed(edges)), **kwargs)
    assert a == b
    assert a["ambiguous_fragment_review_total"] == 1
    [review] = a["ambiguous_fragment_review_queue"]
    assert review["event_id"] == "loose"
    assert review["candidate_item_count"] == 1
    assert review["candidate_items"][0]["witness_event_path"] == [
        "loose", "middle", "anchor"]
    assert "hidden" not in json.dumps(a)
    assert ambiguous_fragment_review([], **kwargs)["ambiguous_fragment_review_total"] == 0
    # An already linked intermediary cannot be used to launder a path.
    assert ambiguous_fragment_review(edges, rows, {"middle": "linked_item_01",
        "anchor": "linked_item_01"}, {"loose"}, {"linked_item_01"}
    )["ambiguous_fragment_review_total"] == 0


def test_two_hop_review_bounds_rows_targets_and_expensive_fanout():
    scope = ("tenant", "actor", "device", "session")
    def row():
        return {"scope": scope, "app": "browser", "source": "extension",
                "reference_tainted": False, "ephemeral_claim_tainted": False}
    rows = {"mid": row(), "anchor": row()}
    rows.update({f"loose{i:03d}": row() for i in range(30)})
    edges = [
        {"event_id": f"loose{i:03d}", "toward_event_id": "mid",
         "relation": "same_tab_unreferenced"} for i in range(30)
    ] + [{"event_id": "mid", "toward_event_id": "anchor",
          "relation": "surface_continuity"}]
    result = ambiguous_fragment_review(
        edges, rows, {"anchor": "linked_item_01"},
        {"mid"} | {f"loose{i:03d}" for i in range(30)}, {"linked_item_01"})
    assert result["ambiguous_fragment_review_total"] == 30
    assert result["ambiguous_fragment_review_truncated"] is True
    assert len(result["ambiguous_fragment_review_queue"]) == 24
    assert result["ambiguous_fragment_review_scan_complete"] is True
    too_many = ambiguous_fragment_review(
        edges * 400, rows, {"anchor": "linked_item_01"},
        {"mid"} | {f"loose{i:03d}" for i in range(30)}, {"linked_item_01"})
    assert too_many["ambiguous_fragment_review_scan_complete"] is False
    assert too_many["ambiguous_fragment_review_total"] is None
    assert too_many["ambiguous_fragment_review_queue"] == []
