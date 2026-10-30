"""Synthetic adversarial ServiceNow Table API staging fixtures; no network."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from server.servicenow_incident_staging import (
    MAX_BYTES, SelectedIncidents, StagingError, plan_page, project_page,
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
