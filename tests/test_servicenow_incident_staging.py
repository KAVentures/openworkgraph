"""Synthetic adversarial ServiceNow Table API staging fixtures; no network."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from server.servicenow_incident_staging import (
    MAX_BYTES, SelectedIncidents, StagingError, plan_page, project_page,
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
