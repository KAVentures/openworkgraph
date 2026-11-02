"""End-to-end observed activity episodes on the real work-item linker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from server import workflow_evidence
from server.work_item_links import linked_work_items

A, B, C = ("owg:r:" + letter * 32 for letter in "abc")


def event(n: int, ref: str | None = None, *, org: str = "tenant-a",
          actor: str = "actor-a", device: str = "device-a",
          session: str = "session-a") -> dict:
    metadata = {}
    if ref is not None:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira",
            "resource_kind": "issue", "host": "work.atlassian.net",
        }
    return {
        "event_id": f"e{n:04d}",
        "observed_at": (datetime(2026, 10, 20, tzinfo=timezone.utc)
                        + timedelta(minutes=n)).isoformat(),
        "event_type": "browser_click",
        "source": "browser_extension",
        "app": "Browser",
        "metadata": metadata,
        "organization_id": org,
        "actor_id": actor,
        "device_id": device,
        "session_id": session,
        "window_title": "SECRET PRIVATE",
    }


def item(result: dict, ref: str) -> dict:
    return next(entry for entry in result["items"] if ref in entry["resource_refs"])


def test_interleaved_cases_have_distinct_observed_episodes_not_time_joins():
    result = linked_work_items([event(1, A), event(2, B), event(3, A),
                                event(4, B), event(5, A)])
    a, b = item(result, A), item(result, B)
    assert a["event_ids"] == ["e0001", "e0003", "e0005"]
    assert b["event_ids"] == ["e0002", "e0004"]
    assert a["activity_segment_count"] == 3
    assert b["activity_segment_count"] == 2
    assert [s["boundary_before"] for s in a["activity_segments"]] == [
        [], ["interleaved_other_record"], ["interleaved_other_record"]]
    assert [s["event_ids"] for s in a["activity_segments"]] == [
        ["e0001"], ["e0003"], ["e0005"]]
    assert result["link_contract"]["activity_segments_are_observed_episodes_not_tasks"]
    assert result["link_contract"]["time_alone_never_links"]


def test_unreferenced_or_other_tenant_activity_does_not_claim_an_interruption():
    result = linked_work_items([event(1, A), event(2), event(3, A),
                                event(4, B, org="tenant-b"), event(5, A)])
    a = item(result, A)
    assert a["activity_segment_count"] == 1
    # The loose same-app step bracketed by A joins A (RECONSTRUCTION_APP_FOCUS.md);
    # the other tenant's B row is neither a member nor an interruption.
    assert a["activity_segments"][0]["event_ids"] == ["e0001", "e0002", "e0003", "e0005"]
    assert a["link_basis"] == {"same_app_bracketed_record": 1, "same_record": 2}
    assert "tenant-b" not in json.dumps(result)
    assert "SECRET" not in json.dumps(result)


def test_same_record_across_capture_sessions_is_linked_but_episode_split():
    result = linked_work_items([
        event(1, A, session="morning"),
        event(2, B, session="other"),
        event(3, A, session="afternoon"),
    ])
    a = item(result, A)
    assert a["activity_segment_count"] == 2
    assert a["activity_segments"][1]["boundary_before"] == ["capture_scope_change"]
    assert a["link_basis"] == {"same_record": 1}
    assert "morning" not in json.dumps(result)
    assert "afternoon" not in json.dumps(result)


def test_clock_gap_alone_never_proves_interruption():
    first, second = event(1, A), event(2, A)
    second["observed_at"] = "2026-10-27T10:00:00+00:00"
    result = linked_work_items([first, second])
    a = item(result, A)
    assert a["activity_segment_count"] == 1
    assert a["activity_segments"][0]["observed_event_count"] == 2
    assert result["link_contract"]["clock_gaps_alone_do_not_split_activity_segments"]


def test_foreign_record_only_interrupts_within_exact_capture_scope():
    result = linked_work_items([
        event(1, A), event(2, B, actor="actor-b"),
        event(3, A), event(4, C, device="device-b"), event(5, A),
        event(6, B), event(7, A),
    ])
    a = item(result, A)
    assert a["activity_segment_count"] == 2
    assert [s["boundary_before"] for s in a["activity_segments"]] == [
        [], ["interleaved_other_record"]]
    assert a["activity_segments"][0]["event_ids"] == ["e0001", "e0003", "e0005"]
    assert a["activity_segments"][1]["event_ids"] == ["e0007"]


def test_segment_counts_and_truncation_are_exact_for_long_interleaving():
    events = []
    for n in range(1, 82):
        events.append(event(n, A if n % 2 else B))
    result = linked_work_items(events)
    a = item(result, A)
    assert a["observed_event_count"] == 41
    assert a["activity_segment_count"] == 41
    assert a["activity_segments_truncated"] is True
    assert len(a["activity_segments"]) == 16
    assert all(s["observed_event_count"] == 1 for s in a["activity_segments"])
    assert all(s["event_ids_truncated"] is False for s in a["activity_segments"])


def test_existing_workflow_evidence_api_carries_additive_segments():
    result = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[event(1, A), event(2, B), event(3, A)]
    )
    linked = result["linked_work_items"]
    assert item(linked, A)["activity_segment_count"] == 2
    assert "record_case_lanes" in result


def test_unreferenced_other_app_splits_observed_episode_not_case_membership():
    browser_before, terminal, browser_after = event(1, A), event(2), event(3, A)
    terminal.update(app="Terminal", source="desktop_capture")
    result = linked_work_items([browser_before, terminal, browser_after])
    a = item(result, A)
    assert a["event_ids"] == ["e0001", "e0003"]
    assert a["activity_segment_count"] == 2
    assert [segment["boundary_before"] for segment in a["activity_segments"]] == [
        [], ["observed_other_app_without_record"]]
    assert a["link_basis"] == {"same_record": 1}
    assert result["unlinked_observation_count"] == 1
    assert "Terminal" not in json.dumps(result)
    assert "SECRET" not in json.dumps(result)
    # Removing a retained observation recomputes boundaries, not memberships.
    without_terminal = item(linked_work_items([browser_before, browser_after]), A)
    assert without_terminal["activity_segment_count"] == 1


def test_other_app_boundary_requires_same_complete_scope_and_trusted_capture():
    def segments(change):
        first, middle, last = event(1, A), event(2), event(3, A)
        middle.update(app="Terminal", source="desktop_capture")
        change(first, middle, last)
        return item(linked_work_items([first, middle, last]), A)["activity_segment_count"]

    assert segments(lambda _a, b, _c: b.update(organization_id="tenant-b")) == 1
    assert segments(lambda _a, b, _c: b.update(actor_id="actor-b")) == 1
    assert segments(lambda _a, b, _c: b.update(device_id="device-b")) == 1
    assert segments(lambda _a, b, _c: b.update(session_id="session-b")) == 1
    assert segments(lambda _a, b, _c: b.pop("device_id")) == 1
    assert segments(lambda _a, b, _c: b.update(app="Other")) == 1
    assert segments(lambda _a, b, _c: b.update(app="Unknown")) == 1
    assert segments(lambda _a, b, _c: b.update(app="Browser")) == 1
    assert segments(lambda _a, b, _c: b.update(source="unknown")) == 1
    assert segments(lambda _a, b, _c: b.update(app="Terminal\nInjected")) == 1
    assert segments(lambda _a, b, _c: b.update(
        metadata={"resource_reference": {"resource_ref": True}})) == 1
    assert segments(lambda _a, b, _c: b.update(
        tab_context_id="tab-a", metadata={"tab_context_id": "tab-b"})) == 1


def test_other_app_boundary_requires_strict_time_and_same_member_app():
    def segments(change):
        first, middle, last = event(1, A), event(2), event(3, A)
        middle.update(app="Terminal", source="desktop_capture")
        change(first, middle, last)
        return item(linked_work_items([first, middle, last]), A)["activity_segments"]

    assert len(segments(lambda _a, b, c: b.update(observed_at=c["observed_at"]))) == 1
    assert len(segments(lambda a, b, _c: b.update(observed_at=a["observed_at"]))) == 1
    assert len(segments(lambda _a, _b, c: c.update(app="Excel"))) == 1
    assert len(segments(lambda _a, _b, _c: None)) == 2


def test_literal_foreign_record_retains_existing_interleaving_boundary():
    first, middle, last = event(1, A), event(2, B), event(3, A)
    middle.update(app="Terminal", source="desktop_capture")
    a = item(linked_work_items([first, middle, last]), A)
    assert a["activity_segment_count"] == 2
    assert a["activity_segments"][1]["boundary_before"] == [
        "interleaved_other_record"]


def test_other_app_episode_counts_remain_exact_when_timeline_truncated():
    observations = []
    for n in range(1, 82):
        row = event(n, A if n % 2 else None)
        if n % 2 == 0:
            row.update(app="Terminal", source="desktop_capture")
        observations.append(row)
    a = item(linked_work_items(observations), A)
    assert a["observed_event_count"] == 41
    assert a["activity_segment_count"] == 41
    assert a["activity_segments_truncated"] is True
    assert len(a["activity_segments"]) == 16
    assert all(segment["boundary_before"] == ["observed_other_app_without_record"]
               for segment in a["activity_segments"][1:])


def test_existing_workflow_evidence_api_exposes_uncertain_other_app_episode():
    first, middle, last = event(1, A), event(2), event(3, A)
    middle.update(app="Terminal", source="desktop_capture")
    response = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[first, middle, last]
    )
    linked = response["linked_work_items"]
    assert item(linked, A)["activity_segments"][1]["boundary_before"] == [
        "observed_other_app_without_record"]
    assert response["record_case_lanes"]["format"] == "openworkgraph.record-case-lanes.v1"


def test_other_tab_observation_splits_same_tab_episode_without_assigning_it():
    before, other_tab, after = event(1, A), event(2), event(3, A)
    before["tab_context_id"] = after["tab_context_id"] = "tab-a"
    other_tab["tab_context_id"] = "tab-b"
    result = linked_work_items([before, other_tab, after])
    a = item(result, A)
    assert a["event_ids"] == ["e0001", "e0003"]
    assert a["activity_segment_count"] == 2
    assert [s["boundary_before"] for s in a["activity_segments"]] == [
        [], ["observed_other_tab_without_record"]]
    assert result["unlinked_observation_count"] == 1
    assert "tab-a" not in json.dumps(result)
    assert "tab-b" not in json.dumps(result)
    assert "SECRET" not in json.dumps(result)
    # Retention/removal recomputes the episode boundary from surviving evidence.
    assert item(linked_work_items([before, after]), A)["activity_segment_count"] == 1


def test_other_tab_episode_requires_exact_complete_capture_scope():
    def count(change):
        before, middle, after = event(1, A), event(2), event(3, A)
        before["tab_context_id"] = after["tab_context_id"] = "tab-a"
        middle["tab_context_id"] = "tab-b"
        change(before, middle, after)
        return item(linked_work_items([before, middle, after]), A)[
            "activity_segment_count"]

    assert count(lambda _a, _b, _c: None) == 2
    assert count(lambda _a, b, _c: b.update(organization_id="tenant-b")) == 1
    assert count(lambda _a, b, _c: b.update(actor_id="actor-b")) == 1
    assert count(lambda _a, b, _c: b.update(device_id="device-b")) == 1
    assert count(lambda _a, b, _c: b.update(session_id="session-b")) == 1
    assert count(lambda _a, b, _c: b.pop("device_id")) == 1
    assert count(lambda _a, b, _c: b.update(tab_context_id="tab-a")) == 1
    assert count(lambda _a, b, _c: b.pop("tab_context_id")) == 1
    assert count(lambda _a, b, _c: b.update(app="Unknown")) == 1
    assert count(lambda _a, b, _c: b.update(source="unknown")) == 1
    assert count(lambda _a, b, _c: b.update(app="Terminal")) == 2
    assert count(lambda _a, b, _c: b.update(
        tab_context_id="tab-b", metadata={"tab_context_id": "tab-c"})) == 1
    assert count(lambda _a, b, _c: b.update(
        metadata={"resource_reference": {"resource_ref": True}})) == 1


def test_other_tab_episode_rejects_ties_clipboard_and_mismatched_anchors():
    def boundary(change):
        before, middle, after = event(1, A), event(2), event(3, A)
        before["tab_context_id"] = after["tab_context_id"] = "tab-a"
        middle["tab_context_id"] = "tab-b"
        change(before, middle, after)
        return item(linked_work_items([before, middle, after]), A)[
            "activity_segments"]

    assert len(boundary(lambda _a, _b, _c: None)) == 2
    assert len(boundary(lambda _a, b, c: b.update(
        observed_at=c["observed_at"]))) == 1
    assert len(boundary(lambda a, b, _c: b.update(
        observed_at=a["observed_at"]))) == 1
    assert len(boundary(lambda _a, _b, c: c.update(tab_context_id="tab-c"))) == 1
    assert len(boundary(lambda _a, _b, c: c.update(app="Excel"))) == 1
    assert len(boundary(lambda _a, _b, c: c.update(source="desktop_capture"))) == 1
    assert len(boundary(lambda _a, b, _c: b.update(
        event_type="clipboard_copy", metadata={"clipboard_transfer_id": "transfer-x"}))) == 1
    assert len(boundary(lambda _a, b, _c: b.update(event_type="clipboard_copy"))) == 1
    assert len(boundary(lambda _a, b, _c: b.update(event_type="clipboard_paste"))) == 1
    assert len(boundary(lambda a, _b, _c: a.update(event_type="clipboard_copy"))) == 1


def test_other_tab_interruption_preserves_foreign_record_precedence_and_api():
    before, middle, after = event(1, A), event(2, B), event(3, A)
    before["tab_context_id"] = after["tab_context_id"] = "tab-a"
    middle["tab_context_id"] = "tab-b"
    a = item(linked_work_items([before, middle, after]), A)
    assert a["activity_segments"][1]["boundary_before"] == [
        "interleaved_other_record"]
    middle = event(2)
    middle["tab_context_id"] = "tab-b"
    response = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[before, middle, after])
    linked = response["linked_work_items"]
    assert item(linked, A)["activity_segments"][1]["boundary_before"] == [
        "observed_other_tab_without_record"]
    assert linked["read_only"] is True
    assert linked["authoritative"] is False


def test_other_tab_interruptions_keep_exact_count_when_projection_is_bounded():
    observations = []
    for n in range(1, 82):
        row = event(n, A if n % 2 else None)
        row["tab_context_id"] = "tab-a" if n % 2 else "tab-b"
        observations.append(row)
    a = item(linked_work_items(observations), A)
    assert a["observed_event_count"] == 41
    assert a["activity_segment_count"] == 41
    assert a["activity_segments_truncated"] is True
    assert len(a["activity_segments"]) == 16
    assert all(s["boundary_before"] == ["observed_other_tab_without_record"]
               for s in a["activity_segments"][1:])


def _cross_record_member_sequence():
    """A witnessed clipboard bridge creates ONE item with two exact records."""
    first, second, resumed = event(1, A), event(2, B), event(3, A)
    first["event_type"] = "copy"
    second["event_type"] = "paste"
    first["metadata"]["clipboard_transfer_id"] = "private-transfer-1"
    second["metadata"]["clipboard_transfer_id"] = "private-transfer-1"
    return first, second, resumed


def test_cross_record_linked_item_has_distinct_observed_record_episodes():
    first, second, resumed = _cross_record_member_sequence()
    result = linked_work_items([first, second, resumed])
    joined = item(result, A)
    assert joined["resource_refs"] == [A, B]
    assert joined["cross_record_join"] is True
    assert joined["event_ids"] == ["e0001", "e0002", "e0003"]
    assert joined["link_basis"] == {"clipboard_transfer": 1, "same_record": 1}
    assert joined["activity_segment_count"] == 3
    assert [s["event_ids"] for s in joined["activity_segments"]] == [
        ["e0001"], ["e0002"], ["e0003"]]
    assert [s["boundary_before"] for s in joined["activity_segments"]] == [
        [], ["observed_member_record_change"], ["observed_member_record_change"]]
    assert "private-transfer-1" not in json.dumps(result)
    assert "actor-a" not in json.dumps(result)
    assert result["link_contract"]["activity_segments_are_observed_episodes_not_tasks"]


def test_cross_record_episodes_survive_workflow_api_and_recompute_on_removal():
    first, second, resumed = _cross_record_member_sequence()
    response = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[first, second, resumed])
    linked = response["linked_work_items"]
    assert item(linked, A)["activity_segment_count"] == 3
    assert response["record_case_lanes"]["format"] == "openworkgraph.record-case-lanes.v1"
    # Removing a source observation must never leave a stale record boundary.
    retained = item(linked_work_items([first, resumed]), A)
    assert retained["event_ids"] == ["e0001", "e0003"]
    assert retained["activity_segment_count"] == 1
    assert retained["cross_record_join"] is False


def test_record_context_boundary_abstains_on_ties_and_incomplete_scope():
    from server.reconstruction_segments import observed_activity_segments
    from server.work_item_links import link_observations

    def projected(first, second):
        rows = link_observations([first, second])["rows"]
        return observed_activity_segments(rows, ["e0001", "e0002"])

    first, second = event(1, A), event(2, B)
    assert [s["boundary_before"] for s in projected(first, second)["activity_segments"]] == [
        [], ["observed_member_record_change"]]
    second["observed_at"] = first["observed_at"]
    assert projected(first, second)["activity_segment_count"] == 1
    second["observed_at"] = event(2, B)["observed_at"]
    first.pop("device_id")
    second.pop("device_id")
    assert projected(first, second)["activity_segment_count"] == 1
    first, second = event(1, A), event(2, B, actor="actor-b")
    assert [s["boundary_before"] for s in projected(first, second)["activity_segments"]] == [
        [], ["capture_scope_change"]]


def test_exact_record_change_after_clean_unreferenced_member_splits_at_new_anchor():
    from server.reconstruction_segments import observed_activity_segments
    from server.work_item_links import link_observations

    first, loose, second = event(1, A), event(2), event(3, B)
    rows = link_observations([first, loose, second])["rows"]
    projected = observed_activity_segments(rows, ["e0001", "e0002", "e0003"])
    assert projected["activity_segment_count"] == 2
    assert [s["event_ids"] for s in projected["activity_segments"]] == [
        ["e0001", "e0002"], ["e0003"]]
    assert [s["boundary_before"] for s in projected["activity_segments"]] == [
        [], ["observed_member_record_change"]]
    # The unreferenced member has no newly invented source-record identity
    # (the linker uses "" for "no reference").
    assert rows[1]["ref"] == ""


def test_many_within_item_record_switches_keep_exact_episode_count():
    observations = [event(n, A if n % 2 else B) for n in range(1, 82)]
    observations[0]["event_type"] = "copy"
    observations[1]["event_type"] = "paste"
    observations[0]["metadata"]["clipboard_transfer_id"] = "bridge-81"
    observations[1]["metadata"]["clipboard_transfer_id"] = "bridge-81"
    result = linked_work_items(observations)
    joined = item(result, A)
    assert joined["cross_record_join"] is True
    assert joined["observed_event_count"] == 81
    assert joined["activity_segment_count"] == 81
    assert joined["activity_segments_truncated"] is True
    assert len(joined["activity_segments"]) == 16
    assert all(segment["boundary_before"] == ["observed_member_record_change"]
               for segment in joined["activity_segments"][1:])
    assert "bridge-81" not in json.dumps(result)


def test_cross_record_linker_and_workflow_api_split_after_unreferenced_member():
    # Clipboard evidence independently links A and B. The same-tab loose
    # observation is already accepted into that item; it never gains a ref.
    first, loose, second = event(1, A), event(2), event(3, B)
    first["event_type"] = "copy"
    second["event_type"] = "paste"
    first["metadata"]["clipboard_transfer_id"] = "private-bridge"
    second["metadata"]["clipboard_transfer_id"] = "private-bridge"
    first["tab_context_id"] = loose["tab_context_id"] = "tab-a"
    second.update(app="Terminal", source="desktop_capture")
    result = linked_work_items([first, loose, second])
    joined = item(result, A)
    assert joined["resource_refs"] == [A, B]
    assert joined["event_ids"] == ["e0001", "e0002", "e0003"]
    assert joined["activity_segment_count"] == 2
    assert [s["event_ids"] for s in joined["activity_segments"]] == [
        ["e0001", "e0002"], ["e0003"]]
    assert joined["activity_segments"][1]["boundary_before"] == [
        "observed_member_record_change"]
    assert "private-bridge" not in json.dumps(result)
    assert "tab-a" not in json.dumps(result)
    assert "SECRET" not in json.dumps(result)
    api = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[first, loose, second])
    assert item(api["linked_work_items"], A)["activity_segment_count"] == 2
    # Removing B removes the record-change boundary, without stale state.
    retained = item(linked_work_items([first, loose]), A)
    assert retained["activity_segment_count"] == 1
    assert retained["cross_record_join"] is False


def test_unreferenced_record_bridge_rejects_ties_taint_and_transfer_claims():
    from server.reconstruction_segments import observed_activity_segments
    from server.work_item_links import link_observations

    def segments(change):
        first, loose, second = event(1, A), event(2), event(3, B)
        rows = link_observations([first, loose, second])["rows"]
        change(rows)
        return observed_activity_segments(
            rows, ["e0001", "e0002", "e0003"])["activity_segments"]

    assert len(segments(lambda _rows: None)) == 2
    assert len(segments(lambda rows: rows[1].update(
        observed_at=rows[2]["observed_at"]))) == 1
    assert len(segments(lambda rows: rows[1].update(
        observed_at=rows[0]["observed_at"]))) == 1
    assert len(segments(lambda rows: rows[1].update(
        reference_tainted=True))) == 1
    assert len(segments(lambda rows: rows[1].update(
        ephemeral_claim_tainted=True))) == 1
    assert len(segments(lambda rows: rows[1].update(
        action_claim_malformed=True))) == 1
    assert len(segments(lambda rows: rows[1].update(
        transfer="untrusted-transfer"))) == 1
    assert len(segments(lambda rows: rows[1].update(
        action="clipboard_paste"))) == 1
    assert len(segments(lambda rows: rows[0].update(
        reference_tainted=True))) == 1
    assert len(segments(lambda rows: rows[2].update(
        reference_tainted=True))) == 1
    assert len(segments(lambda rows: rows[2].update(
        ref=A))) == 1


def test_unreferenced_record_bridge_is_bounded_and_scope_safe():
    from server.reconstruction_segments import observed_activity_segments
    from server.work_item_links import link_observations

    def project(count):
        events = [event(1, A)] + [event(n) for n in range(2, count + 2)]
        events.append(event(count + 2, B))
        rows = link_observations(events)["rows"]
        return observed_activity_segments(
            rows, [row["event_id"] for row in rows])

    assert project(8)["activity_segment_count"] == 2
    assert project(9)["activity_segment_count"] == 1
    first, loose, second = event(1, A), event(2), event(3, B)
    rows = link_observations([first, loose, second])["rows"]
    rows[1]["scope"] = ("tenant-b", "actor-a", "device-a", "session-a")
    result = observed_activity_segments(rows, ["e0001", "e0002", "e0003"])
    assert not any(
        "observed_member_record_change" in s["boundary_before"]
        for s in result["activity_segments"])
    assert result["activity_segments"][1]["boundary_before"] == [
        "capture_scope_change"]
