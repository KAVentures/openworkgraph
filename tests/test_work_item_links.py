"""Multi-signal work-item links with explicit uncertainty (roadmap #326 A1)."""
from __future__ import annotations

import copy
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


def _scoped(event, *, org="tenant-a", actor="actor-a", device="device-a", session="session-a"):
    return {
        **event,
        "organization_id": org,
        "actor_id": actor,
        "device_id": device,
        "session_id": session,
    }


def test_clipboard_transfer_is_not_global_across_tenant_actor_device_or_session():
    for dimension in ("organization_id", "actor_id", "device_id", "session_id"):
        first = _scoped(_event(1, A, transfer="reused-transfer"))
        second = _scoped(_event(2, B, transfer="reused-transfer"))
        second[dimension] = "different-scope"
        result = linked_work_items([first, second])
        assert result["items"] == [], dimension
        assert result["candidate_links"] == [], dimension

    same_scope = linked_work_items([
        _scoped(_event(1, A, transfer="same-transfer")),
        _scoped(_event(2, B, transfer="same-transfer")),
    ])
    assert set(_items(same_scope)) == {("e01", "e02")}
    assert _items(same_scope)[("e01", "e02")]["link_basis"] == {"clipboard_transfer": 1}


def test_reused_tab_id_cannot_attach_unreferenced_events_across_scope():
    for dimension in ("organization_id", "actor_id", "device_id", "session_id"):
        anchor = _scoped(_event(1, A, tab="reused-tab"))
        loose = _scoped(_event(2, tab="reused-tab"))
        loose[dimension] = "different-scope"
        result = linked_work_items([anchor, loose])
        assert result["items"] == [], dimension
        assert result["candidate_links"] == [], dimension

    # Two independently scoped tabs with the same opaque ID remain separate.
    result = linked_work_items([
        _scoped(_event(1, A, tab="reused-tab")),
        _scoped(_event(2, tab="reused-tab")),
        _scoped(_event(3, B, tab="reused-tab"), org="tenant-b"),
        _scoped(_event(4, tab="reused-tab"), org="tenant-b"),
    ])
    assert set(_items(result)) == {("e01", "e02"), ("e03", "e04")}
    assert result["candidate_links"] == []


def test_surface_continuity_candidates_respect_scope_not_just_adjacency():
    for dimension in ("organization_id", "actor_id", "device_id", "session_id"):
        first = _scoped(_event(1, app="Excel"))
        second = _scoped(_event(2, app="Excel"))
        second[dimension] = "different-scope"
        result = linked_work_items([first, second])
        assert result["items"] == [], dimension
        assert result["candidate_links"] == [], dimension

    same_scope = linked_work_items([
        _scoped(_event(1, app="Excel")),
        _scoped(_event(2, app="Excel")),
    ])
    assert {c["relation"] for c in same_scope["candidate_links"]} == {"surface_continuity"}
    assert all(c["merged"] is False for c in same_scope["candidate_links"])


def test_missing_or_malformed_scope_is_never_a_cross_scope_wildcard():
    named = _scoped(_event(1, A, transfer="x", tab="t"))
    unknown = _event(2, B, transfer="x", tab="t")
    invalid = _scoped(_event(3, C, transfer="x", tab="t"))
    invalid["organization_id"] = {"invalid": "not-a-string"}
    result = linked_work_items([named, unknown, invalid])
    assert result["items"] == []
    assert result["candidate_links"] == []
    assert result["link_contract"]["missing_scope_is_wildcard"] is False
    assert result["link_contract"]["ephemeral_signals_scope"] == "exact_organization_actor_device_session"


def test_exact_record_evidence_can_still_link_across_actors_without_leaking_scope():
    result = linked_work_items([
        _scoped(_event(1, A), actor="human-a"),
        _scoped(_event(2, A), actor="human-b"),
    ])
    assert set(_items(result)) == {("e01", "e02")}
    assert _items(result)[("e01", "e02")]["link_basis"] == {"same_record": 1}
    output = json.dumps(result)
    for private_scope in ("tenant-a", "human-a", "human-b", "device-a", "session-a"):
        assert private_scope not in output


def test_conflicted_record_identity_cannot_rejoin_through_transfer_or_tab():
    # A reused opaque reference with contradictory provider identity is
    # suppressed even if its observations also share weaker linking tokens.
    for signal in ("transfer", "tab"):
        opts = {signal: "reused-ephemeral"}
        first = _scoped(_event(1, A, action="copy", **opts))
        conflicting = _scoped(_event(2, A, provider="github", action="paste", **opts))
        independent = _scoped(_event(3, B, action="paste", **opts))
        result = linked_work_items([first, conflicting, independent])
        assert result["items"] == [], signal
        assert result["candidate_links"] == [], signal
        assert result["conflicting_reference_groups_suppressed"] == 1


def test_invalid_explicit_reference_is_not_treated_as_loose_tab_or_transfer():
    for signal in ("transfer", "tab"):
        opts = {signal: "reused-ephemeral"}
        invalid = _scoped(_event(1, app="Excel", action="copy", **opts))
        invalid["metadata"]["resource_reference"] = {
            "resource_ref": A, "provider": "BAD PROVIDER",
            "resource_kind": "issue", "host": "work.atlassian.net",
        }
        valid = _scoped(_event(2, B, action="paste", **opts))
        result = linked_work_items([invalid, valid])
        assert result["items"] == [], signal
        assert result["candidate_links"] == [], signal
        assert result["conflicting_reference_groups_suppressed"] == 1


def test_invalid_claim_cannot_create_surface_candidate_and_valid_loose_events_still_link():
    invalid = _scoped(_event(1, app="Excel"))
    invalid["metadata"]["resource_reference"] = {
        "resource_ref": "not-an-opaque-record-ref", "provider": "jira",
        "resource_kind": "issue",
    }
    ordinary = _scoped(_event(2, app="Excel"))
    result = linked_work_items([invalid, ordinary])
    assert result["items"] == []
    assert result["candidate_links"] == []

    # Genuine absence of a reference is different from an invalid claim.
    result = linked_work_items([
        _scoped(_event(1, A, tab="legitimate-tab")),
        _scoped(_event(2, tab="legitimate-tab")),
        _scoped(_event(3, A, tab="legitimate-tab")),
    ])
    assert set(_items(result)) == {("e01", "e02", "e03")}
    assert _items(result)[("e01", "e02", "e03")]["link_basis"] == {
        "same_record": 1, "same_tab_single_record": 1,
    }


def test_non_object_reference_claims_cannot_impersonate_unreferenced_tab_events():
    for invalid_claim in ("bad-ref", ["bad-ref"], 7, {}):
        invalid = _scoped(_event(1, tab="shared-tab"))
        invalid["metadata"]["resource_reference"] = invalid_claim
        valid = _scoped(_event(2, B, tab="shared-tab"))
        result = linked_work_items([invalid, valid])
        assert result["items"] == [], invalid_claim
        assert result["candidate_links"] == [], invalid_claim


def test_ephemeral_identifiers_reject_nonstring_metadata_claims():
    # Python's str(True) and str(7) match the opaque-ID regex, but neither
    # value is an observed string identifier. They must not join two records.
    for field in ("tab_context_id", "clipboard_transfer_id"):
        for invalid in (True, False, 7, 0):
            first = _scoped(_event(1, A))
            second = _scoped(_event(2, B))
            first["metadata"][field] = invalid
            second["metadata"][field] = invalid
            result = linked_work_items([first, second])
            assert result["items"] == [], (field, invalid)
            assert result["candidate_links"] == [], (field, invalid)


def test_invalid_explicit_top_level_identifier_never_falls_back_to_metadata():
    # A malformed/empty explicit claim must not be replaced by a valid
    # metadata ID, which would fabricate a shared tab or transfer.
    for field in ("tab_context_id", "clipboard_transfer_id"):
        for invalid in (None, False, 0, "", 7):
            first = _scoped(_event(1, A))
            second = _scoped(_event(2, B))
            first["metadata"][field] = "shared-opaque"
            second["metadata"][field] = "shared-opaque"
            first[field] = invalid
            result = linked_work_items([first, second])
            assert result["items"] == [], (field, invalid)
            assert result["candidate_links"] == [], (field, invalid)


def test_literal_metadata_and_explicit_top_level_identifiers_still_work():
    for field in ("tab_context_id", "clipboard_transfer_id"):
        first = _scoped(_event(1, A))
        second = _scoped(_event(2, B if field == "clipboard_transfer_id" else None))
        first["metadata"][field] = "literal-opaque"
        second["metadata"][field] = "literal-opaque"
        expected_basis = "clipboard_transfer" if field == "clipboard_transfer_id" else "same_tab_single_record"
        result = linked_work_items([first, second])
        assert set(_items(result)) == {("e01", "e02")}, field
        assert _items(result)[("e01", "e02")]["link_basis"] == {expected_basis: 1}

        # Explicit valid string wins over malformed metadata, rather than
        # rejecting a genuine top-level identifier.
        first[field] = "top-level-opaque"
        second[field] = "top-level-opaque"
        first["metadata"][field] = 7
        second["metadata"][field] = True
        result = linked_work_items([first, second])
        assert set(_items(result)) == {("e01", "e02")}, field
        assert _items(result)[("e01", "e02")]["link_basis"] == {expected_basis: 1}



def test_nonstring_event_ids_cannot_become_observation_identity():
    # str(True) and str(7) satisfy the event-ID grammar, but they are not
    # literal observed event IDs. Reject rather than fabricating membership.
    for invalid in (True, False, 7, 0, {"id": "e01"}, ["e01"]):
        first = _scoped(_event(1, A))
        second = _scoped(_event(2, A))
        first["event_id"] = invalid
        result = linked_work_items([first, second])
        assert result["items"] == [], invalid
        assert result["candidate_links"] == [], invalid
        assert result["unlinked_observation_count"] == 1, invalid

    # Literal numeric-looking strings remain valid, just like ordinary IDs.
    first = _scoped(_event(1, A))
    second = _scoped(_event(2, A))
    first["event_id"], second["event_id"] = "7", "8"
    assert set(_items(linked_work_items([first, second]))) == {("7", "8")}


def test_nonstring_app_labels_cannot_create_surface_continuity():
    # Non-string values were coerced with str(...), allowing a fabricated
    # match to a real app string and a misleading weak candidate.
    for invalid in (True, 7, {"app": "Excel"}, ["Excel"]):
        first = _scoped(_event(1, app="Excel"))
        second = _scoped(_event(2, app=str(invalid)))
        first["app"] = invalid
        result = linked_work_items([first, second])
        assert result["items"] == [], invalid
        assert result["candidate_links"] == [], invalid
        assert result["unlinked_observation_count"] == 2, invalid

    # Legitimate literal strings still yield only unmerged uncertainty.
    result = linked_work_items([
        _scoped(_event(1, app="Excel")),
        _scoped(_event(2, app="Excel")),
    ])
    assert {c["relation"] for c in result["candidate_links"]} == {"surface_continuity"}
    assert all(c["merged"] is False for c in result["candidate_links"])


def test_nonstring_record_source_and_tenant_claims_cannot_fabricate_same_record():
    # The shared legacy normalizer coerces True -> "true" and 7 -> "7".
    # Two matching coerced identities are not observed source identity.
    for field, invalid in (("provider", True), ("resource_kind", True),
                           ("host", 7), ("organization_id", 7)):
        first = _scoped(_event(1, A))
        second = _scoped(_event(2, A))
        for row in (first, second):
            if field == "organization_id":
                row[field] = invalid
            else:
                row["metadata"]["resource_reference"][field] = invalid
        result = linked_work_items([first, second])
        assert result["items"] == [], (field, invalid)
        assert result["candidate_links"] == [], (field, invalid)
        assert result["conflicting_reference_groups_suppressed"] == 1, field


def test_one_malformed_record_claim_suppresses_valid_ref_and_weak_fallbacks():
    # A corrupt explicit claim must not leave the valid A observation linked
    # through a transfer or tab to an independent B record.
    for field, invalid in (("provider", True), ("resource_kind", True),
                           ("host", 7), ("organization_id", 7)):
        for signal in ("transfer", "tab"):
            options = {signal: "shared-opaque"}
            valid = _scoped(_event(1, A, **options))
            malformed = _scoped(_event(2, A, **options))
            independent = _scoped(_event(3, B, **options))
            if field == "organization_id":
                malformed[field] = invalid
            else:
                malformed["metadata"]["resource_reference"][field] = invalid
            result = linked_work_items([valid, malformed, independent])
            assert result["items"] == [], (field, signal)
            assert result["candidate_links"] == [], (field, signal)
            assert result["conflicting_reference_groups_suppressed"] == 1


def test_literal_record_identity_and_missing_optional_legacy_fields_still_link():
    # Strings that look like Python-coerced values are still legitimate
    # *literal* identities, not forbidden words or numeric-looking strings.
    for field, literal in (("provider", "true"), ("resource_kind", "true"),
                           ("host", "7"), ("organization_id", "7")):
        first = _scoped(_event(1, A))
        second = _scoped(_event(2, A))
        for row in (first, second):
            if field == "organization_id":
                row[field] = literal
            else:
                row["metadata"]["resource_reference"][field] = literal
        result = linked_work_items([first, second])
        assert set(_items(result)) == {("e01", "e02")}, field
        assert _items(result)[("e01", "e02")]["link_basis"] == {"same_record": 1}

    first, second = _event(1, A), _event(2, A)
    for row in (first, second):
        row["metadata"]["resource_reference"].pop("host")
        row.pop("organization_id", None)
    result = linked_work_items([first, second])
    assert set(_items(result)) == {("e01", "e02")}
    assert result["conflicting_reference_groups_suppressed"] == 0


def test_conflicting_duplicate_event_ids_are_withheld_before_record_or_weak_joins():
    # A reused event ID is not an authoritative "first occurrence wins"
    # observation, even when the second copy has an invalid timestamp.
    for contradiction in ("record", "tenant", "timestamp", "invalid_timestamp"):
        first = _scoped(_event(1, A, tab="same-tab", transfer="same-transfer"))
        conflicting = _scoped(_event(1, A, tab="same-tab", transfer="same-transfer"))
        if contradiction == "record":
            conflicting["metadata"]["resource_reference"]["resource_ref"] = B
        elif contradiction == "tenant":
            conflicting["organization_id"] = "tenant-b"
        elif contradiction == "timestamp":
            conflicting["observed_at"] = "2026-10-20T12:00:00+00:00"
        else:
            conflicting["observed_at"] = "invalid-timestamp"
        independent = _scoped(_event(2, A, tab="same-tab", transfer="same-transfer"))
        for events in ([first, conflicting, independent], [conflicting, first, independent]):
            result = linked_work_items(events)
            assert result["items"] == [], contradiction
            assert result["candidate_links"] == [], contradiction
            assert result["unlinked_observation_count"] == 1, contradiction


def test_exact_duplicate_event_id_is_idempotent_and_unrelated_groups_survive_collision():
    first = _scoped(_event(1, A))
    second = _scoped(_event(2, A))
    baseline = linked_work_items([first, second])
    assert set(_items(baseline)) == {("e01", "e02")}
    assert linked_work_items([first, dict(first), second]) == baseline

    conflicting = _scoped(_event(1, B))
    third = _scoped(_event(3, C))
    fourth = _scoped(_event(4, C))
    result = linked_work_items([first, conflicting, second, third, fourth])
    assert set(_items(result)) == {("e03", "e04")}
    assert result["unlinked_observation_count"] == 1


def test_explicit_null_record_claim_is_not_loose_tab_or_transfer_evidence():
    # An explicit null is a malformed reference claim, not evidence that the
    # observation was genuinely unreferenced. It must not rejoin record B.
    for signal in ("tab", "transfer"):
        options = {signal: "shared-opaque"}
        null_claim = _scoped(_event(1, app="Excel", **options))
        null_claim["metadata"]["resource_reference"] = None
        anchored = _scoped(_event(2, B, app="Excel", **options))
        for events in ([null_claim, anchored], [anchored, null_claim]):
            result = linked_work_items(events)
            assert result["items"] == [], signal
            assert result["candidate_links"] == [], signal
            assert result["unlinked_observation_count"] == 2, signal


def test_explicit_null_reference_cannot_become_surface_continuity_candidate():
    null_claim = _scoped(_event(1, app="Excel"))
    null_claim["metadata"]["resource_reference"] = None
    ordinary = _scoped(_event(2, app="Excel"))
    result = linked_work_items([null_claim, ordinary])
    assert result["items"] == []
    assert result["candidate_links"] == []
    assert result["unlinked_observation_count"] == 2


def test_missing_reference_key_still_supports_legitimate_ephemeral_evidence():
    # Absence is not the same as an explicitly rejected null claim.
    for signal in ("tab", "transfer"):
        options = {signal: "shared-opaque"}
        loose = _scoped(_event(1, app="Excel", **options))
        anchored = _scoped(_event(2, B, app="Excel", **options))
        result = linked_work_items([loose, anchored])
        assert set(_items(result)) == {("e01", "e02")}, signal
        basis = "same_tab_single_record" if signal == "tab" else "clipboard_transfer"
        assert _items(result)[("e01", "e02")]["link_basis"] == {basis: 1}

    first = _scoped(_event(1, app="Excel"))
    second = _scoped(_event(2, app="Excel"))
    result = linked_work_items([first, second])
    assert result["items"] == []
    assert {c["relation"] for c in result["candidate_links"]} == {"surface_continuity"}
    assert all(c["merged"] is False for c in result["candidate_links"])



def test_explicit_null_or_empty_scope_cannot_alias_absent_legacy_identity():
    # Legacy missing dimensions are a stable unknown identity, not a wildcard
    # for malformed *present* values. Check each dimension independently.
    for dimension in ("organization_id", "actor_id", "device_id", "session_id"):
        for invalid in (None, ""):
            for signal in ("tab", "transfer"):
                opts = {signal: "reused-legacy-token"}
                anchor = _event(1, A, **opts)
                malformed = _event(2, B if signal == "transfer" else None, **opts)
                malformed[dimension] = invalid
                for events in ([anchor, malformed], [malformed, anchor]):
                    result = linked_work_items(events)
                    assert result["items"] == [], (dimension, invalid, signal)
                    assert result["candidate_links"] == [], (dimension, invalid, signal)
                    assert result["unlinked_observation_count"] == 2

                # Both fields genuinely absent: preserve the historical
                # same-scope legacy transfer/tab behavior.
                legacy = linked_work_items([anchor, _event(2, B if signal == "transfer" else None, **opts)])
                assert set(_items(legacy)) == {("e01", "e02")}, (dimension, signal)


def test_explicit_null_or_empty_scope_cannot_create_surface_candidates():
    for dimension in ("organization_id", "actor_id", "device_id", "session_id"):
        for invalid in (None, ""):
            first = _event(1, app="Excel")
            second = _event(2, app="Excel")
            second[dimension] = invalid
            result = linked_work_items([first, second])
            assert result["items"] == []
            assert result["candidate_links"] == [], (dimension, invalid)
            assert result["unlinked_observation_count"] == 2

    legacy = linked_work_items([_event(1, app="Excel"), _event(2, app="Excel")])
    assert {c["relation"] for c in legacy["candidate_links"]} == {"surface_continuity"}
    assert all(c["merged"] is False for c in legacy["candidate_links"])


def test_malformed_ephemeral_scope_does_not_remove_exact_record_evidence():
    # Exact normalized source references are a separate signal. Invalid local
    # ephemeral scope prevents tab/transfer joins, not valid same-record links.
    for dimension in ("organization_id", "actor_id", "device_id", "session_id"):
        for invalid in (None, ""):
            first, second = _event(1, A), _event(2, A)
            first[dimension] = invalid
            second[dimension] = invalid
            result = linked_work_items([first, second])
            assert set(_items(result)) == {("e01", "e02")}, (dimension, invalid)
            assert _items(result)[("e01", "e02")]["link_basis"] == {"same_record": 1}


def test_multiplexed_transfer_id_does_not_merge_three_interleaved_records():
    # A reused transfer token is not evidence that three distinct business
    # records belong to one task. Preserve independent same-record evidence.
    events = [
        _scoped(_event(1, A, transfer="reused-token", action="copy")),
        _scoped(_event(2, B, transfer="reused-token", action="paste")),
        _scoped(_event(3, C, transfer="reused-token", action="paste")),
        _scoped(_event(4, A)),
    ]
    for order in (events, list(reversed(events))):
        result = linked_work_items(order)
        assert set(_items(result)) == {("e01", "e04")}
        assert _items(result)[("e01", "e04")]["link_basis"] == {"same_record": 1}
        assert result["unlinked_observation_count"] == 2
        assert result["ambiguous_clipboard_transfer_groups"] == 1
        assert result["link_contract"]["multiplexed_clipboard_transfer_is_unmerged"] is True
        assert "reused-token" not in json.dumps(result)


def test_two_record_transfer_with_repeated_observations_still_links():
    # Repeated observations of two actual record identities are not a
    # multiplexed third-record collision and preserve the prior contract.
    events = [
        _scoped(_event(1, A, transfer="two-records", action="copy")),
        _scoped(_event(2, A, transfer="two-records", action="copy")),
        _scoped(_event(3, B, transfer="two-records", action="paste")),
        _scoped(_event(4, B, transfer="two-records", action="paste")),
    ]
    result = linked_work_items(events)
    assert set(_items(result)) == {("e01", "e02", "e03", "e04")}
    assert _items(result)[("e01", "e02", "e03", "e04")]["link_basis"] == {
        "same_record": 2, "clipboard_transfer": 1,
    }
    assert result["ambiguous_clipboard_transfer_groups"] == 0


def test_same_token_in_other_capture_scope_is_not_a_third_record():
    # The fanout check applies per exact capture scope, not globally.
    events = [
        _scoped(_event(1, A, transfer="same-token")),
        _scoped(_event(2, B, transfer="same-token")),
        _scoped(_event(3, C, transfer="same-token"), session="another-session"),
    ]
    result = linked_work_items(events)
    assert set(_items(result)) == {("e01", "e02")}
    assert result["unlinked_observation_count"] == 1
    assert result["ambiguous_clipboard_transfer_groups"] == 0


def test_duplicate_event_id_rejects_type_coerced_nested_payload_claims():
    # Python dict equality equates bool/int and int/float values, even deep
    # inside metadata. Such conflicting observation claims cannot be
    # resolved by first-row-wins dedupe or linked to another record event.
    for left, right in ((True, 1), (False, 0), (1, 1.0), (0, 0.0)):
        first = _scoped(_event(1, A, tab="shared-tab", transfer="shared-transfer"))
        duplicate = copy.deepcopy(first)
        first["metadata"]["target"]["flags"] = [{"observed": left}]
        duplicate["metadata"]["target"]["flags"] = [{"observed": right}]
        assert first == duplicate  # Documents the unsafe Python equality.
        independent = _scoped(_event(2, A, tab="shared-tab", transfer="shared-transfer"))
        for order in ((first, duplicate), (duplicate, first)):
            result = linked_work_items([*order, independent])
            assert result["items"] == [], (left, right)
            assert result["candidate_links"] == [], (left, right)
            assert result["unlinked_observation_count"] == 1, (left, right)


def test_exact_nested_duplicate_payload_remains_idempotent_regardless_of_key_order():
    first = _scoped(_event(1, A))
    first["metadata"]["target"]["flags"] = [{"observed": True}, {"count": 1.0}]
    duplicate = copy.deepcopy(first)
    duplicate["metadata"] = dict(reversed(list(duplicate["metadata"].items())))
    independent = _scoped(_event(2, A))
    baseline = linked_work_items([first, independent])
    assert set(_items(baseline)) == {("e01", "e02")}
    assert linked_work_items([first, duplicate, independent]) == baseline
    assert linked_work_items([duplicate, first, independent]) == baseline


def test_duplicate_payload_comparison_is_bounded_and_rejects_non_json_values():
    # Duplicate equality must not traverse arbitrarily large/cyclic metadata
    # or grant first-row authority to an unsupported Python object.
    for payload in (list(range(10_050)), {"non_json_set": {1, 2}}):
        first = _scoped(_event(1, A))
        first["metadata"]["target"]["payload"] = payload
        duplicate = copy.deepcopy(first)
        independent = _scoped(_event(2, A))
        assert first == duplicate
        result = linked_work_items([first, duplicate, independent])
        assert result["items"] == []
        assert result["unlinked_observation_count"] == 1

    first = _scoped(_event(1, A))
    first["metadata"]["self_reference"] = first["metadata"]
    duplicate = copy.deepcopy(first)
    result = linked_work_items([first, duplicate, _scoped(_event(2, A))])
    assert result["items"] == []
    assert result["unlinked_observation_count"] == 1


def test_explicit_clipboard_direction_conflicts_do_not_join_two_records():
    # Same opaque token alone is not a copy -> paste edge when the captured
    # actions contradict transfer direction. Order of input rows cannot
    # choose a different work-item membership.
    for first_action, second_action in (
        ("copy", "copy"), ("paste", "paste"), ("paste", "copy"),
    ):
        events = [
            _scoped(_event(1, A, transfer="reused-direction", action=first_action)),
            _scoped(_event(2, B, transfer="reused-direction", action=second_action)),
        ]
        for order in (events, list(reversed(events))):
            result = linked_work_items(order)
            assert result["items"] == [], (first_action, second_action)
            assert result["candidate_links"] == []
            assert result["unlinked_observation_count"] == 2
            assert result["inconsistent_clipboard_transfer_direction_groups"] == 1
            assert result["ambiguous_clipboard_transfer_groups"] == 0
            assert result["link_contract"]["explicit_clipboard_direction_conflicts_are_unmerged"] is True
            assert "reused-direction" not in json.dumps(result)


def test_explicit_copy_paste_on_one_record_does_not_attach_copy_only_second_record():
    # An ordered copy -> paste wholly inside record A cannot prove a
    # cross-record transfer to B when B only has a copy observation.
    events = [
        _scoped(_event(1, A, transfer="same-record-only", action="copy")),
        _scoped(_event(2, A, transfer="same-record-only", action="paste")),
        _scoped(_event(3, B, transfer="same-record-only", action="copy")),
    ]
    result = linked_work_items(events)
    assert set(_items(result)) == {("e01", "e02")}
    assert _items(result)[("e01", "e02")]["link_basis"] == {"same_record": 1}
    assert result["unlinked_observation_count"] == 1
    assert result["inconsistent_clipboard_transfer_direction_groups"] == 1


def test_ordered_explicit_copy_to_other_record_still_joins():
    events = [
        _scoped(_event(1, A, transfer="real-direction", action="copy")),
        _scoped(_event(2, B, transfer="real-direction", action="paste")),
    ]
    for order in (events, list(reversed(events))):
        result = linked_work_items(order)
        assert set(_items(result)) == {("e01", "e02")}
        assert _items(result)[("e01", "e02")]["link_basis"] == {"clipboard_transfer": 1}
        assert result["inconsistent_clipboard_transfer_direction_groups"] == 0


def test_direction_veto_preserves_legacy_nondirectional_capture():
    # Existing non-directional capture rows do not assert copy/paste order.
    # Preserve their historical linking contract instead of inventing a
    # direction from timestamps or browser-click actions.
    events = [
        _scoped(_event(1, A, transfer="legacy-generic")),
        _scoped(_event(2, B, transfer="legacy-generic")),
    ]
    result = linked_work_items(events)
    assert set(_items(result)) == {("e01", "e02")}
    assert _items(result)[("e01", "e02")]["link_basis"] == {"clipboard_transfer": 1}
    assert result["inconsistent_clipboard_transfer_direction_groups"] == 0


def test_explicit_clipboard_equal_timestamp_does_not_infer_direction():
    copy_event = _scoped(_event(1, A, transfer="equal-time", action="copy"))
    paste_event = _scoped(_event(2, B, transfer="equal-time", action="paste"))
    paste_event["observed_at"] = copy_event["observed_at"]
    result = linked_work_items([copy_event, paste_event])
    assert result["items"] == []
    assert result["inconsistent_clipboard_transfer_direction_groups"] == 1


def test_unreferenced_direction_pair_cannot_attach_copy_only_second_record():
    # A copy -> loose paste is a genuine directional pair, but it does not
    # prove that an unrelated B copy belongs to the same business case.
    # All rows share one exact capture scope and an opaque transfer token.
    for events in (
        [
            _scoped(_event(1, A, transfer="loose-bridge", action="copy")),
            _scoped(_event(2, transfer="loose-bridge", action="paste")),
            _scoped(_event(3, B, transfer="loose-bridge", action="copy")),
        ],
        [
            _scoped(_event(1, transfer="loose-bridge", action="copy")),
            _scoped(_event(2, A, transfer="loose-bridge", action="paste")),
            _scoped(_event(3, B, transfer="loose-bridge", action="copy")),
        ],
    ):
        for ordered in (events, list(reversed(events))):
            result = linked_work_items(ordered)
            assert result["items"] == []
            assert result["candidate_links"] == []
            assert result["unlinked_observation_count"] == 3
            assert result["inconsistent_clipboard_transfer_direction_groups"] == 1
            assert "loose-bridge" not in json.dumps(result)


def test_direct_cross_record_pair_remains_valid_with_loose_observation():
    # A real A copy -> B paste still supports a two-record join; an additional
    # unreferenced row does not erase independently observed direction.
    events = [
        _scoped(_event(1, A, transfer="direct-pair", action="copy")),
        _scoped(_event(2, B, transfer="direct-pair", action="paste")),
        _scoped(_event(3, transfer="direct-pair", action="paste")),
    ]
    for ordered in (events, list(reversed(events))):
        result = linked_work_items(ordered)
        assert set(_items(result)) == {("e01", "e02", "e03")}
        assert _items(result)[("e01", "e02", "e03")]["cross_record_join"] is True
        assert result["inconsistent_clipboard_transfer_direction_groups"] == 0


def test_one_record_to_unreferenced_paste_keeps_legacy_directional_link():
    # Without a competing second record, explicit A copy -> loose paste
    # retains its previous observed-transfer membership.
    events = [
        _scoped(_event(1, A, transfer="one-record", action="copy")),
        _scoped(_event(2, transfer="one-record", action="paste")),
    ]
    for ordered in (events, list(reversed(events))):
        result = linked_work_items(ordered)
        assert set(_items(result)) == {("e01", "e02")}
        assert _items(result)[("e01", "e02")]["link_basis"] == {"clipboard_transfer": 1}
        assert result["inconsistent_clipboard_transfer_direction_groups"] == 0
