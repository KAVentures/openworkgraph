"""End-to-end synthetic acceptance for conservative reused clipboard token episodes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from server import workflow_evidence
from server.work_item_links import linked_work_items
from server.reconstruction_transfer_episodes import disjoint_directional_transfer_episodes


def ref(n: int) -> str:
    return "owg:r:" + f"{n:032x}"


def event(n: int, record: str | None, action: str = "copy",
          *, token: str | None = "reused-secret-token", tenant: str = "tenant-a",
          actor: str = "actor-a", device: str = "device-a",
          session: str = "session-a") -> dict:
    metadata: dict = {"page": {"pathname": "/PRIVATE"}}
    if record is not None:
        metadata["resource_reference"] = {
            "resource_ref": record, "provider": "jira",
            "resource_kind": "issue", "host": "work.atlassian.net",
        }
    if token is not None:
        metadata["clipboard_transfer_id"] = token
    return {
        "event_id": f"e{n:04d}",
        "observed_at": (datetime(2026, 10, 20, tzinfo=timezone.utc)
                        + timedelta(minutes=n)).isoformat(),
        "event_type": action,
        "source": "browser_extension",
        "app": "Browser",
        "metadata": metadata,
        "organization_id": tenant,
        "actor_id": actor,
        "device_id": device,
        "session_id": session,
        "window_title": "PRIVATE TITLE",
    }


def memberships(result: dict) -> set[tuple[str, ...]]:
    return {tuple(item["event_ids"]) for item in result["items"]}


def test_reused_token_recovers_two_disjoint_directional_episodes_without_merging_them():
    observations = [
        event(1, ref(1), "copy"), event(2, ref(2), "paste"),
        event(3, ref(3), "copy"), event(4, ref(4), "paste"),
    ]
    for order in (observations, list(reversed(observations))):
        result = linked_work_items(order)
        assert memberships(result) == {("e0001", "e0002"), ("e0003", "e0004")}
        assert all(item["link_basis"] == {"clipboard_transfer": 1}
                   and item["cross_record_join"] and item["confidence"] == 0.85
                   for item in result["items"])
        assert result["recovered_reused_clipboard_transfer_groups"] == 1
        assert result["recovered_disjoint_clipboard_transfer_episodes"] == 2
        assert result["ambiguous_clipboard_transfer_groups"] == 0
        assert result["unlinked_observation_count"] == 0
        assert result["link_contract"]["reused_clipboard_token_only_links_disjoint_explicit_episodes"]
        text = json.dumps(result)
        assert "reused-secret-token" not in text
        assert "PRIVATE" not in text
        assert "tenant-a" not in text


def test_recovered_episodes_preserve_independent_same_record_membership():
    result = linked_work_items([
        event(1, ref(1), token=None),
        event(2, ref(1), "copy"),
        event(3, ref(2), "paste"),
        event(4, ref(3), "copy"),
        event(5, ref(4), "paste"),
        event(6, ref(4), token=None),
    ])
    assert memberships(result) == {
        ("e0001", "e0002", "e0003"), ("e0004", "e0005", "e0006"),
    }
    assert result["recovered_disjoint_clipboard_transfer_episodes"] == 2


def test_interleaved_direction_and_repeated_record_stay_unmerged():
    interleaved = [
        event(1, ref(1), "copy"), event(2, ref(3), "copy"),
        event(3, ref(2), "paste"), event(4, ref(4), "paste"),
    ]
    for order in (interleaved, list(reversed(interleaved))):
        result = linked_work_items(order)
        assert result["items"] == []
        assert result["ambiguous_clipboard_transfer_groups"] == 1
        assert result["recovered_disjoint_clipboard_transfer_episodes"] == 0

    # Two plausible pairs sharing record B would transitively fuse A/B/C.
    # Withhold the token entirely, retaining only B's independent exact ref.
    overlapping = [
        event(1, ref(1), "copy"), event(2, ref(2), "paste"),
        event(3, ref(2), "copy"), event(4, ref(3), "paste"),
    ]
    result = linked_work_items(overlapping)
    assert memberships(result) == {("e0002", "e0003")}
    assert result["ambiguous_clipboard_transfer_groups"] == 1
    assert result["recovered_disjoint_clipboard_transfer_episodes"] == 0


def test_loose_partial_generic_or_equal_time_evidence_never_recovers_a_subset():
    complete = [
        event(1, ref(1), "copy"), event(2, ref(2), "paste"),
        event(3, ref(3), "copy"), event(4, ref(4), "paste"),
    ]
    loose = [*complete[:2], event(7, None, "browser_click"), *complete[2:]]
    generic = [*complete[:2], event(3, ref(3), "browser_click"), complete[3]]
    partial = complete[:-1]
    equal = [dict(row) for row in complete]
    equal[1] = dict(equal[1], observed_at=equal[0]["observed_at"])
    for observations in (loose, generic, partial, equal):
        result = linked_work_items(observations)
        assert result["recovered_disjoint_clipboard_transfer_episodes"] == 0
        assert result["items"] == []


def test_exact_scope_isolation_keeps_reused_token_pairs_separate():
    result = linked_work_items([
        event(1, ref(1), "copy", tenant="tenant-a"),
        event(2, ref(2), "paste", tenant="tenant-a"),
        event(3, ref(3), "copy", tenant="tenant-b"),
        event(4, ref(4), "paste", tenant="tenant-b"),
    ])
    assert memberships(result) == {("e0001", "e0002"), ("e0003", "e0004")}
    # These are two normal two-record token groups, not a recovered fanout.
    assert result["recovered_reused_clipboard_transfer_groups"] == 0
    assert result["recovered_disjoint_clipboard_transfer_episodes"] == 0
    assert linked_work_items([event(1, ref(1), token=None),
                              event(2, ref(2), token=None)])["items"] == []


def test_bounded_recovery_refuses_large_reused_token_without_partial_links():
    # The cap applies to the whole token group, not the first 64 episodes.
    observations = [
        event(i, ref(i), "copy" if i % 2 else "paste")
        for i in range(1, 131)
    ]
    result = linked_work_items(observations)
    assert result["items"] == []
    assert result["recovered_disjoint_clipboard_transfer_episodes"] == 0
    assert result["ambiguous_clipboard_transfer_groups"] == 1
    assert disjoint_directional_transfer_episodes([
        {"event_id": f"e{i:04d}", "ref": ref(i),
         "action": "copy" if i % 2 else "paste", "observed_at": i}
        for i in range(1, 131)
    ]) is None


def test_malformed_direction_or_source_claim_does_not_recover_episode():
    observations = [
        event(1, ref(1), "copy"), event(2, ref(2), "paste"),
        event(3, ref(3), "copy"), event(4, ref(4), "paste"),
    ]
    malformed = [dict(row) for row in observations]
    malformed[2] = dict(malformed[2], event_type=42)
    assert linked_work_items(malformed)["recovered_disjoint_clipboard_transfer_episodes"] == 0
    bad_record = [dict(row) for row in observations]
    bad_record[2] = dict(bad_record[2], metadata={
        "clipboard_transfer_id": "reused-secret-token",
        "resource_reference": {"resource_ref": ref(3), "provider": True,
                               "resource_kind": "issue", "host": "work.atlassian.net"},
    })
    assert linked_work_items(bad_record)["recovered_disjoint_clipboard_transfer_episodes"] == 0


def test_existing_workflow_evidence_read_api_exposes_bounded_recovered_episodes():
    result = workflow_evidence.list_workflow_evidence_candidates(_raw_events=[
        event(1, ref(1), "copy"), event(2, ref(2), "paste"),
        event(3, ref(3), "copy"), event(4, ref(4), "paste"),
    ])
    linked = result["linked_work_items"]
    assert memberships(linked) == {("e0001", "e0002"), ("e0003", "e0004")}
    assert linked["recovered_disjoint_clipboard_transfer_episodes"] == 2
    assert linked["read_only"] and not linked["authoritative"]
    assert "record_case_lanes" in result
