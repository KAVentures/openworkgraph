from __future__ import annotations

"""Precision/recall floors for OWG's own exact-record grouping (roadmap #264 Stage 2).

These score the shipped ``record_case_lanes`` against the event-level answer key
of the interleaved reconstruction corpus, including its family-split holdout,
missing observations and deleted evidence.
"""

from collections import defaultdict

from evals.reconstruction.ablations import ablate_event
from evals.reconstruction.baselines import score_baselines
from evals.reconstruction.fixtures import load_cases
from evals.reconstruction.owg_native import _ref, predict_linked_items, predict_record_lanes, score_record_lanes
from evals.reconstruction.prepare import split_cases


def _macro(report: dict) -> dict:
    return report["family_macro"]


def test_record_lanes_meet_regression_floors_on_all_and_holdout_cases():
    cases = load_cases()
    overall = _macro(score_record_lanes(cases))
    holdout = _macro(score_record_lanes(split_cases(cases, "holdout")))
    # Floors sit just below measured values (0.671 / 0.021 and 0.503 / 0.037)
    # so a grouping regression fails here instead of in a later model study.
    assert overall["workflow_assignment_f1"] >= 0.65, overall
    assert overall["cross_workflow_contamination"] <= 0.03, overall
    assert holdout["workflow_assignment_f1"] >= 0.45, holdout
    assert holdout["cross_workflow_contamination"] <= 0.05, holdout
    # Lanes only publish explicitly referenced records, never interruptions.
    assert overall["interruption_rejection"] == 1.0
    assert holdout["interruption_rejection"] == 1.0


def test_record_lanes_are_more_precise_than_every_naive_baseline():
    cases = load_cases()
    owg = _macro(score_record_lanes(cases))
    for name, report in score_baselines(cases).items():
        baseline = _macro(report)
        assert owg["cross_workflow_contamination"] < baseline["cross_workflow_contamination"], name
        assert owg["workflow_assignment_f1"] > baseline["workflow_assignment_f1"], name


def test_contamination_only_comes_from_one_record_shared_by_several_true_workflows():
    # Interleaving must never merge different records by temporal proximity.
    for case in load_cases():
        truth = {
            event_id: index
            for index, workflow in enumerate(case["ground_truth"]["workflows"])
            for event_id in workflow["event_ids"]
        }
        refs = {str(e["event_id"]): _ref(e) for e in case["source_events"]}
        for lane in predict_record_lanes(case)["workflows"]:
            assert len({refs[event_id] for event_id in lane["event_ids"]}) == 1, case["case_id"]
            workflows = {truth.get(event_id) for event_id in lane["event_ids"]}
            if len(workflows) > 1:
                shared = defaultdict(set)
                for event_id, ref in refs.items():
                    if event_id in truth:
                        shared[ref].add(truth[event_id])
                assert len(shared[refs[lane["event_ids"][0]]]) > 1, case["case_id"]


def test_missing_resource_observations_fail_closed_instead_of_guessing():
    cases = [{
        **case,
        "presented_evidence": [ablate_event(e, "resource_references") for e in case["presented_evidence"]],
        "source_events": [ablate_event(e, "resource_references") for e in case["source_events"]],
    } for case in load_cases()]
    for case in cases:
        prediction = predict_record_lanes(case)
        assert prediction["workflows"] == []
        assert len(prediction["unassigned_event_ids"]) == len(case["presented_evidence"])
    assert _macro(score_record_lanes(cases))["cross_workflow_contamination"] == 0.0


def test_deleted_evidence_never_reappears_in_lanes():
    for case in load_cases():
        refs = sorted({_ref(e) for e in case["source_events"] if _ref(e)})
        if not refs:
            continue
        revoked = refs[0]
        deleted = {str(e["event_id"]) for e in case["source_events"] if _ref(e) == revoked}
        kept = [e for e in case["source_events"] if str(e["event_id"]) not in deleted]
        remaining = {**case, "source_events": kept,
                     "presented_evidence": [e for e in case["presented_evidence"] if str(e["event_id"]) not in deleted]}
        prediction = predict_record_lanes(remaining)
        published = {event_id for lane in prediction["workflows"] for event_id in lane["event_ids"]}
        assert not published & deleted, case["case_id"]
        assert not deleted & set(prediction["unassigned_event_ids"]), case["case_id"]


# Adapter-integrity checks: the production record-lane API intentionally
# returns no event IDs. These tests exercise the evaluator's private projection
# without changing the preregistered corpus, scorer, holdout or release floors.
def _lane_projection_event(event_id, ref, observed_at, *, provider="github"):
    return {
        "event_id": event_id,
        "observed_at": observed_at,
        "event_type": "browser_click",
        "organization_id": "tenant-a",
        "metadata": {"resource_reference": {
            "resource_ref": ref,
            "provider": provider,
            "resource_kind": "issue",
            "host": "github.com",
        }},
    }


def _lane_projection_case(source, presented=None):
    return {
        "case_id": "synthetic-lane-projection",
        "source_events": source,
        "presented_evidence": source if presented is None else presented,
    }


def test_lane_projection_excludes_invalid_timestamp_and_unpresented_source_rows():
    ref = "owg:r:" + "a" * 32
    valid_1 = _lane_projection_event("valid1", ref, "2026-10-20T10:00:00Z")
    invalid = _lane_projection_event("badtime", ref, "not-a-timestamp")
    valid_2 = _lane_projection_event("valid2", ref, "2026-10-20T10:01:00Z")
    source_only = _lane_projection_event("hidden", ref, "2026-10-20T10:02:00Z")
    result = predict_record_lanes(_lane_projection_case(
        [valid_1, invalid, valid_2, source_only],
        [valid_1, invalid, valid_2],
    ))
    assert [lane["event_ids"] for lane in result["workflows"]] == [["valid1", "valid2"]]
    assert result["unassigned_event_ids"] == ["badtime"]
    assert "hidden" not in str(result)


def test_lane_projection_uses_first_admitted_duplicate_not_earliest_duplicate():
    ref = "owg:r:" + "a" * 32
    first = _lane_projection_event("dup", ref, "2026-10-20T10:02:00Z")
    other = _lane_projection_event("other", ref, "2026-10-20T10:01:00Z")
    conflicting_later_row = _lane_projection_event("dup", ref, "2026-10-20T10:00:00Z")
    for source in ([first, other, conflicting_later_row],
                   [first, conflicting_later_row, other]):
        result = predict_record_lanes(_lane_projection_case(source))
        assert [lane["ordered_event_ids"] for lane in result["workflows"]] == [
            ["other", "dup"]
        ]
        assert result["unassigned_event_ids"] == []


def test_lane_projection_withholds_reused_event_id_across_distinct_source_records():
    a, b = "owg:r:" + "a" * 32, "owg:r:" + "b" * 32
    source = [
        _lane_projection_event("reused", a, "2026-10-20T10:00:00Z"),
        _lane_projection_event("a2", a, "2026-10-20T10:02:00Z"),
        _lane_projection_event("reused", b, "2026-10-20T10:01:00Z"),
        _lane_projection_event("b2", b, "2026-10-20T10:03:00Z"),
    ]
    for rows in (source, list(reversed(source))):
        result = predict_record_lanes(_lane_projection_case(rows))
        assert {tuple(lane["event_ids"]) for lane in result["workflows"]} == {
            ("a2",), ("b2",)
        }
        assert result["unassigned_event_ids"] == ["reused"]
        assert sum("reused" in lane["event_ids"] for lane in result["workflows"]) == 0


def test_lane_projection_refuses_coerced_nonstring_event_id_but_preserves_valid_rows():
    ref = "owg:r:" + "a" * 32
    source = [
        _lane_projection_event(17, ref, "2026-10-20T10:00:00Z"),
        _lane_projection_event("valid1", ref, "2026-10-20T10:01:00Z"),
        _lane_projection_event("valid2", ref, "2026-10-20T10:02:00Z"),
    ]
    result = predict_record_lanes(_lane_projection_case(source))
    assert [lane["event_ids"] for lane in result["workflows"]] == [["valid1", "valid2"]]
    assert result["unassigned_event_ids"] == []
    assert "17" not in str(result)  # numeric IDs never become scored strings


def test_lane_projection_nonliteral_first_duplicate_consumes_source_admission_slot():
    # record_case_lanes coerces IDs before de-duplicating them, while the
    # evaluator must never coerce a nonliteral ID into scored evidence.
    # A numeric/bool source row can therefore suppress a later string ID.
    ref = "owg:r:" + "a" * 32
    for nonliteral, literal in ((17, "17"), (True, "True"), (1.5, "1.5")):
        first = _lane_projection_event(nonliteral, ref, "2026-10-20T10:00:00Z")
        literal_row = _lane_projection_event(literal, ref, "2026-10-20T10:01:00Z")
        other = _lane_projection_event("other", ref, "2026-10-20T10:02:00Z")
        last = _lane_projection_event("last", ref, "2026-10-20T10:03:00Z")
        presented = [literal_row, other, last]
        # The source engine admits the nonliteral first, suppressing the
        # later literal duplicate; it must not be resurrected by projection.
        result = predict_record_lanes(_lane_projection_case(
            [first, literal_row, other, last], presented
        ))
        assert [w["event_ids"] for w in result["workflows"]] == [
            ["other", "last"]
        ], nonliteral
        assert result["unassigned_event_ids"] == [literal], nonliteral

        # Conversely, a literal first occurrence remains scoreable, even
        # when a later nonliteral source row has an earlier timestamp.
        result = predict_record_lanes(_lane_projection_case(
            [literal_row, first, other, last], presented
        ))
        assert [w["event_ids"] for w in result["workflows"]] == [
            [literal, "other", "last"]
        ], nonliteral
        assert result["unassigned_event_ids"] == [], nonliteral

    # Falsey numeric zero is normalized by production to an empty ID, so
    # it cannot consume the valid literal string "0" duplicate slot.
    zero = _lane_projection_event(0, ref, "2026-10-20T10:00:00Z")
    literal_zero = _lane_projection_event("0", ref, "2026-10-20T10:01:00Z")
    other = _lane_projection_event("other", ref, "2026-10-20T10:02:00Z")
    result = predict_record_lanes(_lane_projection_case(
        [zero, literal_zero, other], [literal_zero, other]
    ))
    assert [w["event_ids"] for w in result["workflows"]] == [["0", "other"]]
    assert result["unassigned_event_ids"] == []


def test_lane_projection_preserves_offset_order_and_shipped_identity_conflict_veto():
    ref = "owg:r:" + "a" * 32
    first = _lane_projection_event("first", ref, "2026-10-20T11:00:00+01:00")
    second = _lane_projection_event("second", ref, "2026-10-20T10:01:00Z")
    result = predict_record_lanes(_lane_projection_case([second, first]))
    assert [lane["ordered_event_ids"] for lane in result["workflows"]] == [
        ["first", "second"]
    ]
    conflicting = _lane_projection_event("other-provider", ref,
                                          "2026-10-20T10:02:00Z", provider="jira")
    vetoed = predict_record_lanes(_lane_projection_case([first, second, conflicting]))
    assert vetoed["workflows"] == []
    assert set(vetoed["unassigned_event_ids"]) == {"first", "second", "other-provider"}


def test_lane_projection_requires_visible_record_identity_not_just_matching_id():
    from copy import deepcopy

    a, b = "owg:r:" + "a" * 32, "owg:r:" + "b" * 32
    source = [
        _lane_projection_event("first", a, "2026-10-20T10:00:00Z"),
        _lane_projection_event("second", a, "2026-10-20T10:01:00Z"),
    ]
    # The source says A, but the actual presented evidence says B. Reusing
    # the same event ID cannot turn a source-only A observation into visible A.
    visible = deepcopy(source)
    visible[0]["metadata"]["resource_reference"]["resource_ref"] = b
    result = predict_record_lanes(_lane_projection_case(source, visible))
    assert [lane["event_ids"] for lane in result["workflows"]] == [["second"]]
    assert result["unassigned_event_ids"] == ["first"]


def test_lane_projection_withholds_conflicting_or_missing_visible_provenance():
    from copy import deepcopy

    ref = "owg:r:" + "a" * 32
    source = [
        _lane_projection_event("first", ref, "2026-10-20T10:00:00Z"),
        _lane_projection_event("second", ref, "2026-10-20T10:01:00Z"),
    ]
    for corruption in ("provider", "kind", "host", "missing", "malformed"):
        visible = deepcopy(source)
        identity = visible[0]["metadata"]["resource_reference"]
        if corruption == "provider":
            identity["provider"] = "jira"
        elif corruption == "kind":
            identity["resource_kind"] = "pull_request"
        elif corruption == "host":
            identity["host"] = "another.example"
        elif corruption == "missing":
            visible[0]["metadata"] = {}
        else:
            visible[0]["metadata"] = {"resource_reference": True}
        result = predict_record_lanes(_lane_projection_case(source, visible))
        assert [lane["event_ids"] for lane in result["workflows"]] == [["second"]], corruption
        assert result["unassigned_event_ids"] == ["first"], corruption


def test_lane_projection_withholds_visible_id_reused_under_two_source_identities():
    from copy import deepcopy

    a, b = "owg:r:" + "a" * 32, "owg:r:" + "b" * 32
    source = [
        _lane_projection_event("shared", a, "2026-10-20T10:00:00Z"),
        _lane_projection_event("second", a, "2026-10-20T10:01:00Z"),
    ]
    visible = deepcopy(source)
    conflicting = deepcopy(visible[0])
    conflicting["metadata"]["resource_reference"]["resource_ref"] = b
    for rows in ([*visible, conflicting], [conflicting, *visible]):
        result = predict_record_lanes(_lane_projection_case(source, rows))
        assert [lane["event_ids"] for lane in result["workflows"]] == [["second"]]
        assert result["unassigned_event_ids"] == ["shared"]
        assert "shared" not in str(result["workflows"])


def test_lane_projection_accepts_normalized_visible_identity_without_tenant_leak():
    from copy import deepcopy

    ref = "owg:r:" + "a" * 32
    source = [
        _lane_projection_event("first", ref, "2026-10-20T10:00:00Z"),
        _lane_projection_event("second", ref, "2026-10-20T10:01:00Z"),
    ]
    visible = deepcopy(source)
    for row in visible:
        row.pop("organization_id")  # rich_evidence_row(include_identity=False)
        identity = row["metadata"]["resource_reference"]
        identity["resource_ref"] = ref.upper()
        identity["provider"] = "GITHUB"
        identity["resource_kind"] = "ISSUE"
        identity["host"] = "GITHUB.COM"
    result = predict_record_lanes(_lane_projection_case(source, visible))
    assert [lane["ordered_event_ids"] for lane in result["workflows"]] == [
        ["first", "second"]
    ]
    assert result["unassigned_event_ids"] == []


def test_lane_projection_requires_source_admitted_instant_to_be_visible():
    from copy import deepcopy

    ref = "owg:r:" + "a" * 32
    source = [
        _lane_projection_event("first", ref, "2026-10-20T10:00:00.000001Z"),
        _lane_projection_event("second", ref, "2026-10-20T10:01:00Z"),
    ]
    visible = deepcopy(source)
    # Same event ID, record, provider, kind and host, but not the observed
    # instant admitted by the source engine: this is not the same observation.
    visible[0]["observed_at"] = "2026-10-20T10:00:00.000002Z"
    result = predict_record_lanes(_lane_projection_case(source, visible))
    assert [lane["event_ids"] for lane in result["workflows"]] == [["second"]]
    assert result["unassigned_event_ids"] == ["first"]

    # Malformed or absent visible timestamps cannot be repaired from source.
    for invalid in ("invalid-time", None, "2026-10-20T10:00:00"):
        malformed = deepcopy(source)
        malformed[0]["observed_at"] = invalid
        result = predict_record_lanes(_lane_projection_case(source, malformed))
        assert [lane["event_ids"] for lane in result["workflows"]] == [["second"]]
        assert result["unassigned_event_ids"] == ["first"]


def test_lane_projection_accepts_offset_equivalent_instant_and_first_admitted_duplicate():
    from copy import deepcopy

    ref = "owg:r:" + "a" * 32
    first = _lane_projection_event("first", ref, "2026-10-20T10:00:00.000001Z")
    second = _lane_projection_event("second", ref, "2026-10-20T10:01:00Z")
    duplicate = _lane_projection_event("first", ref, "2026-10-20T10:03:00Z")
    source = [first, second, duplicate]

    # The source engine keeps the first valid duplicate; later duplicate
    # timestamps do not override its admitted instant.
    visible = deepcopy(source)
    visible[0]["observed_at"] = "2026-10-20T11:00:00.000001+01:00"
    result = predict_record_lanes(_lane_projection_case(source, visible))
    assert [lane["ordered_event_ids"] for lane in result["workflows"]] == [
        ["first", "second"]
    ]
    assert result["unassigned_event_ids"] == []

    # Showing only the later same-ID duplicate must not substitute for the
    # first source-admitted observation, even with identical resource identity.
    only_later = [deepcopy(duplicate), deepcopy(second)]
    result = predict_record_lanes(_lane_projection_case(source, only_later))
    assert [lane["event_ids"] for lane in result["workflows"]] == [["second"]]
    assert result["unassigned_event_ids"] == ["first"]


# Linked-item evaluation admission: source-only evidence must not act as a
# bridge between otherwise distinct visible items. The production linker
# remains unchanged and retains its original scope/isolation safeguards.
def _linked_eval_event(event_id, ref, at, *, action="click", transfer=None):
    row = _lane_projection_event(event_id, ref, at)
    row.update({
        "actor_id": "human", "device_id": "device:one",
        "session_id": "session:one", "app": "Google Chrome",
        "source": "browser_extension", "event_type": action,
    })
    if transfer is not None:
        row["metadata"]["clipboard_transfer_id"] = transfer
        row["metadata"]["clipboard_source_observed"] = True
    return row


def _linked_eval_rows():
    a, b = "owg:r:" + "a" * 32, "owg:r:" + "b" * 32
    return [
        _linked_eval_event("a1", a, "2026-10-20T10:00:00Z",
                           action="copy", transfer="xfer-1"),
        _linked_eval_event("a2", a, "2026-10-20T10:00:10Z"),
        _linked_eval_event("b1", b, "2026-10-20T10:00:20Z",
                           action="paste", transfer="xfer-1"),
        _linked_eval_event("b2", b, "2026-10-20T10:00:30Z"),
        _linked_eval_event("b3", b, "2026-10-20T10:00:40Z"),
    ]


def _linked_eval_case(source, visible):
    from shared.evidence import rich_evidence_row
    return _lane_projection_case(
        source, [rich_evidence_row(event, include_identity=False) for event in visible]
    )


def test_linked_projection_source_only_paste_cannot_bridge_visible_records():
    source = _linked_eval_rows()
    visible = [row for row in source if row["event_id"] != "b1"]
    for accepting_candidates in (False, True):
        result = predict_linked_items(
            _linked_eval_case(source, visible), accept_candidates=accepting_candidates
        )
        assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
            frozenset({"a1", "a2"}), frozenset({"b2", "b3"})
        }
        assert "b1" not in str(result)


def test_linked_projection_preserves_visible_authorized_transfer_and_offset_instant():
    from copy import deepcopy
    source = _linked_eval_rows()
    visible = deepcopy(source)
    visible[2]["observed_at"] = "2026-10-20T11:00:20+01:00"
    result = predict_linked_items(_linked_eval_case(source, visible))
    assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
        frozenset({"a1", "a2", "b1", "b2", "b3"})
    }
    assert result["unassigned_event_ids"] == []


def test_linked_projection_rejects_changed_visible_link_authority_and_time():
    from copy import deepcopy
    source = _linked_eval_rows()
    for corruption in ("token", "timestamp", "app", "session", "source", "ref"):
        visible = deepcopy(source)
        paste = visible[2]
        if corruption == "token":
            paste["metadata"]["clipboard_transfer_id"] = "another-transfer"
        elif corruption == "timestamp":
            paste["observed_at"] = "2026-10-20T10:00:20.000001Z"
        elif corruption == "app":
            paste["app"] = "Another app"
        elif corruption == "session":
            paste["session_id"] = "session:other"
        elif corruption == "source":
            paste["source"] = "desktop_sensor"
        else:
            paste["metadata"]["resource_reference"]["resource_ref"] = (
                "owg:r:" + "c" * 32
            )
        result = predict_linked_items(_linked_eval_case(source, visible))
        assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
            frozenset({"a1", "a2"}), frozenset({"b2", "b3"})
        }, corruption
        assert result["unassigned_event_ids"] == ["b1"], corruption


def test_linked_projection_withholds_conflicting_duplicate_source_or_visible_ids():
    from copy import deepcopy
    source = _linked_eval_rows()
    conflicting_source = deepcopy(source[2])
    conflicting_source["actor_id"] = "other-actor"
    visible = deepcopy(source)
    for mutated_source, mutated_visible in (
        ([*source, conflicting_source], visible),
        (source, [*visible, {**deepcopy(visible[2]), "observed_at": "2026-10-20T10:00:21Z"}]),
    ):
        result = predict_linked_items(_linked_eval_case(mutated_source, mutated_visible))
        assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
            frozenset({"a1", "a2"}), frozenset({"b2", "b3"})
        }
        assert "b1" not in str(result["workflows"])


def test_linked_projection_duplicate_replay_is_idempotent_when_byte_equivalent():
    from copy import deepcopy
    source = _linked_eval_rows()
    replay = deepcopy(source[2])
    result = predict_linked_items(_linked_eval_case([*source, replay], source))
    assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
        frozenset({"a1", "a2", "b1", "b2", "b3"})
    }


def test_linked_projection_refuses_bool_integer_equality_as_transfer_provenance():
    from copy import deepcopy
    source = _linked_eval_rows()
    visible = deepcopy(source)
    # Python considers True == 1, but those are different JSON claims.
    visible[2]["metadata"]["clipboard_source_observed"] = 1
    result = predict_linked_items(_linked_eval_case(source, visible))
    assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
        frozenset({"a1", "a2"}), frozenset({"b2", "b3"})
    }
    duplicate = deepcopy(source[2])
    duplicate["metadata"]["clipboard_source_observed"] = 1
    result = predict_linked_items(_linked_eval_case([*source, duplicate], source))
    assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
        frozenset({"a1", "a2"}), frozenset({"b2", "b3"})
    }


def test_record_lane_projection_never_resurrects_explicitly_revoked_source():
    # A stale visible/export snapshot is not permission to rebuild source lanes
    # after the authoritative source list was deleted or revoked.
    ref = "owg:r:" + "a" * 32
    rows = [
        _lane_projection_event("first", ref, "2026-10-20T10:00:00Z"),
        _lane_projection_event("second", ref, "2026-10-20T10:01:00Z"),
    ]
    for revoked in ([], None):
        case = _lane_projection_case(rows)
        case["source_events"] = revoked
        result = predict_record_lanes(case)
        assert result["workflows"] == []
        assert result["unassigned_event_ids"] == ["first", "second"]

    # Backward compatibility: only ABSENCE of source_events means the caller
    # supplied a legacy presented-only case, not an explicit revocation.
    legacy = _lane_projection_case(rows)
    del legacy["source_events"]
    result = predict_record_lanes(legacy)
    assert [lane["event_ids"] for lane in result["workflows"]] == [
        ["first", "second"]
    ]


def test_linked_projection_never_reconstructs_revoked_source_even_in_candidate_mode():
    source = _linked_eval_rows()
    case = _linked_eval_case(source, source)
    visible_ids = [row["event_id"] for row in case["presented_evidence"]]
    for revoked in ([], None):
        for accepting_candidates in (False, True):
            revoked_case = {**case, "source_events": revoked}
            result = predict_linked_items(
                revoked_case, accept_candidates=accepting_candidates
            )
            assert result["workflows"] == []
            assert result["unassigned_event_ids"] == visible_ids

    # A missing source key is the old presented-only contract, and must still
    # retain independently visible same-record evidence and transfer links.
    legacy = {key: value for key, value in case.items() if key != "source_events"}
    result = predict_linked_items(legacy)
    assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
        frozenset(visible_ids)
    }
    assert result["unassigned_event_ids"] == []


def test_linked_projection_rejects_type_coerced_dual_transfer_tokens():
    from copy import deepcopy
    for metadata_value, top_level_value in (
        (1, True), (True, True), ("xfer-1", True), (1.0, 1),
    ):
        source = deepcopy(_linked_eval_rows())
        case = _linked_eval_case(source, source)
        for index in (0, 2):
            for row in (case["source_events"][index],
                        case["presented_evidence"][index]):
                row["metadata"]["clipboard_transfer_id"] = metadata_value
                row["clipboard_transfer_id"] = top_level_value
        result = predict_linked_items(case)
        assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
            frozenset({"b2", "b3"})
        }, (metadata_value, top_level_value)
        assert {"a1", "b1"} <= set(result["unassigned_event_ids"])


def test_linked_projection_rejects_nonliteral_metadata_only_tokens():
    from copy import deepcopy
    for malformed in (True, 1, 1.0, None, "", "bad token"):
        source = deepcopy(_linked_eval_rows())
        case = _linked_eval_case(source, source)
        for index in (0, 2):
            for row in (case["source_events"][index],
                        case["presented_evidence"][index]):
                row["metadata"]["clipboard_transfer_id"] = malformed
                row.pop("clipboard_transfer_id", None)
        result = predict_linked_items(case)
        assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
            frozenset({"b2", "b3"})
        }, malformed

    # Literal metadata-only strings remain supported, including legacy rows
    # without top-level convenience copies.
    source = deepcopy(_linked_eval_rows())
    case = _linked_eval_case(source, source)
    for index in (0, 2):
        case["presented_evidence"][index].pop("clipboard_transfer_id", None)
    result = predict_linked_items(case)
    assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
        frozenset({"a1", "a2", "b1", "b2", "b3"})
    }


def test_native_predictions_do_not_invent_or_duplicate_presented_event_ids():
    from copy import deepcopy
    source = _linked_eval_rows()
    case = _linked_eval_case(source, source)
    case["presented_evidence"].extend([
        {"event_id": True}, {"event_id": 123}, {"event_id": None},
        {"event_id": "contains space"}, {"event_id": "orphan"},
        {"event_id": "orphan"}, {"not_an_event": True}, None, "bad row",
        deepcopy(case["presented_evidence"][0]),
    ])
    for predictor in (predict_record_lanes, predict_linked_items):
        result = predictor(case)
        assert result["unassigned_event_ids"] == ["orphan"]
        assigned = [event_id for w in result["workflows"] for event_id in w["event_ids"]]
        assert len(assigned) == len(set(assigned))
        assert not set(assigned) & {"True", "123", "None", "contains space"}



def test_linked_projection_rejects_type_coerced_record_identity_claims():
    from copy import deepcopy

    # The shipped linker normalizes provider/kind/host values with str(...).
    # An identical malformed source and visible claim must not manufacture
    # valid same-record or cross-record clipboard authority in the evaluator.
    for field, value in (
        ("provider", True), ("provider", None),
        ("resource_kind", True), ("host", 123), ("host", False),
        ("resource_reference", True),
    ):
        source = deepcopy(_linked_eval_rows())
        for row in source[:2]:
            if field == "resource_reference":
                row["metadata"]["resource_reference"] = value
            else:
                row["metadata"]["resource_reference"][field] = value
        result = predict_linked_items(_linked_eval_case(source, source))
        assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
            frozenset({"b1", "b2", "b3"})
        }, (field, value)
        assert {"a1", "a2"} <= set(result["unassigned_event_ids"])

    # Literal identity values, even when case-normalized by the production
    # engine, remain usable; no synthetic score is claimed from this control.
    source = deepcopy(_linked_eval_rows())
    for row in source[:2]:
        row["metadata"]["resource_reference"]["provider"] = "GITHUB"
        row["metadata"]["resource_reference"]["host"] = "GITHUB.COM"
    result = predict_linked_items(_linked_eval_case(source, source))
    assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
        frozenset({"a1", "a2", "b1", "b2", "b3"})
    }


def test_linked_projection_rejects_nonliteral_app_surface_candidates():
    from copy import deepcopy

    a, b = "owg:r:" + "a" * 32, "owg:r:" + "b" * 32
    for malformed in (True, 123, {"label": "Browser"}):
        source = [
            _linked_eval_event("loose1", a, "2026-10-20T10:00:00Z"),
            _linked_eval_event("loose2", b, "2026-10-20T10:00:10Z"),
        ]
        for row in source:
            row["metadata"].pop("resource_reference")
            row["app"] = malformed
        result = predict_linked_items(
            _linked_eval_case(source, source), accept_candidates=True
        )
        assert result["workflows"] == [], malformed
        assert result["unassigned_event_ids"] == ["loose1", "loose2"]

    # Valid same-app continuity remains explicitly ambiguous in normal mode
    # and is joined only by the opt-in candidate-accepting diagnostic.
    source = [
        _linked_eval_event("loose1", a, "2026-10-20T10:00:00Z"),
        _linked_eval_event("loose2", b, "2026-10-20T10:00:10Z"),
    ]
    for row in source:
        row["metadata"].pop("resource_reference")
        row["app"] = "Browser"
    case = _linked_eval_case(source, source)
    assert predict_linked_items(case)["workflows"] == []
    assert [w["event_ids"] for w in predict_linked_items(
        case, accept_candidates=True
    )["workflows"]] == [["loose1", "loose2"]]


def test_linked_projection_respects_first_normalized_source_id_admission():
    # The shipped linker normalizes event IDs before its first-valid-timestamp
    # duplicate check. A nonliteral first ID can suppress a later literal ID,
    # but may never itself become scored visible membership.
    from copy import deepcopy

    ref = "owg:r:" + "a" * 32
    at = "2026-10-20T10:00:00Z"
    anchor = _linked_eval_event("anchor", ref, "2026-10-20T10:01:00Z")
    for nonliteral, literal in ((17, "17"), (True, "True"), (1.0, "1.0")):
        first = _linked_eval_event(nonliteral, ref, at)
        later = _linked_eval_event(literal, ref, at)
        visible = [later, anchor]
        for accept_candidates in (False, True):
            result = predict_linked_items(
                _linked_eval_case([first, later, anchor], visible),
                accept_candidates=accept_candidates,
            )
            assert result["workflows"] == [], (nonliteral, accept_candidates)
            assert result["unassigned_event_ids"] == [literal, "anchor"]

        # Reverse admission: the literal valid ID is the first source row
        # and remains linkable; a later nonliteral duplicate cannot veto it.
        result = predict_linked_items(
            _linked_eval_case([later, first, anchor], visible)
        )
        assert [w["event_ids"] for w in result["workflows"]] == [
            [literal, "anchor"]
        ], nonliteral
        assert result["unassigned_event_ids"] == []

        # An invalid-timestamp nonliteral row never consumes the slot.
        invalid = deepcopy(first)
        invalid["observed_at"] = "not-a-timestamp"
        result = predict_linked_items(
            _linked_eval_case([invalid, later, anchor], visible)
        )
        assert [w["event_ids"] for w in result["workflows"]] == [
            [literal, "anchor"]
        ], nonliteral

    # Production treats falsey IDs as empty before normalizing. They do
    # not reserve the later literal "0" or "False" source admission slot.
    for nonliteral, literal in ((0, "0"), (False, "False"), (None, "None")):
        first = _linked_eval_event(nonliteral, ref, at)
        later = _linked_eval_event(literal, ref, at)
        result = predict_linked_items(
            _linked_eval_case([first, later, anchor], [later, anchor])
        )
        assert [w["event_ids"] for w in result["workflows"]] == [
            [literal, "anchor"]
        ], nonliteral


def test_linked_projection_ignores_invalid_timestamp_duplicates_before_and_after_valid_source():
    """Production skips invalid timestamps before first-ID admission.

    An invalid duplicate must not veto a valid observed transfer merely
    because its record, scope, or clipboard claim conflicts with the first
    timestamp-valid source observation. This checks evaluator parity only.
    """
    from copy import deepcopy

    source = _linked_eval_rows()
    invalid = deepcopy(source[2])
    invalid["observed_at"] = "not-a-timestamp"
    invalid["actor_id"] = "other-actor"
    invalid["metadata"]["resource_reference"]["provider"] = "other-provider"
    invalid["metadata"]["clipboard_transfer_id"] = "other-transfer"
    for insertion in (2, 3):
        with_invalid = deepcopy(source)
        with_invalid.insert(insertion, deepcopy(invalid))
        for accepting_candidates in (False, True):
            result = predict_linked_items(
                _linked_eval_case(with_invalid, source),
                accept_candidates=accepting_candidates,
            )
            assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
                frozenset({"a1", "a2", "b1", "b2", "b3"})
            }, (insertion, accepting_candidates)
            assert result["unassigned_event_ids"] == []


def test_linked_projection_valid_conflicting_duplicate_still_vetoes_transfer():
    """Skipping malformed timestamps must not weaken valid duplicate vetoes."""
    from copy import deepcopy

    source = _linked_eval_rows()
    conflict = deepcopy(source[2])
    conflict["actor_id"] = "other-actor"
    for insertion in (2, 3):
        with_conflict = deepcopy(source)
        with_conflict.insert(insertion, deepcopy(conflict))
        for accepting_candidates in (False, True):
            result = predict_linked_items(
                _linked_eval_case(with_conflict, source),
                accept_candidates=accepting_candidates,
            )
            assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
                frozenset({"a1", "a2"}), frozenset({"b2", "b3"})
            }, (insertion, accepting_candidates)
            assert result["unassigned_event_ids"] == ["b1"]


def test_linked_projection_large_duplicate_replay_remains_idempotent():
    """Thousands of replays cannot create new members or bridge hidden work.

    This stresses the evaluator's constant-per-ID admission bookkeeping,
    without asserting a timing/memory threshold on different CI machines.
    """
    from copy import deepcopy

    original = _linked_eval_rows()
    replays = [deepcopy(original[2]) for _ in range(2048)]
    for accepting_candidates in (False, True):
        case = _linked_eval_case([*original, *replays], [*original, *replays])
        result = predict_linked_items(case, accept_candidates=accepting_candidates)
        assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
            frozenset({"a1", "a2", "b1", "b2", "b3"})
        }
        assert result["unassigned_event_ids"] == []


def test_linked_projection_late_duplicate_conflicts_are_irreversible():
    """One contradictory late replay vetoes membership despite valid copies."""
    from copy import deepcopy

    original = _linked_eval_rows()
    replays = [deepcopy(original[2]) for _ in range(128)]
    bad_source = deepcopy(original[2])
    bad_source["actor_id"] = "another-actor"
    bad_visible = deepcopy(original[2])
    bad_visible["observed_at"] = "2026-10-20T10:00:21Z"

    for accepting_candidates in (False, True):
        # A source-side contradiction is withheld even when the visible
        # evidence is otherwise consistent.
        source_conflict = _linked_eval_case(
            [*original, *replays, bad_source], original
        )
        result = predict_linked_items(
            source_conflict, accept_candidates=accepting_candidates
        )
        assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
            frozenset({"a1", "a2"}), frozenset({"b2", "b3"})
        }
        assert result["unassigned_event_ids"] == ["b1"]

        # A visible late duplicate cannot repair a contradictory timestamp
        # or substitute for the first source-admitted observation.
        visible_conflict = _linked_eval_case(
            original, [*original, *replays, bad_visible]
        )
        result = predict_linked_items(
            visible_conflict, accept_candidates=accepting_candidates
        )
        assert {frozenset(w["event_ids"]) for w in result["workflows"]} == {
            frozenset({"a1", "a2"}), frozenset({"b2", "b3"})
        }
        assert result["unassigned_event_ids"] == ["b1"]


def test_linked_projection_first_nonliteral_and_invalid_replays_keep_admission_order():
    """Bounded replay state retains production's first valid normalized ID."""
    from copy import deepcopy

    original = _linked_eval_rows()
    ref = original[2]["metadata"]["resource_reference"]["resource_ref"]
    numeric = _linked_eval_event(17, ref, "2026-10-20T10:00:20Z")
    literal = _linked_eval_event("17", ref, "2026-10-20T10:00:20Z")
    invalid = deepcopy(literal)
    invalid["observed_at"] = "invalid"
    anchor = _linked_eval_event("anchor", ref, "2026-10-20T10:00:30Z")

    for accepting_candidates in (False, True):
        # An earlier numeric source ID reserves "17" even after many
        # malformed-timestamp replays; it must not become scored membership.
        case = _linked_eval_case(
            [numeric, *[deepcopy(invalid) for _ in range(64)], literal, anchor],
            [literal, anchor],
        )
        result = predict_linked_items(case, accept_candidates=accepting_candidates)
        assert result["workflows"] == []
        assert result["unassigned_event_ids"] == ["17", "anchor"]

        # Invalid timestamps do not reserve the normalized ID slot, so the
        # first valid literal row can still link with its same-record anchor.
        case = _linked_eval_case(
            [*[deepcopy(invalid) for _ in range(64)], literal, anchor],
            [literal, anchor],
        )
        result = predict_linked_items(case, accept_candidates=accepting_candidates)
        assert [w["event_ids"] for w in result["workflows"]] == [
            ["17", "anchor"]
        ]
        assert result["unassigned_event_ids"] == []
