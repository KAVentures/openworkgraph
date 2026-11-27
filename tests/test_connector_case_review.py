"""Exact reviewed cross-source cases: actual source publication and mining import."""
from io import BytesIO
import json

import pytest
from pydantic import ValidationError

from gateway.connector_case_review import ReviewedCases, Catalog, Plan, Export, linked_csv
from gateway.connector_jobs import JobError
from gateway.connector_event_publications import Remove
from shared.connector_event_snapshot import Snapshot, digest
from server import event_log_workspace as ws
from test_connector_event_publications import deployed, source, grant, upload, pin
from test_gateway_connector_jobs import db


@pytest.fixture
def linked(deployed):
    jobs,store=deployed
    a,at,sa,_=source(deployed)
    b,bt,sb,_=source(deployed)
    ga=grant(store,a,sa);gb=grant(store,b,sb)
    upload(store,at,ga,sa);upload(store,bt,gb,sb)
    spec={"snapshots":[pin(ga),pin(gb)],"confirm_inspection":True,
          "groups":[{"members":[{"job_id":a["job_id"],"case_id":sa.events[0].case_id},
                                {"job_id":b["job_id"],"case_id":sb.events[0].case_id}],
                     "basis":"reviewed_business_record"}]}
    return ReviewedCases(store),spec,ga,gb


def approve(spec,report,**kw):
    return Export.model_validate({**spec,"expected_review_sha256":report["review_sha256"],
                                  "confirm_link_assertions":True,"confirm_export":True,**kw})


def test_catalog_review_export_and_exact_source_key_import(linked):
    store,spec,_,_=linked
    catalog=store.catalog("customer",Catalog(snapshots=spec["snapshots"],confirm_inspection=True,limit=1),"owner")
    assert len(catalog["cases"])==1 and catalog["next_offset"]==1
    next_page=store.catalog("customer",Catalog(snapshots=spec["snapshots"],confirm_inspection=True,limit=1,
                        offset=1,expected_scope_sha256=catalog["scope_sha256"]),"owner")
    assert next_page["next_offset"] is None
    assert catalog["cases"][0]!=next_page["cases"][0]
    review=store.preview("customer",Plan.model_validate(spec),"owner")
    assert review["summary"]["input_cases"]==2
    assert review["summary"]["export_cases"]==1
    assert review["summary"]["linked_events"]==4
    assert not review["business_identity_independently_verified"]
    result=store.export("customer",approve(spec,review),"owner")
    assert len({r["case_id"] for r in result["events"]})==1
    assert len({r["source_namespace"] for r in result["events"]})==2
    assert result["event_count"]==4
    assert all(r["activity"].startswith(r["source_namespace"]+".A_") for r in result["events"])
    payload=linked_csv(result).encode()
    log=ws.import_log(BytesIO(payload),name="Reviewed linked cases",source_format="csv",mapping=result["mapping"])
    assert log["events"]==4 and log["objects"]==1
    replay=ws.append_log(log["log_id"],BytesIO(payload),source_format="csv",mapping=result["mapping"])
    assert replay["added_events"]==0 and replay["duplicate_events_skipped"]==4


def test_reorder_same_plan_same_digest_and_rows(linked):
    store,spec,_,_=linked
    first=store.preview("customer",Plan.model_validate(spec),"owner")
    changed={**spec,"snapshots":list(reversed(spec["snapshots"])),
             "groups":[{**spec["groups"][0],"members":list(reversed(spec["groups"][0]["members"]))}]}
    second=store.preview("customer",Plan.model_validate(changed),"owner")
    assert first["review_sha256"]==second["review_sha256"]
    assert store.export("customer",approve(changed,first),"owner")["events"]==store.export("customer",approve(spec,first),"owner")["events"]


def test_exclude_unlinked_requires_new_review(linked,deployed):
    store,spec,_,_=linked
    j,token,snap,_=source(deployed)
    g=grant(store.publications,j,snap);upload(store.publications,token,g,snap)
    spec["snapshots"].append(pin(g))
    all_cases=store.preview("customer",Plan.model_validate(spec),"owner")
    assert all_cases["summary"]["unlinked_cases"]==1 and all_cases["summary"]["export_cases"]==2
    assert all_cases["summary"]["export_events"]==6
    restricted={**spec,"include_unlinked":False}
    reduced=store.preview("customer",Plan.model_validate(restricted),"owner")
    assert reduced["summary"]["export_cases"]==1 and reduced["summary"]["export_events"]==4
    with pytest.raises(JobError,match="cross_source_review_changed"):
        store.export("customer",approve(restricted,all_cases),"owner")
    assert store.export("customer",approve(restricted,reduced),"owner")["event_count"]==4


def test_unknown_members_duplicate_groups_and_unapproved_many_to_one(linked):
    store,spec,_,_=linked
    members=spec["groups"][0]["members"]
    unknown={**spec,"groups":[{"members":[members[0],{**members[1],"case_id":"f"*64}],"basis":"operator_assertion"}]}
    with pytest.raises(JobError,match="case_member_missing"):
        store.preview("customer",Plan.model_validate(unknown),"owner")
    with pytest.raises(ValidationError):
        Plan.model_validate({**spec,"groups":spec["groups"]*2})
    with pytest.raises(ValidationError):
        Plan.model_validate({**spec,"groups":[{"members":[*members,{**members[0],"case_id":"e"*64}],"basis":"operator_assertion"}]})
    with pytest.raises(ValidationError):
        Catalog(snapshots=spec["snapshots"],confirm_inspection=True,offset=1)


def test_basis_change_requires_exact_review_and_explicit_confirmation(linked):
    store,spec,_,_=linked
    r=store.preview("customer",Plan.model_validate(spec),"owner")
    changed={**spec,"groups":[{**spec["groups"][0],"basis":"operator_assertion"}]}
    with pytest.raises(JobError,match="cross_source_review_changed"):
        store.export("customer",approve(changed,r),"owner")
    with pytest.raises(JobError,match="explicit_link_assertion"):
        store.export("customer",approve(spec,r,confirm_link_assertions=False),"owner")
    with pytest.raises(JobError,match="explicit_cross_source_inspection"):
        store.preview("customer",Plan.model_validate({**spec,"confirm_inspection":False}),"owner")


def test_deleted_or_expired_publication_invalidates_preview(linked):
    store,spec,ga,gb=linked
    r=store.preview("customer",Plan.model_validate(spec),"owner")
    store.publications.remove("customer",ga["job_id"],Remove(expected_publication_revision=ga["revision"],confirm_delete=True),"owner",ga["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        store.export("customer",approve(spec,r),"owner")


def test_expiry_and_foreign_org_fail_closed(linked):
    store,spec,ga,gb=linked
    r=store.preview("customer",Plan.model_validate(spec),"owner")
    with pytest.raises(JobError,match="unknown_connector_job"):
        store.preview("other",Plan.model_validate(spec),"owner")
    store.jobs.clock=lambda:r["expires_at"]+1
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        store.export("customer",approve(spec,r),"owner")


def test_time_ties_and_day_precision_are_not_handoffs(linked):
    store,spec,_,_=linked
    with store.jobs._tx("customer") as c:
        loaded=store._read(c,"customer",Plan.model_validate(spec))
        cases,manifests,providers,count,scope,expires=loaded
        # Exercise the pure projection with a day-precision source and a
        # known timestamp tie, without rewriting the approved snapshot.
        a,b=list(cases)
        providers[a[0]]="sap"
        cases[a][0]["observed_at"]=cases[b][0]["observed_at"]
        _,r=store._compute("customer",Plan.model_validate(spec),loaded)
    group=r["groups"][0]
    assert group["day_precision_present"] and group["cross_source_timestamp_ties"]>=1
    assert group["observed_span_seconds"] is None and group["strict_adjacent_source_switches"] is None


def test_http_owner_only(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from gateway import admin_accounts
    from gateway.enterprise_app import create_enterprise_app
    from gateway.settings import GatewaySettings
    from gateway.hardening import HardeningSettings
    monkeypatch.setenv("OWG_GATEWAY_ADMIN_TOKEN_API","enabled")
    app=create_enterprise_app(settings=GatewaySettings(database_url="sqlite:///"+str(tmp_path/"http.db"),
            admin_token="test-admin",enrollment_token="test-enroll"),hardening=HardeningSettings())
    with TestClient(app) as c:
        base="/v1/admin/connector-events/customer"
        admin={"Authorization":"Bearer test-admin"}
        assert c.get("/admin/connector-case-review").status_code==200
        for suffix in ("case-catalog","case-review","case-export","case-export-csv","case-export-portable"):
            assert c.post(base+"/"+suffix,json={}).status_code==401
            assert c.post(base+"/"+suffix,json={},headers=admin).status_code==422
        ordinary=admin_accounts.AdminIdentity("admin-fixture","admin@example.test","Admin","admin","test")
        monkeypatch.setattr(admin_accounts,"validate_session",lambda db,token:ordinary)
        header={"Authorization":"Bearer "+admin_accounts.SESSION_PREFIX+"test"}
        for suffix in ("case-catalog","case-review","case-export","case-export-csv","case-export-portable"):
            assert c.post(base+"/"+suffix,json={},headers=header).status_code==403
