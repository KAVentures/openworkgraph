"""Counts-only quality policy, exact grant fencing and mining acceptance."""
from datetime import timedelta
from io import BytesIO
from random import Random
import json

import pytest
from pydantic import ValidationError

from shared.connector_publication_quality import summarize,evaluate,bps
from shared.connector_event_snapshot import Snapshot,digest,utc
from gateway.connector_publication_quality import Review,Export,review,export
from gateway.connector_event_publications import Remove,bundle_csv
from gateway.connector_jobs import Create,JobError
from server import event_log_workspace as ws
from test_connector_event_publications import deployed,source,grant,upload,pin
from test_gateway_connector_jobs import db


def row(case,activity,at):
    return {"case_id":case,"activity":activity,"observed_at":utc(at)}


def test_quality_metrics_do_not_invent_tie_order_or_leak_labels():
    rows=[row("private-a","private-label-1","2026-01-01T10:00:00Z"),
          row("private-a","private-label-2","2026-01-01T10:00:00Z"),
          row("private-b","private-label-1","2026-01-01T10:00:00Z"),
          row("private-b","private-label-1","2026-01-01T10:01:00Z"),
          row("private-c","private-label-3","2026-01-01T11:00:00Z")]
    r=summarize(rows)
    assert r["events"]==5 and r["cases"]==3 and r["distinct_activities"]==3
    assert r["singleton_case_rate_bps"]==3334 and r["single_activity_case_rate_bps"]==6667
    assert r["repeated_activity_occurrences"]==1 and r["largest_activity_rate_bps"]==6000
    assert r["same_reported_time_events"]==2 and r["same_reported_time_cases"]==1
    assert r["timestamp_tie_event_rate_bps"]==4000
    assert r["cases_eligible_for_reported_order"]==2
    assert r["distinct_reported_activity_sequences"]==2
    assert r["reported_span_seconds"]=={"p50":0,"p90":60,"max":60}
    assert "private" not in json.dumps(r)
    assert summarize(list(reversed(rows)))==r


def test_day_precision_is_indeterminate_not_zero():
    rows=[row("a","x","2026-01-01T00:00:00Z"),row("a","y","2026-01-02T00:00:00Z")]
    r=summarize(rows,day_precision=True)
    assert r["timestamp_tie_event_rate_bps"] is None
    assert r["cases_eligible_for_reported_order"]==0 and r["reported_span_seconds"] is None
    assert r["distinct_reported_activity_sequences"] is None
    assert evaluate(r,{"max_timestamp_tie_event_rate_bps":10000})[0]["status"]=="indeterminate"
    assert evaluate(r,{"min_cases_eligible_for_reported_order":1})[0]["status"]=="fail"


def test_rates_round_conservatively_at_threshold_boundaries():
    assert bps(1,3)==3334 and bps(0,3)==0 and bps(3,3)==10000
    r={"singleton_case_rate_bps":bps(1,3)}
    assert evaluate(r,{"max_singleton_case_rate_bps":3333})[0]["status"]=="fail"
    assert evaluate(r,{"max_singleton_case_rate_bps":3334})[0]["status"]=="pass"


def test_randomized_counts_match_independent_grouping():
    rng=Random(902)
    for _ in range(300):
        rows=[row(str(rng.randrange(10)),str(rng.randrange(5)),
                  f"2026-01-01T00:00:{rng.randrange(10):02d}Z") for _ in range(rng.randrange(1,100))]
        r=summarize(rows)
        cases=set(e["case_id"] for e in rows)
        tied=sum(sum(e["case_id"]==v["case_id"] and e["observed_at"]==v["observed_at"] for v in rows)>1 for e in rows)
        singleton=sum(sum(e["case_id"]==c for e in rows)==1 for c in cases)
        assert r["same_reported_time_events"]==tied
        assert r["singleton_cases"]==singleton
        assert r["cases"]==len(cases)
        assert r["cases_eligible_for_reported_order"]+r["cases_excluded_from_reported_order"]==len(cases)


@pytest.fixture
def published(deployed):
    jobs,store=deployed
    a,at,sa,_=source(deployed,labels=True)
    b,bt,sb,_=source(deployed)
    ga=grant(store,a,sa,confirm_activity_labels=True);gb=grant(store,b,sb)
    upload(store,at,ga,sa);upload(store,bt,gb,sb)
    spec={"snapshots":[pin(ga),pin(gb)],"policy":{"min_events":2,"min_cases":1},
          "confirm_inspection":True}
    return store,spec,ga,gb,sa,sb


def approved(spec,r,**overrides):
    return Export.model_validate({**spec,"expected_quality_sha256":r["quality_sha256"],
        "confirm_quality_gated_export":True,"confirm_event_export":True,**overrides})


def test_exact_quality_roundtrip_and_mining_replay(published):
    store,spec,ga,gb,sa,sb=published
    r=review(store,"customer",Review.model_validate(spec),"owner")
    assert r["all_selected_sources_pass"] and r["summary"]["events"]==4
    assert r["summary"]["source_local_cases"]==2
    assert not r["provider_completeness_verified"] and not r["business_identity_verified"]
    assert r["quality_sha256"]==digest({k:v for k,v in r.items() if k!="quality_sha256"})
    for e in [*sa.events,*sb.events]:
        assert e.event_id not in json.dumps(r) and e.case_id not in json.dumps(r)
        assert e.activity not in json.dumps(r)
    reverse={**spec,"snapshots":list(reversed(spec["snapshots"]))}
    assert review(store,"customer",Review.model_validate(reverse),"owner")==r
    result=export(store,"customer",approved(reverse,r),"owner")
    assert result["quality_review"]==r and result["event_count"]==4
    payload=bundle_csv(result).encode()
    log=ws.import_log(BytesIO(payload),name="Quality accepted",source_format="csv",mapping=result["mapping"])
    assert log["events"]==4 and log["objects"]==2
    replay=ws.append_log(log["log_id"],BytesIO(payload),source_format="csv",mapping=result["mapping"])
    assert replay["added_events"]==0 and replay["duplicate_events_skipped"]==4


def test_source_override_replaces_policy_and_failure_cannot_export(published):
    store,spec,ga,gb,_,_=published
    changed={**spec,"policy":{"min_events":3},"source_overrides":{ga["job_id"]:{"min_events":2}}}
    r=review(store,"customer",Review.model_validate(changed),"owner")
    assert r["summary"]["passing_sources"]==1 and r["summary"]["failing_sources"]==1
    assert not r["all_selected_sources_pass"]
    with pytest.raises(JobError,match="publication_quality_gates_not_satisfied"):
        export(store,"customer",approved(changed,r),"owner")
    passing={**changed,"source_overrides":{ga["job_id"]:{"min_events":2},gb["job_id"]:{"min_cases":1}}}
    new=review(store,"customer",Review.model_validate(passing),"owner")
    assert new["all_selected_sources_pass"]
    with pytest.raises(JobError,match="quality_review_changed"):
        export(store,"customer",approved(passing,r),"owner")


def test_exact_policies_and_scope_are_required(published):
    store,spec,ga,gb,_,_=published
    for changes in ({"policy":{}},{"policy":{"min_events":True}},
        {"policy":{"max_singleton_case_rate_bps":10001}},
        {"snapshots":[pin(ga),pin(ga)]},{"source_overrides":{"foreign":{"min_events":1}}}):
        with pytest.raises(ValidationError):
            Review.model_validate({**spec,**changes})
    with pytest.raises(JobError,match="explicit_publication_quality_inspection"):
        review(store,"customer",Review.model_validate({**spec,"confirm_inspection":False}),"owner")
    r=review(store,"customer",Review.model_validate(spec),"owner")
    for field in ("confirm_event_export","confirm_quality_gated_export"):
        with pytest.raises(JobError,match="explicit_quality_and_event"):
            export(store,"customer",approved(spec,r,**{field:False}),"owner")


def test_revocation_and_expiry_reject_exact_gate(published):
    store,spec,ga,gb,_,_=published
    r=review(store,"customer",Review.model_validate(spec),"owner")
    store.remove("customer",ga["job_id"],Remove(expected_publication_revision=ga["revision"],confirm_delete=True),"owner",ga["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        export(store,"customer",approved(spec,r),"owner")
    store.jobs.clock=lambda:r["expires_at"]+1
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        review(store,"customer",Review.model_validate({"snapshots":[pin(gb)],"policy":{"min_events":1},"confirm_inspection":True}),"owner")


def test_wrong_tenant_and_corrupt_payload_fail_closed(published):
    store,spec,ga,gb,_,_=published
    with pytest.raises(JobError):
        review(store,"foreign",Review.model_validate(spec),"owner")
    with store.jobs._tx("customer") as c:
        store.q(c,"UPDATE connector_event_publications SET payload=? WHERE organization_id=? AND job_id=?",
                '{"broken":true}',"customer",ga["job_id"])
    with pytest.raises(JobError,match="retained_snapshot_integrity_failure"):
        review(store,"customer",Review.model_validate(spec),"owner")


def test_sap_date_only_gate_requires_appropriate_source_policy(deployed):
    jobs,store=deployed
    j,token,snap,_=source(deployed)
    made=jobs.create(Create(organization_id="customer",name="SAP fixture",provider="sap",
        profile_id="rest_"+"a"*24,profile_revision=1,profile_sha256="b"*64,
        interval_minutes=60,confirm_dispatch=True,confirm_counts_only_receipts=True),"owner")
    m=snap.manifest.model_dump()
    from datetime import datetime
    first=datetime.fromisoformat(m["since"]).replace(hour=0,minute=0,second=0,microsecond=0)
    start=utc(first.isoformat());end=utc((first+timedelta(days=1)).isoformat())
    events=[{**e.model_dump(),"observed_at":start} for e in snap.events]
    events.sort(key=lambda e:(e["observed_at"],e["event_id"]))
    s=Snapshot(manifest={**m,"provider":"sap","profile_id":made["job"]["profile_id"],
        "profile_sha256":"b"*64,"profile_revision":1,"since":start,"until":end,
        "rows_sha256":digest(events)},events=events)
    g=grant(store,made["job"],s);upload(store,made["worker_token"],g,s)
    spec={"snapshots":[pin(g)],"policy":{"max_timestamp_tie_event_rate_bps":10000},"confirm_inspection":True}
    r=review(store,"customer",Review.model_validate(spec),"owner")
    assert r["summary"]["indeterminate_sources"]==1
    with pytest.raises(JobError,match="publication_quality_gates_not_satisfied"):
        export(store,"customer",approved(spec,r),"owner")
    corrected={**spec,"source_overrides":{g["job_id"]:{"min_cases":1}}}
    assert review(store,"customer",Review.model_validate(corrected),"owner")["all_selected_sources_pass"]


def test_quality_routes_require_owner(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from gateway import admin_accounts
    from gateway.enterprise_app import create_enterprise_app
    from gateway.settings import GatewaySettings
    from gateway.hardening import HardeningSettings
    monkeypatch.setenv("OWG_GATEWAY_ADMIN_TOKEN_API","enabled")
    app=create_enterprise_app(settings=GatewaySettings(database_url="sqlite:///"+str(tmp_path/"http.db"),
            admin_token="test-admin",enrollment_token="test-enroll"),hardening=HardeningSettings())
    with TestClient(app) as c:
        base="/v1/admin/connector-events/customer/"
        assert c.get("/admin/connector-publication-quality").status_code==200
        for path in ("quality-review","quality-export","quality-export-csv","case-quality-export"):
            assert c.post(base+path,json={}).status_code==401
            assert c.post(base+path,json={},headers={"Authorization":"Bearer test-admin"}).status_code==422
        ordinary=admin_accounts.AdminIdentity("admin-fixture","admin@example.test","Admin","admin","test")
        monkeypatch.setattr(admin_accounts,"validate_session",lambda db,token:ordinary)
        header={"Authorization":"Bearer "+admin_accounts.SESSION_PREFIX+"test"}
        for path in ("quality-review","quality-export","quality-export-csv","case-quality-export"):
            assert c.post(base+path,json={},headers=header).status_code==403


def test_linked_quality_export_combines_exact_provenance(published):
    from gateway.connector_case_review import ReviewedCases,Plan,linked_csv
    from gateway.connector_publication_quality import LinkedExport,linked_export
    store,spec,ga,gb,sa,sb=published
    reviewed=ReviewedCases(store)
    quality=review(store,"customer",Review.model_validate(spec),"owner")
    plan={"snapshots":spec["snapshots"],"confirm_inspection":True,"groups":[
        {"members":[{"job_id":ga["job_id"],"case_id":sa.events[0].case_id},
                    {"job_id":gb["job_id"],"case_id":sb.events[0].case_id}],
         "basis":"reviewed_business_record"}]}
    linkage=reviewed.preview("customer",Plan.model_validate(plan),"owner")
    request={**plan,"expected_review_sha256":linkage["review_sha256"],"confirm_link_assertions":True,
             "confirm_export":True,"quality":spec,"expected_quality_sha256":quality["quality_sha256"],
             "confirm_quality_gated_export":True}
    result=linked_export(reviewed,"customer",LinkedExport.model_validate(request),"owner")
    assert result["quality_review"]==quality and result["review_sha256"]==linkage["review_sha256"]
    assert result["event_count"]==4 and len({x["case_id"] for x in result["events"]})==1
    log=ws.import_log(BytesIO(linked_csv(result).encode()),name="Quality accepted linked cases",
                      source_format="csv",mapping=result["mapping"])
    assert log["events"]==4 and log["objects"]==1
    with pytest.raises(ValidationError):
        LinkedExport.model_validate({**request,"quality":{**spec,"snapshots":[pin(ga)]}})
    with pytest.raises(JobError,match="quality_review_changed"):
        linked_export(reviewed,"customer",LinkedExport.model_validate({**request,
                      "expected_quality_sha256":"e"*64}),"owner")
    failing={**spec,"policy":{"min_events":3}}
    failed=review(store,"customer",Review.model_validate(failing),"owner")
    with pytest.raises(JobError,match="publication_quality_gates_not_satisfied"):
        linked_export(reviewed,"customer",LinkedExport.model_validate({**request,
                      "quality":failing,"expected_quality_sha256":failed["quality_sha256"]}),"owner")
    with pytest.raises(JobError,match="cross_source_review_changed"):
        linked_export(reviewed,"customer",LinkedExport.model_validate({**request,
                      "expected_review_sha256":"f"*64}),"owner")
    with pytest.raises(JobError,match="explicit_quality_linkage_and_export"):
        linked_export(reviewed,"customer",LinkedExport.model_validate({**request,
                      "confirm_quality_gated_export":False}),"owner")
    store.remove("customer",ga["job_id"],Remove(expected_publication_revision=ga["revision"],
                 confirm_delete=True),"owner",ga["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        linked_export(reviewed,"customer",LinkedExport.model_validate(request),"owner")
