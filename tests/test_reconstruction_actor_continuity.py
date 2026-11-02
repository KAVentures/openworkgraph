"""Synthetic, source-identity-scoped actor participation acceptance (#326 A, #264 S2)."""
from __future__ import annotations

import json

from server import workflow_evidence
from server.work_item_links import linked_work_items

A = "owg:r:" + "a" * 32
B = "owg:r:" + "b" * 32


def _event(
    n: int, ref: str | None = A, *, actor: str = "actor-one",
    org: str = "tenant-one", device: str = "laptop", session: str = "session",
    minute: int | None = None, host: str = "work.example.org",
    transfer: str | None = None, action: str = "browser_click",
) -> dict:
    metadata = {"target": {"label": "PRIVATE LABEL"}}
    if ref:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira",
            "resource_kind": "issue", "host": host,
            "resolver_locator": "PRIVATE LOCATOR",
        }
    if transfer:
        metadata["clipboard_transfer_id"] = transfer
    return {
        "event_id": f"evt-{n:03d}",
        "observed_at": f"2026-10-30T10:{minute if minute is not None else n:02d}:00Z",
        "organization_id": org, "actor_id": actor,
        "device_id": device, "session_id": session,
        "app": "PRIVATE APP", "event_type": action,
        "source": "browser_extension", "metadata": metadata,
        "window_title": "PRIVATE WINDOW",
    }


def _single(events: list[dict]) -> dict:
    result = linked_work_items(events)
    assert len(result["items"]) == 1
    return result["items"][0]


def test_cross_actor_same_record_and_return_are_observed_not_verified_handoffs():
    item = _single([
        _event(1, actor="alice"), _event(2, actor="bob"),
        _event(3, actor="alice"), _event(4, actor="alice"),
    ])
    assert item["observed_same_record_participant_count"] == 2
    assert item["observed_same_record_actor_change_count"] == 2
    assert item["observed_same_record_actor_return_count"] == 1
    first, second = item["observed_same_record_actor_changes"]
    assert (first["from_event_id"], first["to_event_id"]) == ("evt-001", "evt-002")
    assert (first["from_participant_id"], first["to_participant_id"]) == (
        "participant_01", "participant_02",
    )
    assert second["return_to_previously_observed_participant"] is True
    assert second["to_participant_id"] == "participant_01"
    assert all(change["handoff_verified"] is False and
               change["continuity_unverified"] is True for change in (first, second))
    assert item["task_identity_inferred"] is False
    assert item["business_outcome_verified"] is False


def test_interleaved_foreign_record_does_not_become_handoff_or_new_membership():
    result = linked_work_items([
        _event(1, actor="alice"), _event(2, B, actor="alice"),
        _event(3, actor="bob"), _event(4, B, actor="alice"),
    ])
    item = next(item for item in result["items"] if item["resource_refs"] == [A])
    [change] = item["observed_same_record_actor_changes"]
    assert change["intervening_same_tenant_observation_count"] == 1
    assert change["continuity_unverified"] is True
    assert item["event_ids"] == ["evt-001", "evt-003"]
    assert len(result["items"]) == 2


def test_cross_record_clipboard_bridge_is_not_an_actor_handoff():
    item = _single([
        _event(1, A, actor="alice", transfer="transfer", action="copy"),
        _event(2, B, actor="alice", transfer="transfer", action="paste"),
        _event(3, A, actor="bob"),
    ])
    assert item["cross_record_join"] is True
    assert item["observed_same_record_actor_change_count"] == 1
    assert item["observed_same_record_actor_changes"][0]["record_id"] == "record_01"
    assert item["observed_same_record_actor_changes"][0]["from_event_id"] == "evt-001"
    assert item["observed_same_record_actor_changes"][0]["to_event_id"] == "evt-003"


def test_incomplete_explicit_scope_breaks_participation_order():
    middle = _event(2, actor="unknown")
    middle.pop("actor_id")
    item = _single([_event(1, actor="alice"), middle, _event(3, actor="bob")])
    assert item["incomplete_actor_scope_observations_suppressed"] == 1
    assert item["observed_same_record_actor_change_count"] == 0
    assert item["observed_same_record_participant_count"] == 2


def test_equal_timestamp_cross_actor_does_not_imply_order():
    item = _single([
        _event(1, actor="alice", minute=1),
        _event(2, actor="bob", minute=1),
        _event(3, actor="alice", minute=3),
        _event(4, actor="bob", minute=4),
    ])
    assert item["same_time_cross_actor_order_suppressed"] == 1
    assert item["observed_same_record_actor_change_count"] == 1
    assert item["observed_same_record_actor_changes"][0]["from_event_id"] == "evt-003"


def test_source_identity_conflict_and_cross_tenant_cannot_claim_handoff():
    result = linked_work_items([
        _event(1, actor="alice"),
        _event(2, actor="bob", org="tenant-two"),
        _event(3, actor="alice"),
    ])
    assert result["items"] == []
    assert result["conflicting_reference_groups_suppressed"] == 1
    host = linked_work_items([
        _event(1, actor="alice"),
        _event(2, actor="bob", host="another.example.org"),
        _event(3, actor="alice"),
    ])
    assert host["items"] == []


def test_legacy_missing_scope_does_not_impersonate_consenting_actor():
    first, second = _event(1), _event(2, actor="bob")
    for key in ("organization_id", "actor_id", "device_id", "session_id"):
        first.pop(key)
    item = _single([first, second])
    assert item["observed_same_record_actor_change_count"] == 0
    assert item["incomplete_actor_scope_observations_suppressed"] == 1


def test_bounded_timeline_has_exact_counts_and_no_identity_or_text_leakage():
    events = [_event(i, actor="alice" if i % 2 else "bob") for i in range(1, 32)]
    item = _single(events)
    assert item["observed_same_record_actor_change_count"] == 30
    assert len(item["observed_same_record_actor_changes"]) == 24
    assert item["observed_same_record_actor_changes_truncated"] is True
    payload = json.dumps(item)
    for secret in ("alice", "bob", "tenant-one", "laptop", "session",
                   "PRIVATE", "work.example.org", "resolver_locator"):
        assert secret not in payload


def test_existing_read_only_workflow_evidence_includes_actor_participation():
    result = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[_event(1, actor="alice"), _event(2, actor="bob")],
    )
    [item] = result["linked_work_items"]["items"]
    assert item["observed_same_record_actor_change_count"] == 1
    assert result["linked_work_items"]["link_contract"][
        "same_record_actor_changes_are_observed_not_verified_handoffs"
    ] is True
    assert result["linked_work_items"]["read_only"] is True
