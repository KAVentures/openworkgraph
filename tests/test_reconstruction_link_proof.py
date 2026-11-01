"""Synthetic integration acceptance for #326 A cross-record link witnesses."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from server import workflow_evidence
from server.work_item_links import link_observations, linked_work_items

A, B, C, D = ("owg:r:" + char * 32 for char in "abcd")


def event(n: int, ref: str | None, *, action: str = "browser_click",
          transfer: str | None = None, actor: str = "actor-a",
          session: str = "session-a") -> dict:
    metadata: dict = {}
    if ref is not None:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira", "resource_kind": "issue",
            "host": "private.atlassian.net",
        }
    row = {
        "event_id": f"e{n:04d}",
        "observed_at": (datetime(2026, 10, 20, tzinfo=timezone.utc)
                        + timedelta(minutes=n)).isoformat(),
        "event_type": action, "source": "browser_extension",
        "app": "PRIVATE APP", "window_title": "SECRET PATIENT",
        "metadata": metadata,
        "organization_id": "secret-tenant", "actor_id": actor,
        "device_id": "secret-device", "session_id": session,
    }
    if transfer is not None:
        row["clipboard_transfer_id"] = transfer
    return row


def test_directional_bridge_uses_actual_copy_paste_endpoints_and_api():
    events = [
        event(1, A, action="copy", transfer="private-token"),
        event(2, A, transfer="private-token"),
        event(3, B, action="paste", transfer="private-token"),
        event(4, B),
    ]
    for ordered in (events, list(reversed(events))):
        internal = link_observations(ordered)
        assert ("e0001", "e0003", "clipboard_transfer") in internal["links"]
        assert not any(
            basis == "clipboard_transfer" and target == "e0002"
            for _source, target, basis in internal["links"]
        )
        result = linked_work_items(ordered)
        assert result["total_items"] == 1
        item = result["items"][0]
        assert item["event_ids"] == ["e0001", "e0002", "e0003", "e0004"]
        assert item["link_basis"] == {"same_record": 2, "clipboard_transfer": 1}
        assert item["observed_record_bridge_count"] == 1
        assert item["observed_directional_bridge_count"] == 1
        assert item["observed_legacy_token_bridge_count"] == 0
        assert item["observed_record_bridges_truncated"] is False
        [bridge] = item["observed_record_bridges"]
        assert bridge["evidence_class"] == "ordered_copy_paste"
        assert bridge["direction_observed"] is True
        assert bridge["witness_event_ids"] == ["e0001", "e0003"]
        assert bridge["copy_record_id"] == "record_01"
        assert bridge["paste_record_id"] == "record_02"
        assert bridge["record_ids"] == ["record_01", "record_02"]
        assert bridge["capture_scope_verified"] is True
        assert item["observed_record_runs"][0]["record_id"] == "record_01"
        assert result["link_contract"]["record_bridges_are_observed_link_witnesses_not_handoffs"]
        via_api = workflow_evidence.list_workflow_evidence_candidates(_raw_events=ordered)
        assert via_api["linked_work_items"]["items"][0]["observed_record_bridges"] == [bridge]
        exposed = json.dumps(via_api["linked_work_items"])
        for secret in ("PRIVATE", "SECRET", "secret-tenant", "secret-device",
                       "private-token", "private.atlassian.net"):
            assert secret not in exposed


def test_legacy_generic_token_bridge_is_explicitly_undirected():
    result = linked_work_items([
        event(1, A, transfer="legacy-token"),
        event(2, B, transfer="legacy-token"),
    ])
    [item] = result["items"]
    assert item["cross_record_join"] is True
    assert item["observed_record_bridge_count"] == 1
    assert item["observed_directional_bridge_count"] == 0
    assert item["observed_legacy_token_bridge_count"] == 1
    [bridge] = item["observed_record_bridges"]
    assert bridge["evidence_class"] == "legacy_shared_token"
    assert bridge["direction_observed"] is False
    assert bridge["copy_record_id"] is None and bridge["paste_record_id"] is None
    assert bridge["witness_event_ids"] == ["e0001", "e0002"]


def test_reused_token_recovers_two_disjoint_witnessed_bridges_not_one_case():
    result = linked_work_items([
        event(1, A, action="copy", transfer="reused"),
        event(2, B, action="paste", transfer="reused"),
        event(3, C, action="copy", transfer="reused"),
        event(4, D, action="paste", transfer="reused"),
    ])
    assert result["total_items"] == 2
    assert result["recovered_disjoint_clipboard_transfer_episodes"] == 2
    assert {tuple(item["event_ids"]) for item in result["items"]} == {
        ("e0001", "e0002"), ("e0003", "e0004"),
    }
    for item in result["items"]:
        assert item["observed_record_bridge_count"] == 1
        assert item["observed_directional_bridge_count"] == 1
        assert item["observed_record_bridges"][0]["direction_observed"] is True


def test_competing_copy_origins_are_rejected_not_guessed():
    result = linked_work_items([
        event(1, A, action="copy", transfer="ambiguous"),
        event(2, B, action="copy", transfer="ambiguous"),
        event(3, B, action="paste", transfer="ambiguous"),
        event(4, B),
    ])
    assert result["total_items"] == 1
    [item] = result["items"]
    assert item["event_ids"] == ["e0002", "e0003", "e0004"]
    assert item["cross_record_join"] is False
    assert item["observed_record_bridge_count"] == 0
    assert result["unlinked_observation_count"] == 1
    assert result["inconsistent_clipboard_transfer_direction_groups"] == 1


def test_scope_separation_and_time_alone_never_create_bridge():
    different_actor = linked_work_items([
        event(1, A, action="copy", transfer="same-token"),
        event(2, B, action="paste", transfer="same-token", actor="actor-b"),
    ])
    assert different_actor["items"] == []
    assert different_actor["unlinked_observation_count"] == 2
    time_only = linked_work_items([event(1, A), event(2, B)])
    assert time_only["items"] == []
    assert time_only["link_contract"]["time_alone_never_links"]


def test_bridge_list_is_bounded_with_exact_counts_and_response_local_labels():
    refs = ["owg:r:" + f"{n:032x}" for n in range(1, 32)]
    events = []
    for n in range(30):
        events.append(event(2 * n + 1, refs[n], action="copy", transfer=f"t-{n}"))
        events.append(event(2 * n + 2, refs[n + 1], action="paste", transfer=f"t-{n}"))
    result = linked_work_items(events)
    assert result["total_items"] == 1
    [item] = result["items"]
    assert item["observed_record_bridge_count"] == 30
    assert item["observed_directional_bridge_count"] == 30
    assert len(item["observed_record_bridges"]) == 24
    assert item["observed_record_bridges_truncated"] is True
    assert item["observed_record_bridges"][0]["record_ids"] == ["record_01", "record_02"]
    assert item["observed_record_bridges"][-1]["bridge_index"] == 24
    exposed = json.dumps(item["observed_record_bridges"])
    assert all(ref not in exposed for ref in refs)
    assert "secret-tenant" not in exposed
