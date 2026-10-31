"""Synthetic adversarial ServiceNow Table API staging fixtures; no network."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from server.servicenow_incident_staging import (
    MAX_BYTES, MAX_JSON_DEPTH, MAX_JSON_NODES, SelectedIncidents, StagingError,
    plan_page, project_page,
    project_planned_page, project_planned_pages,
)

A = "a" * 32
B = "b" * 32
C = "c" * 32


def scope(**overrides):
    options = dict(host="example.service-now.com", tenant_scope="org1", sys_ids=(A, B),
                   consented=True, grant_current=True)
    options.update(overrides)
    return SelectedIncidents(**options)


def item(sys_id=A, *, state="2", mod="3", updated="2026-10-30 03:10:00", **extra):
    return {"sys_id": sys_id, "state": state, "sys_mod_count": mod,
            "sys_updated_on": updated, **extra}


def page(*rows):
    return json.dumps({"result": list(rows)}).encode()


def test_fixed_host_selected_only_read_and_minimal_fields():
    plan = plan_page(scope(), offset=5, limit=10, since="2026-10-29 00:00:00")
    parsed = urlsplit(plan["url"])
    params = parse_qs(parsed.query)
    assert plan["method"] == "GET" and parsed.netloc == "example.service-now.com"
    assert parsed.path == "/api/now/table/incident"
    assert params["sysparm_fields"] == ["sys_id,sys_updated_on,sys_mod_count,state"]
    assert params["sysparm_query"] == [
        f"sys_idIN{A},{B}^sys_updated_on>=2026-10-29 00:00:00^ORDERBYsys_updated_on^ORDERBYsys_id"
    ]
    assert params["sysparm_limit"] == ["10"] and params["sysparm_offset"] == ["5"]
    assert "Authorization" not in plan and "token" not in plan["url"]
    assert plan["historical_completeness"] == "unknown"


def test_no_consent_or_revoked_grant_prevents_plan_and_projection():
    for changes in ({"consented": False}, {"grant_current": False}):
        with pytest.raises(StagingError):
            plan_page(scope(**changes))
        with pytest.raises(StagingError):
            project_page(scope(**changes), page(item()))


def test_tenant_namespace_and_selected_ids_are_separate():
    assert scope().namespace != scope(tenant_scope="org2").namespace
    assert scope().namespace != scope(host="another.service-now.com").namespace
    assert scope().namespace == scope(sys_ids=(B,)).namespace
    with pytest.raises(StagingError):
        project_page(scope(), page(item(C)))


def test_source_revision_is_stable_and_ignores_unselected_private_fields():
    projected = project_page(scope(), page(item(short_description="private", caller_id="secret")))
    record = projected["records"][0]
    assert record["source_event_id"] == A + ":3"
    assert "private" not in json.dumps(projected) and "secret" not in json.dumps(projected)
    assert record["source_status_only"] and not record["business_outcome_verified"]
    assert not projected["checkpoint_advanced"]
    assert not record["timestamp_timezone_verified"]


def test_duplicate_equal_rows_dedupe_but_conflicting_same_revision_refused():
    out = project_page(scope(), page(item(), item()))
    assert out["returned_rows"] == 2 and out["distinct_selected_rows"] == 1
    with pytest.raises(StagingError, match="Conflicting"):
        project_page(scope(), page(item(), item(state="6")))


@pytest.mark.parametrize("raw", [
    b'{"result":[],"result":[]}', b'{"result":NaN}', b'{"result":{}}',
    b'{"result":[null]}', b'{"result":[]}' + b" " * MAX_BYTES,
    b'{"result":[' + b"[" * 1500,
])
def test_malformed_and_oversized_provider_responses_fail_closed(raw):
    with pytest.raises(StagingError):
        project_page(scope(), raw)


@pytest.mark.parametrize("bad", [
    item(sys_id=C), item(updated="2026-10-30T03:10:00Z"),
    item(updated="2026-99-99 03:10:00"), item(updated="2026-02-29 03:10:00"),
    item(updated="2024-02-30 03:10:00"), item(updated="2024-02-29 24:00:00"),
    item(mod="-1"), item(mod=3), item(state="\nprivate"), item(state=""),
])
def test_invalid_or_unselected_records_fail_closed(bad):
    with pytest.raises(StagingError):
        project_page(scope(), page(bad))


def test_page_boundary_does_not_claim_complete_history():
    one = project_page(scope(), page(item()), limit=2)
    full = project_page(scope(), page(item(), item(B)), limit=2)
    assert not one["may_have_more"] and full["may_have_more"]
    assert one["historical_completeness"] == full["historical_completeness"] == "unknown"
    assert not one["checkpoint_advanced"] and not full["checkpoint_advanced"]


def test_reject_untrusted_host_query_injection_and_unbounded_requests():
    for host in ("evil.example", "example.service-now.com.evil.com", "EXAMPLE.service-now.com", "example.service-now.com/path"):
        with pytest.raises(StagingError):
            scope(host=host)
    for ids in ((A, A), (A, C + "^ORactive=true"), ()):
        with pytest.raises(StagingError):
            scope(sys_ids=ids)
    for kw in ({"offset": -1}, {"limit": 101}, {"limit": True},
               {"since": "2026-10-30 03:10:00^ORactive=true"},
               {"since": "2026-99-99 03:10:00"}):
        with pytest.raises(StagingError):
            plan_page(scope(), **kw)


def test_provider_local_leap_day_is_valid_but_never_promoted_to_utc():
    local = "2024-02-29 23:59:59"
    planned = plan_page(scope(), since=local)
    assert local in parse_qs(urlsplit(planned["url"]).query)["sysparm_query"][0]
    staged = project_page(scope(), page(item(updated=local)))
    assert staged["records"][0]["reported_updated_on"] == local
    assert not staged["records"][0]["timestamp_timezone_verified"]
    assert not staged["provider_timestamp_timezone_verified"]


def test_incremental_projection_refuses_ignored_provider_filter():
    since = "2026-10-30 03:10:00"
    plan = plan_page(scope(), since=since)
    assert f"sys_updated_on>={since}" in parse_qs(urlsplit(plan["url"]).query)["sysparm_query"][0]

    # Even a selected ID cannot bypass the caller's requested lower bound.
    with pytest.raises(StagingError, match="older than requested watermark"):
        project_page(scope(), page(item(updated="2026-10-29 23:59:59")),
                     since=since)
    # A single older row poisons the entire staged page; no partial return.
    with pytest.raises(StagingError, match="older than requested watermark"):
        project_page(scope(), page(item(), item(B, updated="2026-10-29 23:59:59")),
                     since=since)


def test_incremental_projection_accepts_equal_and_newer_provider_local_times():
    since = "2024-02-29 23:59:59"
    projected = project_page(
        scope(), page(item(updated=since), item(B, updated="2024-03-01 00:00:00")),
        since=since,
    )
    assert projected["distinct_selected_rows"] == 2
    assert [r["reported_updated_on"] for r in projected["records"]] == [
        since, "2024-03-01 00:00:00",
    ]
    assert not projected["checkpoint_advanced"]
    assert not projected["provider_timestamp_timezone_verified"]
    assert all(not r["timestamp_timezone_verified"] for r in projected["records"])


@pytest.mark.parametrize("bad_since", [
    "2026-02-29 00:00:00", "2026-13-01 00:00:00",
    "2026-10-30 03:10:00^ORactive=true", "2026-10-30T03:10:00Z",
    3, True,
])
def test_incremental_projection_rejects_invalid_watermark_even_on_empty_page(bad_since):
    with pytest.raises(StagingError):
        project_page(scope(), page(), since=bad_since)


def test_incremental_projection_does_not_bypass_revoked_authority():
    with pytest.raises(StagingError, match="not currently authorized"):
        project_page(scope(grant_current=False), page(item()), since="2026-10-30 03:10:00")


def test_planned_projection_pins_requested_incremental_bound_and_page_size():
    selected = scope()
    since = "2026-10-30 03:10:00"
    plan = plan_page(selected, offset=7, limit=2, since=since)
    assert plan["since"] == since
    staged = project_planned_page(
        selected, page(item(updated=since), item(B, updated="2026-10-30 03:11:00")),
        plan=plan,
    )
    assert staged["plan_bound"] is True
    assert staged["planned_offset"] == 7 and staged["planned_since"] == since
    assert staged["distinct_selected_rows"] == 2
    assert staged["checkpoint_advanced"] is False
    assert staged["historical_completeness"] == "unknown"
    assert all(not r["business_outcome_verified"] for r in staged["records"])
    # A provider ignoring the planned watermark cannot silently stage old rows.
    with pytest.raises(StagingError, match="older than requested watermark"):
        project_planned_page(selected, page(item(updated="2026-10-29 23:59:59")),
                             plan=plan)
    # A provider returning more rows than the planned limit is also rejected.
    with pytest.raises(StagingError, match="page size"):
        project_planned_page(selected, page(item(), item(B)),
                             plan=plan_page(selected, limit=1, since=since))


@pytest.mark.parametrize("mutation", [
    ("since", None),
    ("since", "2026-10-29 00:00:00"),
    ("url", "https://evil.example/api/now/table/incident"),
    ("limit", 1),
    ("offset", 3),
    ("source_namespace", "snow.other"),
    ("selected_count", 1),
    ("historical_completeness", "complete"),
    ("unexpected", "untrusted"),
])
def test_planned_projection_rejects_tampered_plan_without_partial_staging(mutation):
    selected = scope()
    plan = plan_page(selected, offset=2, limit=2, since="2026-10-30 03:10:00")
    key, value = mutation
    plan[key] = value
    with pytest.raises(StagingError):
        project_planned_page(selected, page(item()), plan=plan)


@pytest.mark.parametrize("field,substitute", [
    ("selected_count", True),
    ("selected_count", 1.0),
    ("provider_timestamp_timezone_verified", 0),
    ("provider_timestamp_timezone_verified", 0.0),
], ids=[
    "boolean-count", "float-count", "integer-false-flag", "float-false-flag",
])
def test_planned_page_metadata_requires_exact_types_across_all_entrypoints(
    field, substitute,
):
    """Python equality must not let forged plan metadata claim exact provenance."""
    from server.servicenow_incident_staging import reconcile_planned_replays

    selected = scope(sys_ids=(A,))
    original = plan_page(selected, limit=1)
    assert original["selected_count"] == 1
    assert original["provider_timestamp_timezone_verified"] is False
    raw = page(item(A))
    # Preserve the original contract for an unmodified plan.
    assert project_planned_page(selected, raw, plan=original)["plan_bound"]
    assert project_planned_pages(selected, [(original, raw)])["plan_sequence_checked"]
    assert reconcile_planned_replays(
        selected, [(original, raw)], [(original, raw)]
    )["same_plans_checked"]

    altered = dict(original, **{field: substitute})
    # The vulnerability is a Python value-equality collision, not a change
    # to a planned URL, selection, limit, offset, or source record.
    assert altered == original
    assert type(altered[field]) is not type(original[field])
    for invoke in (
        lambda: project_planned_page(selected, raw, plan=altered),
        lambda: project_planned_pages(selected, [(altered, raw)]),
        lambda: reconcile_planned_replays(
            selected, [(altered, raw)], [(altered, raw)]
        ),
    ):
        with pytest.raises(StagingError, match="does not match"):
            invoke()


def test_planned_projection_rejects_missing_plan_keys_and_wrong_scope():
    selected = scope()
    plan = plan_page(selected, since="2026-10-30 03:10:00")
    for key in ("since", "url", "offset", "limit"):
        incomplete = dict(plan)
        incomplete.pop(key)
        with pytest.raises(StagingError):
            project_planned_page(selected, page(item()), plan=incomplete)
    for changed in (scope(sys_ids=(A,)), scope(tenant_scope="org2"),
                    scope(host="another.service-now.com")):
        with pytest.raises(StagingError, match="does not match"):
            project_planned_page(changed, page(item()), plan=plan)
    with pytest.raises(StagingError, match="not currently authorized"):
        project_planned_page(scope(grant_current=False), page(item()), plan=plan)
    with pytest.raises(StagingError):
        project_planned_page(selected, page(item()), plan=None)


def test_planned_projection_supports_explicit_unfiltered_selected_read():
    selected = scope()
    plan = plan_page(selected, limit=1)
    assert plan["since"] is None
    result = project_planned_page(selected, page(item()), plan=plan)
    assert result["plan_bound"] and result["planned_since"] is None
    assert not result["checkpoint_advanced"]


def test_planned_projection_rejects_reversed_update_order_without_normalizing():
    selected = scope()
    plan = plan_page(selected, limit=2, since="2026-10-30 03:10:00")
    raw = page(item(B, updated="2026-10-30 03:11:00"),
               item(A, updated="2026-10-30 03:10:00"))
    with pytest.raises(StagingError, match="out of planned order"):
        project_planned_page(selected, raw, plan=plan)
    # Standalone validation makes no assertion about a planned ORDERBY.
    standalone = project_page(selected, raw, limit=2, since=plan["since"])
    assert [r["source_case_id"] for r in standalone["records"]] == [A, B]
    assert "page_order_matches_plan" not in standalone


def test_planned_projection_rejects_reversed_sys_id_on_equal_timestamps():
    selected = scope()
    plan = plan_page(selected, limit=2)
    with pytest.raises(StagingError, match="out of planned order"):
        project_planned_page(selected, page(item(B), item(A)), plan=plan)


def test_planned_projection_accepts_ties_and_exact_duplicate_rows():
    selected = scope()
    plan = plan_page(selected, limit=3)
    projected = project_planned_page(
        selected, page(item(A), item(A), item(B)), plan=plan,
    )
    assert projected["page_order_matches_plan"] is True
    assert projected["returned_rows"] == 3
    assert projected["distinct_selected_rows"] == 2
    assert [r["source_case_id"] for r in projected["records"]] == [A, B]
    assert projected["historical_completeness"] == "unknown"
    assert projected["checkpoint_advanced"] is False


def test_planned_projection_order_check_does_not_override_revocation():
    selected = scope()
    plan = plan_page(selected, limit=2)
    with pytest.raises(StagingError, match="not currently authorized"):
        project_planned_page(scope(grant_current=False),
                             page(item(B), item(A)), plan=plan)

def test_planned_pages_accept_consecutive_ordered_bounded_responses():
    selected = scope()
    pages = [
        (plan_page(selected, offset=0, limit=1, since="2026-10-30 03:00:00"),
         page(item(A, updated="2026-10-30 03:10:00"))),
        (plan_page(selected, offset=1, limit=1, since="2026-10-30 03:00:00"),
         page(item(B, updated="2026-10-30 03:11:00"))),
    ]
    out = project_planned_pages(selected, pages)
    assert out["pages_checked"] == 2 and out["plan_sequence_checked"]
    assert out["returned_rows"] == out["distinct_selected_rows"] == 2
    assert [r["source_case_id"] for r in out["records"]] == [A, B]
    assert out["may_have_more"] and out["historical_completeness"] == "unknown"
    assert not out["checkpoint_advanced"] and out["not_an_authorization_to_append"]
    assert not out["provider_timestamp_timezone_verified"]


@pytest.mark.parametrize("first,second", [
    (item(B, updated="2026-10-30 03:11:00"), item(A, updated="2026-10-30 03:10:00")),
    (item(B), item(A)),
])
def test_planned_pages_reject_cross_boundary_time_and_tie_order_regression(first, second):
    selected = scope()
    entries = [(plan_page(selected, offset=i, limit=1), page(row))
               for i, row in enumerate((first, second))]
    # Each single page is ordered; only the batch boundary reveals the defect.
    assert all(project_planned_page(selected, raw, plan=plan)["page_order_matches_plan"]
               for plan, raw in entries)
    with pytest.raises(StagingError, match="regressed across offsets"):
        project_planned_pages(selected, entries)


@pytest.mark.parametrize("second", [item(A), item(A, state="6", mod="4")])
def test_planned_pages_refuse_same_incident_repeated_across_offsets(second):
    selected = scope()
    entries = [(plan_page(selected, offset=0, limit=1), page(item(A))),
               (plan_page(selected, offset=1, limit=1), page(second))]
    with pytest.raises(StagingError, match="repeated across planned pages"):
        project_planned_pages(selected, entries)


@pytest.mark.parametrize("second_offset,second_limit,second_since", [
    (0, 1, "2026-10-30 03:00:00"),
    (2, 1, "2026-10-30 03:00:00"),
    (1, 2, "2026-10-30 03:00:00"),
    (1, 1, "2026-10-30 03:01:00"),
])
def test_planned_pages_reject_replay_skip_or_changed_page_contract(second_offset, second_limit, second_since):
    selected = scope()
    entries = [
        (plan_page(selected, offset=0, limit=1, since="2026-10-30 03:00:00"), page(item(A))),
        (plan_page(selected, offset=second_offset, limit=second_limit, since=second_since),
         page(item(B, updated="2026-10-30 03:11:00"))),
    ]
    with pytest.raises(StagingError, match="Nonconsecutive or inconsistent"):
        project_planned_pages(selected, entries)


def test_planned_pages_reject_short_intermediate_page():
    selected = scope()
    entries = [(plan_page(selected, offset=0, limit=1), page()),
               (plan_page(selected, offset=1, limit=1), page(item(B)))]
    with pytest.raises(StagingError, match="Short page"):
        project_planned_pages(selected, entries)


def test_planned_pages_fail_closed_on_revocation_and_changed_tenant():
    selected = scope()
    entries = [(plan_page(selected, limit=1), page(item(A)))]
    with pytest.raises(StagingError, match="not currently authorized"):
        project_planned_pages(scope(grant_current=False), entries)
    with pytest.raises(StagingError, match="does not match"):
        project_planned_pages(scope(tenant_scope="another"), entries)


def test_planned_pages_bound_entries_and_require_zero_start():
    selected = scope()
    entry = (plan_page(selected, limit=1), page(item(A)))
    for bad in ([], [entry] * 9, ["not-a-pair"], [(dict(limit=None), page(item()))]):
        with pytest.raises(StagingError):
            project_planned_pages(selected, bad)
    with pytest.raises(StagingError, match="Nonconsecutive"):
        project_planned_pages(selected, [(plan_page(selected, offset=1, limit=1), page(item(A)))])


@pytest.mark.parametrize("extra", [
    {"error": {"message": "private ACL denial"}, "status": "failure"},
    {"error": {"message": "private partial response"}},
    {"status": "failure"},
    {"next": "https://attacker.example/next"},
    {"metadata": {"total": 0}},
])
def test_mixed_result_envelopes_fail_closed_in_all_projection_modes(extra):
    """An error-bearing/extended envelope is not evidence of a clean empty page."""
    selected = scope()
    raw = json.dumps({"result": [item(A)], **extra}).encode("utf-8")
    planned = plan_page(selected, limit=1)
    projections = (
        lambda: project_page(selected, raw, limit=1),
        lambda: project_planned_page(selected, raw, plan=planned),
        lambda: project_planned_pages(selected, [(planned, raw)]),
    )
    for project in projections:
        with pytest.raises(StagingError, match="envelope") as caught:
            project()
        assert "private" not in str(caught.value)
        assert "attacker" not in str(caught.value)


def test_exact_result_envelope_remains_compatible_with_empty_planned_page():
    selected = scope()
    planned = plan_page(selected, limit=1)
    result = project_planned_pages(selected, [(planned, page())])
    assert result["returned_rows"] == 0
    assert result["historical_completeness"] == "unknown"
    assert result["checkpoint_advanced"] is False


@pytest.mark.parametrize("rows_by_page,selected_ids", [
    (((item(A), item(A)),), (A, B)),
    (((item(A), item(A)), (item(B),)), (A, B)),
    (((item(A), item(B)), (item(C), item(C))), (A, B, C)),
])
def test_planned_pages_refuse_duplicate_rows_within_any_offset_page(rows_by_page, selected_ids):
    selected = scope(sys_ids=selected_ids)
    entries = [(plan_page(selected, offset=index * 2, limit=2), page(*rows))
               for index, rows in enumerate(rows_by_page)]
    # A standalone plan-bound page remains backwards-compatible: exact
    # duplicate snapshots can be deduplicated when no offset batch is claimed.
    first = project_planned_page(selected, entries[0][1], plan=entries[0][0])
    assert first["returned_rows"] == 2
    with pytest.raises(StagingError, match="Duplicate incident row within planned offset page"):
        project_planned_pages(selected, entries)


def test_planned_pages_unique_rows_still_stage_without_completeness_claim():
    selected = scope()
    entries = [(plan_page(selected, offset=0, limit=2), page(item(A), item(B)))]
    staged = project_planned_pages(selected, entries)
    assert staged["returned_rows"] == staged["distinct_selected_rows"] == 2
    assert staged["plan_sequence_checked"] is True
    assert staged["historical_completeness"] == "unknown"
    assert staged["checkpoint_advanced"] is False


def _page_with_private_json_number(token: str) -> bytes:
    """Inject a numeric token without json.dumps normalizing overflow."""
    raw = page(item(A, diagnostic={"nested_metric": 0}))
    marker = b'"nested_metric": 0'
    assert raw.count(marker) == 1
    return raw.replace(marker, f'"nested_metric": {token}'.encode("ascii"))


@pytest.mark.parametrize("mode", ["standalone", "planned", "batch"])
@pytest.mark.parametrize("token", ["1e309", "-1E99999", "9.99e9999"])
def test_overflowed_private_json_number_refused_in_all_projection_modes(mode, token):
    selected = scope()
    raw = _page_with_private_json_number(token)
    planned = plan_page(selected, limit=1)
    with pytest.raises(StagingError, match="Invalid bounded provider JSON") as caught:
        if mode == "standalone":
            project_page(selected, raw, limit=1)
        elif mode == "planned":
            project_planned_page(selected, raw, plan=planned)
        else:
            project_planned_pages(selected, [(planned, raw)])
    assert token not in str(caught.value)
    assert "nested_metric" not in str(caught.value)


@pytest.mark.parametrize("token", ["1e308", "-2.5", "0.0", "1e-9999"])
def test_finite_private_json_numbers_are_discarded_without_changing_source_projection(token):
    selected = scope()
    planned = plan_page(selected, limit=1)
    staged = project_planned_pages(
        selected, [(planned, _page_with_private_json_number(token))]
    )
    assert staged["returned_rows"] == staged["distinct_selected_rows"] == 1
    assert staged["records"][0]["source_event_id"] == A + ":3"
    assert "nested_metric" not in json.dumps(staged)
    assert not staged["checkpoint_advanced"]
    assert staged["historical_completeness"] == "unknown"


def test_replay_reconciliation_distinguishes_equal_higher_and_missing_unknown():
    from server.servicenow_incident_staging import reconcile_replayed_pages
    selected = scope(sys_ids=(A, B, C))
    before = page(item(A), item(B, mod="4", updated="2026-10-30 03:11:00"))
    after = page(item(A, short_description="discard me"),
                 item(B, mod="5", updated="2026-10-30 03:12:00"),
                 item(C, updated="2026-10-30 03:13:00"))
    out = reconcile_replayed_pages(selected, before, after, limit=3)
    assert out["same_reported_revision_ids"] == [A]
    assert out["higher_reported_revision_ids"] == [B]
    assert out["newly_observed_ids"] == [C]
    assert out["missing_unknown_ids"] == []
    assert out["source_namespace"] == selected.namespace
    assert out["replay_consistency_checked"]
    assert out["historical_completeness"] == "unknown"
    assert not out["business_outcome_verified"]
    assert not out["checkpoint_advanced"]
    assert not out["missing_means_deleted"]
    assert out["not_an_authorization_to_append"]
    assert "discard me" not in json.dumps(out)


@pytest.mark.parametrize("duplicate_side", ["earlier", "later"])
def test_unplanned_replay_refuses_duplicate_rows_that_hide_selected_incidents(duplicate_side):
    from server.servicenow_incident_staging import reconcile_replayed_pages
    selected = scope(sys_ids=(A, B))
    duplicate = page(item(A), item(A, caller_id="private value"))
    unique = page(item(A), item(B, updated="2026-10-30 03:11:00"))
    # The standalone API intentionally retains legacy identical-row dedupe.
    staged = project_page(selected, duplicate, limit=2)
    assert staged["returned_rows"] == 2
    assert staged["distinct_selected_rows"] == 1
    assert staged["unobserved_selected_ids"] == [B]
    earlier, later = ((duplicate, unique) if duplicate_side == "earlier"
                      else (unique, duplicate))
    with pytest.raises(StagingError, match="Duplicate incident row in replay page") as caught:
        reconcile_replayed_pages(selected, earlier, later, limit=2)
    assert "private value" not in str(caught.value)



@pytest.mark.parametrize("later", [
    item(A, mod="2"),
    item(A, state="6"),
    item(A, updated="2026-10-30 03:10:01"),
])
def test_replay_reconciliation_refuses_backward_or_conflicting_revision(later):
    from server.servicenow_incident_staging import reconcile_replayed_pages
    with pytest.raises(StagingError, match="revision|Conflicting"):
        reconcile_replayed_pages(scope(), page(item(A)), page(later))


def test_replay_reconciliation_never_equates_omission_with_deletion():
    from server.servicenow_incident_staging import reconcile_replayed_pages
    result = reconcile_replayed_pages(scope(), page(item(A), item(B)), page(item(B)))
    assert result["missing_unknown_ids"] == [A]
    assert result["same_reported_revision_ids"] == [B]
    assert result["newly_observed_ids"] == []
    assert result["missing_means_deleted"] is False
    assert result["historical_completeness"] == "unknown"


def test_replay_reconciliation_never_joins_distinct_ids_by_equal_time():
    from server.servicenow_incident_staging import reconcile_replayed_pages
    result = reconcile_replayed_pages(scope(), page(item(A)), page(item(B)))
    assert result["same_reported_revision_ids"] == []
    assert result["newly_observed_ids"] == [B]
    assert result["missing_unknown_ids"] == [A]


def test_replay_reconciliation_revalidates_both_pages_and_authority():
    from server.servicenow_incident_staging import reconcile_replayed_pages
    for revoked in (scope(consented=False), scope(grant_current=False)):
        with pytest.raises(StagingError, match="not currently authorized"):
            reconcile_replayed_pages(revoked, page(item(A)), page(item(A)))
    for malformed in (page(item(C)), b'{"result":[],"result":[]}',
                      page(item(A, updated="2026-10-29 00:00:00"))):
        with pytest.raises(StagingError):
            reconcile_replayed_pages(scope(), page(item(A)), malformed,
                                     since="2026-10-30 00:00:00")
    assert (reconcile_replayed_pages(scope(tenant_scope="another"),
                                     page(item(A)), page(item(A)))["source_namespace"]
            != scope().namespace)



def _planned_replay_batch(selected, first, second):
    return [(plan_page(selected, offset=0, limit=1), page(first)),
            (plan_page(selected, offset=1, limit=1), page(second))]


def test_plan_bound_replay_same_plans_and_unknown_outcomes():
    from server.servicenow_incident_staging import reconcile_planned_replays
    selected = scope(sys_ids=(A, B, C))
    before = _planned_replay_batch(selected, item(A),
                                   item(B, mod="4", updated="2026-10-30 03:11:00"))
    after = _planned_replay_batch(selected, item(A, caller_id="private"),
                                  item(B, mod="5", updated="2026-10-30 03:12:00"))
    result = reconcile_planned_replays(selected, before, after)
    assert result["same_reported_revision_ids"] == [A]
    assert result["higher_reported_revision_ids"] == [B]
    assert result["same_plans_checked"] and result["plan_sequence_checked"]
    assert result["pages_per_read"] == 2
    assert result["source_namespace"] == selected.namespace
    assert result["historical_completeness"] == "unknown"
    assert not result["business_outcome_verified"]
    assert not result["checkpoint_advanced"]
    assert result["not_an_authorization_to_append"]
    assert "private" not in json.dumps(result)


def test_plan_bound_replay_missing_ids_remain_unknown():
    from server.servicenow_incident_staging import reconcile_planned_replays
    selected = scope(sys_ids=(A, B, C))
    before = _planned_replay_batch(selected, item(A),
                                   item(B, updated="2026-10-30 03:11:00"))
    after = _planned_replay_batch(selected, item(A),
                                  item(C, updated="2026-10-30 03:12:00"))
    result = reconcile_planned_replays(selected, before, after)
    assert result["newly_observed_ids"] == [C]
    assert result["missing_unknown_ids"] == [B]
    assert result["missing_means_deleted"] is False


@pytest.mark.parametrize("change", ["since", "limit", "offset", "scope", "revoked"])
def test_plan_bound_replay_rejects_changed_plans_or_revocation(change):
    from server.servicenow_incident_staging import reconcile_planned_replays
    selected = scope(sys_ids=(A, B))
    before = _planned_replay_batch(selected, item(A),
                                   item(B, updated="2026-10-30 03:11:00"))
    after = _planned_replay_batch(selected, item(A),
                                  item(B, updated="2026-10-30 03:11:00"))
    if change == "since":
        after[0] = (plan_page(selected, limit=1, since="2026-10-30 03:00:00"), after[0][1])
    elif change == "limit":
        after[0] = (plan_page(selected, limit=2), after[0][1])
    elif change == "offset":
        after[0] = (plan_page(selected, offset=1, limit=1), after[0][1])
    elif change == "scope":
        other = scope(tenant_scope="other", sys_ids=(A, B))
        after[0] = (plan_page(other, limit=1), after[0][1])
    else:
        selected = scope(sys_ids=(A, B), grant_current=False)
    with pytest.raises(StagingError):
        reconcile_planned_replays(selected, before, after)


def test_plan_bound_replay_rejects_revision_conflict_and_cross_page_order():
    from server.servicenow_incident_staging import reconcile_planned_replays
    selected = scope(sys_ids=(A, B))
    before = _planned_replay_batch(selected, item(A),
                                   item(B, updated="2026-10-30 03:11:00"))
    conflicting = _planned_replay_batch(selected, item(A, state="6"),
                                        item(B, updated="2026-10-30 03:11:00"))
    with pytest.raises(StagingError, match="Conflicting"):
        reconcile_planned_replays(selected, before, conflicting)
    reversed_order = _planned_replay_batch(
        selected, item(B, updated="2026-10-30 03:11:00"), item(A))
    with pytest.raises(StagingError, match="order regressed"):
        reconcile_planned_replays(selected, before, reversed_order)


def test_plan_bound_replay_does_not_change_unplanned_api():
    from server.servicenow_incident_staging import reconcile_replayed_pages
    result = reconcile_replayed_pages(scope(), page(item(A)), page(item(A)))
    assert result["same_reported_revision_ids"] == [A]
    assert "same_plans_checked" not in result


@pytest.mark.parametrize("earlier,later,unobserved", [
    ((), (), (A, B, C)),
    ((A,), (A,), (B, C)),
    ((A,), (B,), (C,)),
    ((A, B), (A, B, C), ()),
], ids=["both-empty", "same-partial", "disjoint-partial", "all-observed"])
def test_replay_discloses_selected_ids_absent_from_both_reads(
    earlier, later, unobserved,
):
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )
    selected = scope(sys_ids=(C, A, B))  # Deliberately not sorted.
    before_raw = page(*(item(incident_id) for incident_id in earlier))
    after_raw = page(*(item(incident_id) for incident_id in later))
    standalone = reconcile_replayed_pages(
        selected, before_raw, after_raw, limit=3,
    )
    planned = plan_page(selected, limit=3)
    plan_bound = reconcile_planned_replays(
        selected, [(planned, before_raw)], [(planned, after_raw)],
    )
    for result in (standalone, plan_bound):
        assert result["unobserved_in_either_read_ids"] == list(unobserved)
        assert result["historical_completeness"] == "unknown"
        assert result["business_outcome_verified"] is False
        assert result["checkpoint_advanced"] is False
        assert result["not_an_authorization_to_append"] is True
        assert result["missing_means_deleted"] is False
    assert plan_bound["same_plans_checked"] is True


def test_replay_unobserved_scope_is_tenant_and_grant_scoped():
    from server.servicenow_incident_staging import reconcile_replayed_pages
    first = scope(sys_ids=(A, B, C))
    other = scope(sys_ids=(A, B, C), tenant_scope="other")
    for selected in (first, other):
        result = reconcile_replayed_pages(selected, page(item(A)), page(item(A)))
        assert result["unobserved_in_either_read_ids"] == [B, C]
        assert result["source_namespace"] == selected.namespace
    assert first.namespace != other.namespace
    for selected in (scope(sys_ids=(A, B, C), consented=False),
                     scope(sys_ids=(A, B, C), grant_current=False)):
        with pytest.raises(StagingError, match="not currently authorized"):
            reconcile_replayed_pages(selected, page(), page())


@pytest.mark.parametrize("private_fields", [
    {"private": "\ud800"},
    {"private": {"nested": ["\udc00"]}},
    {"\ud800": "private key"},
], ids=["high-surrogate-value", "low-surrogate-nested", "surrogate-key"])
def test_discarded_private_invalid_unicode_refused_across_staging_paths(private_fields):
    """Discarded private fields cannot hide invalid Unicode scalar escapes."""
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )
    selected = scope()
    raw = page(item(A, **private_fields))
    planned = plan_page(selected, limit=2)
    paths = (
        lambda: project_page(selected, raw, limit=2),
        lambda: project_planned_page(selected, raw, plan=planned),
        lambda: project_planned_pages(selected, [(planned, raw)]),
        lambda: reconcile_replayed_pages(selected, raw, raw, limit=2),
        lambda: reconcile_planned_replays(
            selected, [(planned, raw)], [(planned, raw)],
        ),
    )
    for invoke in paths:
        with pytest.raises(StagingError, match="Unicode scalar"):
            invoke()


@pytest.mark.parametrize("private_value", [
    "deep",
    "wide",
], ids=["over-depth", "over-node-budget"])
def test_discarded_private_json_tree_stays_bounded(private_value):
    """Even ignored source fields must respect depth and node limits."""
    if private_value == "deep":
        extra = "leaf"
        for _ in range(MAX_JSON_DEPTH + 2):
            extra = {"child": extra}
    else:
        extra = list(range(MAX_JSON_NODES))
    raw = page(item(A, private=extra))
    assert len(raw) < MAX_BYTES
    with pytest.raises(StagingError, match="structural bounds"):
        project_page(scope(), raw, limit=2)
    planned = plan_page(scope(), limit=2)
    with pytest.raises(StagingError, match="structural bounds"):
        project_planned_page(scope(), raw, plan=planned)


def test_valid_unicode_pair_and_small_discarded_tree_remain_compatible():
    """A valid escaped surrogate pair is one Unicode scalar, not a lone half."""
    selected = scope()
    raw = page(item(A, private={"note": "\U0001f642", "values": list(range(40))}))
    staged = project_page(selected, raw, limit=2)
    assert staged["distinct_selected_rows"] == 1
    assert staged["records"][0]["source_case_id"] == A
    assert "private" not in staged["records"][0]
    assert staged["historical_completeness"] == "unknown"
    assert staged["checkpoint_advanced"] is False
    planned = plan_page(selected, limit=2)
    assert project_planned_page(selected, raw, plan=planned)["plan_bound"] is True


@pytest.mark.parametrize("mode", ["standalone", "planned"])
def test_replay_withholds_higher_revision_when_reported_source_time_regresses(mode):
    """A higher counter plus earlier provider-local time is conflicting evidence."""
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )
    selected = scope()
    before_a = item(A, mod="3", updated="2026-10-30 03:10:00")
    before_b = item(B, mod="3", updated="2026-10-30 03:11:00")
    after_a = item(A, mod="4", state="6", updated="2026-10-30 03:09:59")
    after_b = item(B, mod="4", updated="2026-10-30 03:12:00")
    if mode == "standalone":
        result = reconcile_replayed_pages(
            selected, page(before_a, before_b), page(after_a, after_b),
        )
    else:
        plans = [plan_page(selected, offset=i, limit=1) for i in (0, 1)]
        result = reconcile_planned_replays(
            selected,
            [(plans[0], page(before_a)), (plans[1], page(before_b))],
            [(plans[0], page(after_a)), (plans[1], page(after_b))],
        )
    assert result["timestamp_regression_unknown_ids"] == [A]
    assert result["higher_reported_revision_ids"] == [B]
    assert result["same_reported_revision_ids"] == []
    assert result["replay_consistency_checked"]
    assert result["historical_completeness"] == "unknown"
    assert not result["provider_timestamp_timezone_verified"]
    assert not result["business_outcome_verified"]
    assert not result["checkpoint_advanced"]
    assert result["not_an_authorization_to_append"]


@pytest.mark.parametrize("mode", ["standalone", "planned"])
def test_replay_allows_higher_counter_at_equal_provider_local_second(mode):
    """Second-resolution timestamps can tie without proving true chronology."""
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )
    selected = scope(sys_ids=(A,))
    before = item(A, mod="3", updated="2026-10-30 03:10:00")
    after = item(A, mod="4", updated="2026-10-30 03:10:00")
    if mode == "standalone":
        result = reconcile_replayed_pages(selected, page(before), page(after))
    else:
        plan = plan_page(selected, limit=1)
        result = reconcile_planned_replays(
            selected, [(plan, page(before))], [(plan, page(after))],
        )
    assert result["higher_reported_revision_ids"] == [A]
    assert result["timestamp_regression_unknown_ids"] == []
    assert not result["business_outcome_verified"]
    assert not result["provider_timestamp_timezone_verified"]
    assert not result["checkpoint_advanced"]


def test_selected_scope_cannot_be_duck_typed_to_bypass_consent_or_host():
    """No-op authority methods on arbitrary objects cannot create a GET plan."""
    class ForgedScope:
        host = "attacker.invalid"
        tenant_scope = "org1"
        sys_ids = (A,)
        namespace = "snow.untrusted"

        def require_authority(self):
            return None

    with pytest.raises(StagingError, match="Invalid selected incident scope"):
        plan_page(ForgedScope())


def test_all_offline_entrypoints_reject_forged_scope_authority():
    """Even a matching-looking namespace and plan cannot replace consent."""
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )
    authorized = scope(sys_ids=(A,))
    planned = plan_page(authorized, limit=1)
    raw = page(item(A))

    class ForgedScope:
        host = authorized.host
        tenant_scope = authorized.tenant_scope
        sys_ids = authorized.sys_ids
        namespace = authorized.namespace

        def require_authority(self):
            return None

    fake = ForgedScope()
    entrypoints = [
        lambda: plan_page(fake, limit=1),
        lambda: project_page(fake, raw, limit=1),
        lambda: project_planned_page(fake, raw, plan=planned),
        lambda: project_planned_pages(fake, [(planned, raw)]),
        lambda: reconcile_replayed_pages(fake, raw, raw, limit=1),
        lambda: reconcile_planned_replays(fake, [(planned, raw)], [(planned, raw)]),
    ]
    for entrypoint in entrypoints:
        with pytest.raises(StagingError, match="Invalid selected incident scope"):
            entrypoint()

    # The identical authorized input remains a valid offline staging operation.
    assert project_planned_page(authorized, raw, plan=planned)["plan_bound"] is True


def test_selected_scope_subclass_cannot_override_revocation_authority():
    """Frozen dataclass fields do not prevent overriding instance methods."""
    class ForgedSubclass(SelectedIncidents):
        def require_authority(self):
            return None

    denied = ForgedSubclass(
        host="example.service-now.com", tenant_scope="org1", sys_ids=(A,),
        consented=False, grant_current=False,
    )
    with pytest.raises(StagingError, match="Invalid selected incident scope"):
        plan_page(denied)
    with pytest.raises(StagingError, match="Invalid selected incident scope"):
        project_page(denied, page(item(A)), limit=1)



@pytest.mark.parametrize("observed,missing", [
    ((), (A, B, C)),
    ((B,), (A, C)),
    ((C, A, B), ()),
], ids=["empty", "partial", "all-selected"])
def test_standalone_projection_discloses_unobserved_selected_ids(observed, missing):
    selected = scope(sys_ids=(C, A, B))  # Deliberately unsorted input.
    raw = page(*(item(incident_id) for incident_id in observed))
    staged = project_page(selected, raw, limit=3)
    assert staged["unobserved_selected_ids"] == list(missing)
    assert staged["historical_completeness"] == "unknown"
    assert staged["checkpoint_advanced"] is False
    assert staged["not_an_authorization_to_append"] is True
    assert staged["provider_timestamp_timezone_verified"] is False
    assert all(not record["business_outcome_verified"] for record in staged["records"])


def test_projection_omission_dedupe_is_by_exact_selected_id_only():
    selected = scope(sys_ids=(A, B, C))
    staged = project_page(selected, page(item(A), item(A)), limit=2)
    assert staged["returned_rows"] == 2
    assert staged["distinct_selected_rows"] == 1
    assert staged["unobserved_selected_ids"] == [B, C]
    assert staged["may_have_more"] is True
    with pytest.raises(StagingError, match="unselected"):
        project_page(selected, page(item("d" * 32)))


def test_planned_offset_projection_discloses_unobserved_ids_across_pages():
    selected = scope(sys_ids=(C, A, B))
    first = plan_page(selected, offset=0, limit=1)
    second = plan_page(selected, offset=1, limit=1)
    staged = project_planned_pages(
        selected, [(first, page(item(A))),
                   (second, page(item(B, updated="2026-10-30 03:11:00")))]
    )
    assert staged["unobserved_selected_ids"] == [C]
    assert staged["returned_rows"] == staged["distinct_selected_rows"] == 2
    assert staged["pages_checked"] == 2
    assert staged["historical_completeness"] == "unknown"
    assert staged["checkpoint_advanced"] is False
    assert staged["not_an_authorization_to_append"] is True

    full = project_planned_pages(
        selected, [(plan_page(selected, limit=3),
                    page(item(A), item(B, updated="2026-10-30 03:11:00"),
                         item(C, updated="2026-10-30 03:12:00")))]
    )
    assert full["unobserved_selected_ids"] == []
    assert full["historical_completeness"] == "unknown"


def test_planned_empty_page_discloses_all_unobserved_and_revocation_refuses():
    selected = scope(sys_ids=(B, A))
    plan = plan_page(selected, limit=2)
    for staged in (project_planned_page(selected, page(), plan=plan),
                   project_planned_pages(selected, [(plan, page())])):
        assert staged["unobserved_selected_ids"] == [A, B]
        assert staged["historical_completeness"] == "unknown"
        assert staged["checkpoint_advanced"] is False
    for revoked in (scope(sys_ids=(B, A), consented=False),
                    scope(sys_ids=(B, A), grant_current=False)):
        with pytest.raises(StagingError, match="not currently authorized"):
            project_page(revoked, page())
        with pytest.raises(StagingError, match="not currently authorized"):
            project_planned_pages(revoked, [(plan, page())])


def test_unobserved_selected_ids_are_tenant_scoped_without_private_fields():
    raw = page(item(A, caller_id="private caller"))
    left = project_page(scope(sys_ids=(A, B), tenant_scope="left"), raw)
    right = project_page(scope(sys_ids=(A, B), tenant_scope="right"), raw)
    assert left["unobserved_selected_ids"] == right["unobserved_selected_ids"] == [B]
    assert left["records"][0]["source_namespace"] != right["records"][0]["source_namespace"]
    assert "private caller" not in json.dumps(left)
    assert "private caller" not in json.dumps(right)


@pytest.mark.parametrize("mode", ["standalone", "planned"])
@pytest.mark.parametrize("gap_time", [
    "2026-10-30 03:10:00", "2026-10-30 03:11:00",
], ids=["equal-second-gap", "later-second-gap"])
def test_replay_revision_gap_is_unknown_not_adjacent_advance(mode, gap_time):
    """Missing intermediate provider counters must not look like clean advances."""
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )

    selected = scope(sys_ids=(C, A, B))
    before = page(item(A), item(B), item(C))
    after_rows = [
        item(A, mod="6", state="6", updated=gap_time),
        item(B, mod="4"),  # Adjacent counter, same reported second.
        item(C, mod="8", updated="2026-10-30 03:09:59"),
    ]
    # The planned path validates source ORDERBY; reorder only the raw rows,
    # not source identities or the previously reported revision evidence.
    after_rows.sort(key=lambda row: (row["sys_updated_on"], row["sys_id"]))
    after = page(*after_rows)
    if mode == "standalone":
        result = reconcile_replayed_pages(selected, before, after, limit=3)
    else:
        plan = plan_page(selected, limit=3)
        result = reconcile_planned_replays(
            selected, [(plan, before)], [(plan, after)],
        )

    assert result["revision_gap_unknown_ids"] == [A]
    assert result["higher_reported_revision_ids"] == [B]
    # Source time disagreement takes precedence over revision-gap classification.
    assert result["timestamp_regression_unknown_ids"] == [C]
    assert result["same_reported_revision_ids"] == []
    assert result["missing_unknown_ids"] == []
    assert result["historical_completeness"] == "unknown"
    assert result["business_outcome_verified"] is False
    assert result["provider_timestamp_timezone_verified"] is False
    assert result["checkpoint_advanced"] is False
    assert result["not_an_authorization_to_append"] is True


@pytest.mark.parametrize("mode", ["standalone", "planned"])
def test_replay_newly_observed_large_counter_is_not_inferred_history(mode):
    """An ID absent from the first read has no observed counter baseline."""
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )

    selected = scope(sys_ids=(A, B))
    before = page()
    after = page(item(A, mod="999"))
    if mode == "standalone":
        result = reconcile_replayed_pages(selected, before, after, limit=2)
    else:
        plan = plan_page(selected, limit=2)
        result = reconcile_planned_replays(
            selected, [(plan, before)], [(plan, after)],
        )
    assert result["newly_observed_ids"] == [A]
    assert result["revision_gap_unknown_ids"] == []
    assert result["higher_reported_revision_ids"] == []
    assert result["unobserved_in_either_read_ids"] == [B]
    assert result["historical_completeness"] == "unknown"
    assert result["business_outcome_verified"] is False
    assert result["checkpoint_advanced"] is False


@pytest.mark.parametrize("mode", ["standalone", "planned", "batch", "replay", "planned_replay"])
@pytest.mark.parametrize("token", ["9" * 129, "-" + "9" * 129])
def test_oversized_discarded_json_integer_refused_before_bignum_conversion(mode, token):
    from server.servicenow_incident_staging import (
        reconcile_replayed_pages, reconcile_planned_replays,
    )
    selected = scope()
    raw = _page_with_private_json_number(token)
    planned = plan_page(selected, limit=1)
    assert len(raw) < MAX_BYTES
    with pytest.raises(StagingError, match="Invalid bounded provider JSON") as caught:
        if mode == "standalone":
            project_page(selected, raw, limit=1)
        elif mode == "planned":
            project_planned_page(selected, raw, plan=planned)
        elif mode == "batch":
            project_planned_pages(selected, [(planned, raw)])
        elif mode == "replay":
            reconcile_replayed_pages(selected, raw, page(item(A)), limit=1)
        else:
            reconcile_planned_replays(
                selected, [(planned, raw)], [(planned, page(item(A)))]
            )
    assert token not in str(caught.value)
    assert "nested_metric" not in str(caught.value)


@pytest.mark.parametrize("token", ["9" * 128, "-" + "9" * 128, "0", "-0"])
def test_bounded_discarded_json_integers_stay_private_and_compatible(token):
    selected = scope()
    planned = plan_page(selected, limit=1)
    staged = project_planned_pages(
        selected, [(planned, _page_with_private_json_number(token))]
    )
    assert staged["returned_rows"] == staged["distinct_selected_rows"] == 1
    assert staged["records"][0]["source_event_id"] == A + ":3"
    assert "nested_metric" not in json.dumps(staged)
    assert not staged["checkpoint_advanced"]
    assert staged["historical_completeness"] == "unknown"


@pytest.mark.parametrize("mode", ["standalone", "planned", "batch", "replay", "planned_replay"])
@pytest.mark.parametrize("alias, shadow", [
    ("SYS_ID", B),
    ("Sys_Updated_On", "2026-10-30 03:11:00"),
    ("SYS_MOD_COUNT", "99"),
    ("STATE", "closed"),
    ("ſtate", "closed"),  # Unicode casefold also aliases the reserved field.
    ("ｓｙｓ＿ｉｄ", B),  # NFKC fullwidth compatibility spelling.
    ("ｓｙｓ＿ｕｐｄａｔｅｄ＿ｏｎ", "2026-10-30 03:11:00"),
    ("ｓｙｓ＿ｍｏｄ＿ｃｏｕｎｔ", "99"),
    ("ｓｔａｔｅ", "closed"),
    ("𝘴𝘵𝘢𝘵𝘦", "closed"),  # NFKC mathematical styled letters.
])
def test_reserved_incident_aliases_fail_closed_across_all_staging_paths(mode, alias, shadow):
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )
    selected = scope()
    planned = plan_page(selected, limit=1)
    ambiguous = page(item(A, **{alias: shadow}))
    ordinary = page(item(A))
    with pytest.raises(StagingError, match="Ambiguous provider incident field") as caught:
        if mode == "standalone":
            project_page(selected, ambiguous, limit=1)
        elif mode == "planned":
            project_planned_page(selected, ambiguous, plan=planned)
        elif mode == "batch":
            project_planned_pages(selected, [(planned, ambiguous)])
        elif mode == "replay":
            reconcile_replayed_pages(selected, ambiguous, ordinary, limit=1)
        else:
            reconcile_planned_replays(
                selected, [(planned, ambiguous)], [(planned, ordinary)]
            )
    # Never echo provider-controlled identity/status aliases or their values.
    assert alias not in str(caught.value)
    assert shadow not in str(caught.value)


def test_reserved_alias_rejected_even_without_canonical_field():
    row = item(A)
    del row["state"]
    row["STATE"] = "2"
    with pytest.raises(StagingError, match="Ambiguous provider incident field"):
        project_page(scope(), page(row), limit=1)


def test_unrelated_private_fields_remain_discarded_and_replay_compatible():
    from server.servicenow_incident_staging import reconcile_planned_replays
    selected = scope()
    planned = plan_page(selected, limit=1)
    private = page(item(A, short_description="secret", incident_state="shadow",
                        sys_mod_counter="999", diagnostic={"Stateful": True}))
    staged = project_planned_pages(selected, [(planned, private)])
    assert staged["records"][0]["source_event_id"] == A + ":3"
    assert staged["records"][0]["reported_state"] == "2"
    assert "secret" not in json.dumps(staged)
    assert "shadow" not in json.dumps(staged)
    assert "999" not in json.dumps(staged)
    replay = reconcile_planned_replays(
        selected, [(planned, private)], [(planned, page(item(A)))]
    )
    assert replay["same_reported_revision_ids"] == [A]
    assert replay["business_outcome_verified"] is False
    assert replay["checkpoint_advanced"] is False



def test_compatibility_alias_without_canonical_field_fails_closed():
    row = item(A)
    del row["sys_id"]
    row["ｓｙｓ＿ｉｄ"] = A
    with pytest.raises(StagingError, match="Ambiguous provider incident field"):
        project_page(scope(), page(row), limit=1)


def test_unrelated_compatibility_spelling_stays_private_and_is_not_rejected():
    from server.servicenow_incident_staging import reconcile_replayed_pages
    selected = scope()
    raw = page(item(A, **{"ｐｒｉｖａｔｅ": "sensitive",
                          "𝘯𝘰𝘵𝘦": "private text"}))
    staged = project_page(selected, raw, limit=1)
    assert staged["records"][0]["source_case_id"] == A
    assert "sensitive" not in json.dumps(staged)
    assert "private text" not in json.dumps(staged)
    replay = reconcile_replayed_pages(selected, raw, page(item(A)), limit=1)
    assert replay["same_reported_revision_ids"] == [A]
    assert replay["historical_completeness"] == "unknown"
    assert replay["business_outcome_verified"] is False
    assert replay["checkpoint_advanced"] is False


@pytest.mark.parametrize("field,bad_value", [
    ("host", "attacker.invalid"),
    ("tenant_scope", "org1^ORtenant=other"),
    ("sys_ids", [A]),
    ("sys_ids", (A, A)),
    ("consented", 1),
    ("grant_current", "true"),
], ids=["host", "tenant", "ids-list", "ids-duplicate", "consent-type", "grant-type"])
def test_post_construction_scope_mutation_is_revalidated_at_every_entrypoint(field, bad_value):
    """Even a frozen instance can be altered with object.__setattr__."""
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )
    selected = scope(sys_ids=(A,))
    planned = plan_page(selected, limit=1)
    raw = page(item(A))
    object.__setattr__(selected, field, bad_value)
    entrypoints = [
        lambda: plan_page(selected, limit=1),
        lambda: project_page(selected, raw, limit=1),
        lambda: project_planned_page(selected, raw, plan=planned),
        lambda: project_planned_pages(selected, [(planned, raw)]),
        lambda: reconcile_replayed_pages(selected, raw, raw, limit=1),
        lambda: reconcile_planned_replays(selected, [(planned, raw)], [(planned, raw)]),
    ]
    for entrypoint in entrypoints:
        with pytest.raises(StagingError) as caught:
            entrypoint()
        assert "attacker.invalid" not in str(caught.value)
        assert "org1^ORtenant=other" not in str(caught.value)


def test_instance_method_shadowing_cannot_skip_scope_validation_or_revocation():
    """Never dispatch validation/authority through a mutable instance attribute."""
    denied = scope(sys_ids=(A,), consented=False)
    object.__setattr__(denied, "require_authority", lambda: None)
    with pytest.raises(StagingError, match="not currently authorized"):
        plan_page(denied, limit=1)

    malformed = scope(sys_ids=(A,))
    object.__setattr__(malformed, "host", "attacker.invalid")
    object.__setattr__(malformed, "__post_init__", lambda: None)
    with pytest.raises(StagingError, match="Unsupported ServiceNow instance host"):
        project_page(malformed, page(item(A)), limit=1)

    # Ordinary, unchanged exact-scope instances retain the prior read contract.
    authorized = scope(sys_ids=(A,))
    planned = plan_page(authorized, limit=1)
    assert project_planned_page(authorized, page(item(A)), plan=planned)["plan_bound"] is True



@pytest.mark.parametrize("field,initial,substitute", [
    ("host", {}, "another.service-now.com"),
    ("tenant_scope", {}, "other-tenant"),
    ("sys_ids", {}, (B,)),
    ("consented", {"consented": False}, True),
    ("grant_current", {"grant_current": False}, True),
], ids=["valid-host", "valid-tenant", "valid-selection", "grant-consent",
        "revoked-grant"])
def test_valid_shaped_scope_substitution_never_reuses_original_authority(
        field, initial, substitute):
    """A valid-looking replacement must not inherit constructor-time authority."""
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )
    selected = scope(sys_ids=(A,), **initial)
    # Plan metadata is deliberately from a separately authorized original.
    original = scope(sys_ids=(A,))
    planned = plan_page(original, limit=1)
    raw = page(item(A))
    object.__setattr__(selected, field, substitute)
    entrypoints = [
        lambda: plan_page(selected, limit=1),
        lambda: project_page(selected, raw, limit=1),
        lambda: project_planned_page(selected, raw, plan=planned),
        lambda: project_planned_pages(selected, [(planned, raw)]),
        lambda: reconcile_replayed_pages(selected, raw, raw, limit=1),
        lambda: reconcile_planned_replays(
            selected, [(planned, raw)], [(planned, raw)]),
    ]
    for entrypoint in entrypoints:
        with pytest.raises(StagingError) as caught:
            entrypoint()
        assert "another.service-now.com" not in str(caught.value)
        assert "other-tenant" not in str(caught.value)


def test_missing_constructor_binding_fails_closed_and_cannot_be_rebound_by_shadow():
    selected = scope(sys_ids=(A,))
    del selected.__dict__["_bound_selection"]
    object.__setattr__(selected, "_validate_fields", lambda: None)
    with pytest.raises(StagingError, match="Invalid selected incident scope binding"):
        plan_page(selected, limit=1)


def test_original_scope_binding_remains_usable_for_offline_replay():
    from server.servicenow_incident_staging import (
        reconcile_planned_replays, reconcile_replayed_pages,
    )
    selected = scope(sys_ids=(A,))
    planned = plan_page(selected, limit=1)
    raw = page(item(A))
    assert project_page(selected, raw, limit=1)["distinct_selected_rows"] == 1
    assert project_planned_page(selected, raw, plan=planned)["plan_bound"] is True
    assert project_planned_pages(
        selected, [(planned, raw)])["plan_sequence_checked"] is True
    assert reconcile_replayed_pages(
        selected, raw, raw, limit=1)["same_reported_revision_ids"] == [A]
    assert reconcile_planned_replays(
        selected, [(planned, raw)], [(planned, raw)]
    )["same_reported_revision_ids"] == [A]


@pytest.mark.parametrize("media_type", [
    "application/json", "application/json; charset=utf-8",
    "Application/JSON; charset=UTF-8", "application/json ; charset= utf-8",
])
def test_supplied_http_metadata_accepts_only_exact_selected_get(media_type):
    from server.servicenow_incident_staging import project_supplied_http_response
    selected = scope(sys_ids=(A,))
    plan = plan_page(selected, limit=1)
    raw = page(item(A, caller_id="private caller"))
    staged = project_supplied_http_response(
        selected, plan=plan, request_method="GET", request_url=plan["url"],
        response_url=plan["url"], status=200, content_type=media_type,
        redirect_count=0, body=raw,
    )
    assert staged["plan_bound"] is True
    assert staged["supplied_http_metadata_checked"] is True
    assert staged["authenticated_transport_receipt"] is False
    assert staged["transport_grant_verified"] is False
    assert staged["historical_completeness"] == "unknown"
    assert staged["checkpoint_advanced"] is False
    assert staged["not_an_authorization_to_append"] is True
    assert "private caller" not in json.dumps(staged)


@pytest.mark.parametrize("field,bad_value", [
    ("request_method", "POST"),
    ("request_method", "get"),
    ("request_url", "https://attacker.invalid/incident"),
    ("response_url", "https://attacker.invalid/incident"),
    ("status", 301),
    ("status", 401),
    ("status", 403),
    ("status", 429),
    ("status", 500),
    ("status", True),
    ("status", 200.0),
    ("redirect_count", 1),
    ("redirect_count", True),
    ("content_type", "text/html"),
    ("content_type", "application/json, text/html"),
    ("content_type", "application/json; charset=iso-8859-1"),
    ("content_type", "applicat\u0131on/json"),
    ("content_type", "appl\u0130cation/json"),
    ("content_type", "application/json\r\nX-Secret: private"),
    ("content_type", None),
], ids=[
    "write-method", "lowercase-method", "request-url", "final-url",
    "redirect-status", "unauthorized", "forbidden", "rate-limited",
    "server-error", "boolean-status", "float-status", "redirect-hop",
    "boolean-redirect", "html", "mixed-media", "non-utf8",
    "dotless-i-lookalike", "dotted-i-lookalike",
    "header-injection", "missing-media",
])
def test_supplied_http_metadata_refuses_untrusted_transport_claims(field, bad_value):
    from server.servicenow_incident_staging import project_supplied_http_response
    selected = scope(sys_ids=(A,))
    plan = plan_page(selected, limit=1)
    kwargs = {
        "request_method": "GET", "request_url": plan["url"],
        "response_url": plan["url"], "status": 200,
        "content_type": "application/json", "redirect_count": 0,
        "body": page(item(A, caller_id="private caller")),
    }
    kwargs[field] = bad_value
    with pytest.raises(StagingError, match="Supplied HTTP") as caught:
        project_supplied_http_response(selected, plan=plan, **kwargs)
    assert "private caller" not in str(caught.value)
    assert "attacker.invalid" not in str(caught.value)


def test_supplied_http_metadata_does_not_bypass_plan_scope_or_source_validation():
    from server.servicenow_incident_staging import project_supplied_http_response
    selected = scope(sys_ids=(A,))
    plan = plan_page(selected, limit=1, since="2026-10-30 03:00:00")

    def invoke(current_scope, current_plan, body):
        return project_supplied_http_response(
            current_scope, plan=current_plan, request_method="GET",
            request_url=plan["url"], response_url=plan["url"],
            status=200, content_type="application/json",
            redirect_count=0, body=body,
        )

    assert invoke(selected, plan, page(item(A)))["supplied_http_metadata_checked"]
    with pytest.raises(StagingError, match="not currently authorized"):
        invoke(scope(sys_ids=(A,), grant_current=False), plan, page(item(A)))
    with pytest.raises(StagingError, match="does not match"):
        invoke(scope(sys_ids=(A,), tenant_scope="other"), plan, page(item(A)))
    altered = dict(plan)
    altered["since"] = None
    with pytest.raises(StagingError, match="does not match"):
        invoke(selected, altered, page(item(A)))
    with pytest.raises(StagingError, match="unselected"):
        invoke(selected, plan, page(item(B)))
    with pytest.raises(StagingError, match="older than requested"):
        invoke(selected, plan, page(item(A, updated="2026-10-29 03:00:00")))
    with pytest.raises(StagingError, match="Invalid bounded provider JSON"):
        invoke(selected, plan, b'{"result":[')


def _supplied_http_page(plan, raw):
    return {
        "plan": plan, "request_method": "GET",
        "request_url": plan["url"], "response_url": plan["url"],
        "status": 200, "content_type": "application/json",
        "redirect_count": 0, "body": raw,
    }


def test_supplied_http_batch_checks_each_page_without_claiming_transport():
    from server.servicenow_incident_staging import project_supplied_http_pages
    selected = scope(sys_ids=(A, B))
    first = plan_page(selected, offset=0, limit=1)
    second = plan_page(selected, offset=1, limit=1)
    responses = [
        _supplied_http_page(first, page(item(A, caller_id="private caller"))),
        _supplied_http_page(second, page(item(
            B, updated="2026-10-30 03:11:00", short_description="private summary",
        ))),
    ]
    staged = project_supplied_http_pages(selected, responses)
    assert staged["all_supplied_pages_checked"] is True
    assert staged["supplied_http_metadata_checked"] is True
    assert staged["plan_sequence_checked"] is True
    assert staged["pages_checked"] == 2
    assert staged["distinct_selected_rows"] == staged["returned_rows"] == 2
    assert {r["source_case_id"] for r in staged["records"]} == {A, B}
    assert staged["authenticated_transport_receipt"] is False
    assert staged["transport_grant_verified"] is False
    assert staged["historical_completeness"] == "unknown"
    assert staged["checkpoint_advanced"] is False
    assert staged["not_an_authorization_to_append"] is True
    assert "private caller" not in json.dumps(staged)
    assert "private summary" not in json.dumps(staged)


@pytest.mark.parametrize("mutation", [
    "second-redirect", "second-status", "second-url", "second-method",
    "second-media", "second-tenant-plan", "second-missing-key",
    "second-extra-key", "second-nondict", "second-bad-json",
], ids=[
    "redirect", "http-401", "foreign-final-url", "write-method",
    "html", "cross-tenant-plan", "missing-metadata",
    "extra-metadata", "invalid-envelope", "malformed-body",
])
def test_supplied_http_batch_refuses_corrupt_second_response(mutation):
    from server.servicenow_incident_staging import project_supplied_http_pages
    selected = scope(sys_ids=(A, B))
    first = plan_page(selected, offset=0, limit=1)
    second = plan_page(selected, offset=1, limit=1)
    responses = [
        _supplied_http_page(first, page(item(A))),
        _supplied_http_page(second, page(item(
            B, updated="2026-10-30 03:11:00", private="discard",
        ))),
    ]
    field_changes = {
        "second-redirect": ("redirect_count", 1),
        "second-status": ("status", 401),
        "second-url": ("response_url", "https://attacker.invalid/"),
        "second-method": ("request_method", "POST"),
        "second-media": ("content_type", "text/html"),
        "second-tenant-plan": ("plan", plan_page(
            scope(sys_ids=(A, B), tenant_scope="other"), offset=1, limit=1,
        )),
        "second-bad-json": ("body", b'{"result":['),
    }
    if mutation in field_changes:
        key, value = field_changes[mutation]
        responses[1][key] = value
    elif mutation == "second-missing-key":
        del responses[1]["status"]
    elif mutation == "second-extra-key":
        responses[1]["untrusted_header"] = "private"
    else:
        responses[1] = "private"
    with pytest.raises(StagingError) as caught:
        project_supplied_http_pages(selected, responses)
    assert "private" not in str(caught.value)
    assert "attacker.invalid" not in str(caught.value)


@pytest.mark.parametrize("case", [
    "duplicate-offset", "cross-page-regression", "short-first",
    "revoked", "empty-batch", "overlong-batch", "wrong-container",
])
def test_supplied_http_batch_inherits_offset_order_and_grant_guards(case):
    from server.servicenow_incident_staging import project_supplied_http_pages
    selected = scope(sys_ids=(A, B))
    first = plan_page(selected, offset=0, limit=1)
    second = plan_page(selected, offset=1, limit=1)
    responses = [
        _supplied_http_page(first, page(item(A))),
        _supplied_http_page(second, page(item(
            B, updated="2026-10-30 03:11:00",
        ))),
    ]
    if case == "duplicate-offset":
        responses[1]["body"] = page(item(A))
    elif case == "cross-page-regression":
        responses[1]["body"] = page(item(B, updated="2026-10-30 03:09:00"))
    elif case == "short-first":
        responses[0]["body"] = page()
    elif case == "revoked":
        selected = scope(sys_ids=(A, B), grant_current=False)
    elif case == "empty-batch":
        responses = []
    elif case == "overlong-batch":
        responses = responses * 5
    else:
        responses = tuple(responses)
    with pytest.raises(StagingError):
        project_supplied_http_pages(selected, responses)

def test_supplied_http_replay_checks_both_reads_and_preserves_revision_semantics():
    from server.servicenow_incident_staging import reconcile_supplied_http_replays
    selected = scope(sys_ids=(A, B))
    plan = plan_page(selected, limit=2)
    before = [_supplied_http_page(plan, page(
        item(A, private="never retain"),
        item(B, updated="2026-10-30 03:11:00"),
    ))]
    after = [_supplied_http_page(plan, page(
        item(A, mod="4", private="also private"),
        item(B, updated="2026-10-30 03:11:00"),
    ))]
    result = reconcile_supplied_http_replays(selected, before, after)
    assert result["higher_reported_revision_ids"] == [A]
    assert result["same_reported_revision_ids"] == [B]
    assert result["same_plans_checked"] is True
    assert result["all_supplied_replay_pages_checked"] is True
    assert result["supplied_http_metadata_checked"] is True
    assert result["authenticated_transport_receipt"] is False
    assert result["transport_grant_verified"] is False
    assert result["historical_completeness"] == "unknown"
    assert result["business_outcome_verified"] is False
    assert result["checkpoint_advanced"] is False
    assert result["not_an_authorization_to_append"] is True
    assert "never retain" not in json.dumps(result)
    assert "also private" not in json.dumps(result)


@pytest.mark.parametrize("side", ["earlier", "later"])
@pytest.mark.parametrize("fault", [
    "redirect", "status", "method", "foreign-url", "html", "bad-body",
    "missing-metadata", "extra-metadata", "foreign-plan",
])
def test_supplied_http_replay_rejects_unchecked_page_on_either_side(side, fault):
    from server.servicenow_incident_staging import reconcile_supplied_http_replays
    selected = scope(sys_ids=(A, B))
    first = plan_page(selected, offset=0, limit=1)
    second = plan_page(selected, offset=1, limit=1)

    def responses():
        return [
            _supplied_http_page(first, page(item(A))),
            _supplied_http_page(second, page(item(
                B, updated="2026-10-30 03:11:00", private="secret",
            ))),
        ]

    before, after = responses(), responses()
    target = before if side == "earlier" else after
    changes = {
        "redirect": ("redirect_count", 1),
        "status": ("status", 403),
        "method": ("request_method", "POST"),
        "foreign-url": ("response_url", "https://attacker.invalid/"),
        "html": ("content_type", "text/html"),
        "bad-body": ("body", b'{"result":['),
        "foreign-plan": ("plan", plan_page(
            scope(sys_ids=(A, B), tenant_scope="other"), offset=1, limit=1,
        )),
    }
    if fault in changes:
        key, value = changes[fault]
        target[1][key] = value
    elif fault == "missing-metadata":
        del target[1]["status"]
    else:
        target[1]["untrusted_extra"] = "secret"
    with pytest.raises(StagingError) as caught:
        reconcile_supplied_http_replays(selected, before, after)
    assert "secret" not in str(caught.value)
    assert "attacker.invalid" not in str(caught.value)


def test_supplied_http_replay_refuses_changed_plans_and_revoked_grant():
    from server.servicenow_incident_staging import reconcile_supplied_http_replays
    selected = scope(sys_ids=(A, B))
    first_plan = plan_page(selected, limit=2)
    different_plan = plan_page(selected, limit=1)
    before = [_supplied_http_page(first_plan, page(
        item(A), item(B, updated="2026-10-30 03:11:00"),
    ))]
    after = [_supplied_http_page(different_plan, page(item(A)))]
    with pytest.raises(StagingError, match="different selected-record plans"):
        reconcile_supplied_http_replays(selected, before, after)
    with pytest.raises(StagingError, match="not currently authorized"):
        reconcile_supplied_http_replays(
            scope(sys_ids=(A, B), grant_current=False), before, before,
        )


def test_supplied_http_replay_keeps_gaps_and_missing_records_unknown():
    from server.servicenow_incident_staging import reconcile_supplied_http_replays
    selected = scope(sys_ids=(A, B))
    plan = plan_page(selected, limit=2)
    before = [_supplied_http_page(plan, page(
        item(A), item(B, updated="2026-10-30 03:11:00"),
    ))]
    after = [_supplied_http_page(plan, page(item(A, mod="7")))]
    result = reconcile_supplied_http_replays(selected, before, after)
    assert result["revision_gap_unknown_ids"] == [A]
    assert result["missing_unknown_ids"] == [B]
    assert result["higher_reported_revision_ids"] == []
    assert result["business_outcome_verified"] is False
    assert result["missing_means_deleted"] is False
    assert result["checkpoint_advanced"] is False
