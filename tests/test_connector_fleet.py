"""Fleet review against real SQLite/PostgreSQL job and publication storage."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from gateway import connector_fleet as fleet
from gateway.connector_jobs import Jobs, JobError, Readiness, LEASE_SECONDS
from gateway.connector_event_publications import Publications
from test_gateway_connector_jobs import db,store,make,pin,claim,receipt,change
from test_connector_event_publications import deployed,source,grant,upload


@pytest.fixture
def prepared(store):
    jobs,clock=store
    Publications(jobs).initialize()
    return jobs,clock


def select(*jobs,action="pause",cancel=False):
    return fleet.Selection(job_ids=[j["job_id"] for j in jobs],action=action,
                           allow_active_lease_cancellation=cancel)


def approve(jobs,org,selection,**overrides):
    plan=fleet.preview(jobs,org,selection)
    return fleet.Apply.model_validate({**selection.model_dump(),
        "expected_plan_sha256":plan["plan_sha256"],
        "confirm_exact_dispatch_changes":True,**overrides})


def read_job(jobs,org,j):
    with jobs._tx(org) as c:
        return jobs._get(c,org,j["job_id"])


def test_empty_fleet_and_organization_validation(prepared):
    jobs,_=prepared
    assert fleet.report(jobs,"empty")["summary"]["jobs"]==0
    with pytest.raises(JobError,match="invalid_organization"):
        fleet.report(jobs,"bad / org")
    with pytest.raises(ValidationError):
        fleet.Selection(job_ids=[],action="pause")


def test_report_distinguishes_stale_and_expired_without_mutating_job(prepared):
    jobs,clock=prepared
    _,_,a,at=make(prepared)
    _,_,b,bt=make(prepared,profile_id="rest_"+"c"*24,provider="slack")
    _,_,other,ot=make(prepared,organization_id="foreign")
    claim(jobs,a,at)
    jobs.readiness(bt,Readiness(**pin(b).model_dump(),code="local_recording_paused"))
    clock.now+=LEASE_SECONDS+1
    before=read_job(jobs,"acme",a)
    r=fleet.report(jobs,"acme")
    after=read_job(jobs,"acme",a)
    assert before==after and before["lease"] is not None
    assert r["summary"]["jobs"]==2
    assert r["summary"]["states"]=={"due":1,"expired_lease":1}
    assert r["summary"]["jobs_with_fresh_worker_report"]==0
    assert r["worklist"][0]["job_id"]==a["job_id"]
    assert r["worklist"][0]["priority"]=="urgent"
    assert not r["lease_state_mutated"] and not r["event_payloads_read"]
    assert not r["provider_health_verified"]
    raw=json.dumps(r)
    for private in (at,bt,ot,other["job_id"],a["profile_id"],a["profile_sha256"],before["lease"]["hash"]):
        assert private not in raw
    assert fleet.preview(jobs,"acme",select(a))["blocked_count"]==1


def test_preview_is_canonical_and_apply_all_or_nothing(prepared):
    jobs,clock=prepared
    _,_,a,at=make(prepared)
    _,_,b,bt=make(prepared,profile_id="rest_"+"c"*24)
    p=fleet.preview(jobs,"acme",select(a,b))
    q=fleet.preview(jobs,"acme",select(b,a))
    assert p==q and p["eligible_count"]==2
    assert not p["dispatch_changed"]
    spec=approve(jobs,"acme",select(a,b))
    # A worker claim changes sequence without changing administrative revision.
    leased=claim(jobs,b,bt)
    assert leased["claimed"]
    with pytest.raises(JobError,match="fleet_plan_changed"):
        fleet.apply(jobs,"acme",spec,"owner")
    assert read_job(jobs,"acme",a)["enabled"]
    assert read_job(jobs,"acme",b)["lease"]
    blocked=approve(jobs,"acme",select(a,b))
    with pytest.raises(JobError,match="contains_blocked_jobs"):
        fleet.apply(jobs,"acme",blocked,"owner")
    assert read_job(jobs,"acme",a)["revision"]==a["revision"]


def test_bulk_pause_and_resume_preserve_due_time_and_history(prepared):
    jobs,clock=prepared
    _,_,a,at=make(prepared)
    _,_,b,bt=make(prepared,profile_id="rest_"+"c"*24)
    c=claim(jobs,a,at)
    completed=jobs.complete(at,receipt(c,clock))["job"]
    due=completed["next_due_at"]
    out=fleet.apply(jobs,"acme",approve(jobs,"acme",select(completed,b)),"owner")
    assert out["changed_count"]==2 and not out["published_snapshots_deleted"]
    rows=jobs.inventory("acme")["jobs"]
    assert all(not x["enabled"] and x["paused_reason"]=="operator_paused" for x in rows)
    resumed=fleet.apply(jobs,"acme",approve(jobs,"acme",select(*rows,action="resume")),"owner")
    assert resumed["changed_count"]==2
    current=next(x for x in jobs.inventory("acme")["jobs"] if x["job_id"]==a["job_id"])
    assert current["next_due_at"]==due
    assert current["last_receipt"]==completed["last_receipt"]
    assert not claim(jobs,a,at)["claimed"]
    h=jobs.history("acme",a["job_id"])["history"]
    assert [x["outcome"] for x in h[:2]]==["enabled","paused"]
    assert h[0]["fleet_plan_sha256"]==resumed["plan_sha256"]


def test_explicit_cancellation_does_not_allow_bulk_recovery(prepared):
    jobs,clock=prepared
    _,_,a,at=make(prepared)
    _,_,b,bt=make(prepared,profile_id="rest_"+"c"*24)
    leased=claim(jobs,b,bt)
    selection=select(a,b,cancel=True)
    spec=approve(jobs,"acme",selection)
    assert fleet.preview(jobs,"acme",selection)["active_leases_to_cancel"]==1
    with pytest.raises(JobError,match="explicit_active_lease"):
        fleet.apply(jobs,"acme",spec,"owner")
    assert read_job(jobs,"acme",a)["enabled"]
    out=fleet.apply(jobs,"acme",approve(jobs,"acme",selection,confirm_active_lease_uncertainty=True),"owner")
    assert out["active_leases_invalidated"]==1
    assert not out["already_dispatched_local_reads_recalled"]
    with pytest.raises(JobError,match="lease_expired_or_invalidated"):
        jobs.complete(bt,receipt(leased,clock))
    current=jobs.inventory("acme")["jobs"]
    assert any(x["paused_reason"]=="cancelled_active_lease" for x in current)
    with pytest.raises(JobError,match="contains_blocked_jobs"):
        fleet.apply(jobs,"acme",approve(jobs,"acme",select(*current,action="resume")),"owner")


def test_lease_expiry_after_preview_blocks_apply(prepared):
    jobs,clock=prepared
    _,_,a,at=make(prepared)
    claim(jobs,a,at)
    spec=approve(jobs,"acme",select(a,cancel=True),confirm_active_lease_uncertainty=True)
    clock.now+=LEASE_SECONDS+1
    with pytest.raises(JobError,match="fleet_plan_changed"):
        fleet.apply(jobs,"acme",spec,"owner")
    assert read_job(jobs,"acme",a)["lease"] is not None


def test_foreign_selection_and_consent_refused(prepared):
    jobs,_=prepared
    _,_,a,at=make(prepared)
    _,_,b,bt=make(prepared,organization_id="foreign")
    with pytest.raises(JobError,match="unknown_connector_job"):
        fleet.preview(jobs,"acme",select(a,b))
    with pytest.raises(JobError,match="explicit_bulk"):
        fleet.apply(jobs,"acme",approve(jobs,"acme",select(a),confirm_exact_dispatch_changes=False),"owner")
    assert read_job(jobs,"acme",a)["enabled"]
    with pytest.raises(ValidationError):
        select(a,a)
    with pytest.raises(ValidationError):
        select(a,action="resume",cancel=True)


def test_concurrent_apply_same_review_only_one_commit(prepared,db):
    jobs,clock=prepared
    _,_,a,at=make(prepared)
    _,_,b,bt=make(prepared,profile_id="rest_"+"c"*24)
    spec=approve(jobs,"acme",select(a,b))
    replica=Jobs(db,clock)
    def attempt(service):
        try:
            return fleet.apply(service,"acme",spec,"owner")["changed_count"]
        except JobError as exc:
            assert exc.code=="connector_fleet_plan_changed"
            return 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,(jobs,replica)))==[0,2]
    assert all(x["revision"]==2 for x in jobs.inventory("acme")["jobs"])


def test_failed_audit_rolls_back_entire_batch(prepared,monkeypatch):
    jobs,_=prepared
    _,_,a,at=make(prepared)
    _,_,b,bt=make(prepared,profile_id="rest_"+"c"*24)
    spec=approve(jobs,"acme",select(a,b))
    original=jobs._audit
    calls=[]
    def audit(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:
            raise RuntimeError("synthetic transactional failure")
        return original(*args,**kwargs)
    monkeypatch.setattr(jobs,"_audit",audit)
    with pytest.raises(RuntimeError,match="synthetic"):
        fleet.apply(jobs,"acme",spec,"owner")
    assert all(x["revision"]==1 and x["enabled"] for x in jobs.inventory("acme")["jobs"])


def test_publication_grant_state_fences_bulk_plan(deployed):
    jobs,pubs=deployed
    a,at,snap,_=source(deployed)
    before=approve(jobs,"customer",select(a))
    g=grant(pubs,a,snap)
    with pytest.raises(JobError,match="fleet_plan_changed"):
        fleet.apply(jobs,"customer",before,"owner")
    p=fleet.preview(jobs,"customer",select(a))
    assert p["current_publication_upload_pins_to_invalidate"]==1
    pending=approve(jobs,"customer",select(a))
    upload(pubs,at,g,snap)
    with pytest.raises(JobError,match="fleet_plan_changed"):
        fleet.apply(jobs,"customer",pending,"owner")
    report=fleet.report(jobs,"customer")
    assert report["summary"]["reported_published_event_count"]==snap.manifest.event_count
    assert report["worklist"][0]["publication"]["state"]=="published"
    assert g["grant_id"] not in json.dumps(report)
    fleet.apply(jobs,"customer",approve(jobs,"customer",select(a)),"owner")
    updated=fleet.report(jobs,"customer")
    assert updated["worklist"][0]["publication"]["state"]=="published"
    assert not updated["worklist"][0]["publication"]["upload_pin_current"]
    assert "publication_upload_pin_changed" in updated["worklist"][0]["review_flags"]
    assert len(pubs.inventory("customer")["publications"])==1


def test_owner_only_fleet_http_and_strict_requests(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from gateway import admin_accounts
    from gateway.settings import GatewaySettings
    from gateway.hardening import HardeningSettings
    from gateway.enterprise_app import create_enterprise_app
    from test_gateway_connector_jobs import spec
    monkeypatch.setenv("OWG_GATEWAY_ADMIN_TOKEN_API","enabled")
    app=create_enterprise_app(settings=GatewaySettings(database_url="sqlite:///"+str(tmp_path/"fleet.db"),
        admin_token="test-admin",enrollment_token="test-enroll"),hardening=HardeningSettings())
    with TestClient(app) as c:
        h={"Authorization":"Bearer test-admin"}
        base="/v1/admin/connector-jobs"
        url=base+"/acme/fleet"
        assert c.get(url).status_code==401
        made=c.post(base,json=spec().model_dump(),headers=h).json()
        worker={"Authorization":"Bearer "+made["worker_token"]}
        assert c.get(url,headers=worker).status_code==401
        result=c.get(url,headers=h)
        assert result.status_code==200,result.text
        assert result.headers["cache-control"]=="no-store"
        support=c.get(url+"?support_only=true",headers=h)
        assert support.status_code==200
        assert support.json()["scope"]=="aggregate_support"
        assert "organization_id" not in support.json() and "worklist" not in support.json()
        selection=select(made["job"]).model_dump()
        response=c.post(url+"/preview",json=selection,headers=h)
        assert response.status_code==200,response.text
        plan=response.json()
        body={**selection,"expected_plan_sha256":plan["plan_sha256"],"confirm_exact_dispatch_changes":True}
        for role in ("viewer","admin"):
            who=admin_accounts.AdminIdentity("fixture-"+role,role+"@example.test",role,role,"test")
            monkeypatch.setattr(admin_accounts,"validate_session",lambda db,token:who)
            other={"Authorization":"Bearer "+admin_accounts.SESSION_PREFIX+"test"}
            assert c.get(url,headers=other).status_code==403
            assert c.post(url+"/preview",json=selection,headers=other).status_code==403
            assert c.post(url+"/apply",json=body,headers=other).status_code==403
        assert c.post(url+"/preview",content=b'{"action":"pause","action":"resume"}',
                      headers={**h,"Content-Type":"application/json"}).status_code==422
        r=c.post(url+"/apply",json=body,headers=h)
        assert r.status_code==200,r.text
        assert r.json()["changed_count"]==1


def test_aggregate_support_removes_job_identity_and_lists_all_providers(prepared):
    from shared.enterprise_connector_contract import PROVIDERS
    jobs,clock=prepared
    _,_,a,at=make(prepared,provider="zendesk")
    _,_,b,bt=make(prepared,profile_id="rest_"+"c"*24,provider="bigquery")
    detail=fleet.report(jobs,"acme")
    support=fleet.report(jobs,"acme",support_only=True)
    assert support["scope"]=="aggregate_support"
    assert detail["scope"]=="owner_worklist"
    assert support["summary"]==detail["summary"]
    assert support["providers"]==detail["providers"]
    assert {x["provider"] for x in support["providers"]}==PROVIDERS
    assert sum(x["jobs"] for x in support["providers"])==2
    assert "worklist" not in support and "organization_id" not in support
    serialized=json.dumps(support)
    for value in (a["job_id"],b["job_id"],at,bt,a["profile_id"],"acme"):
        assert value not in serialized


def test_future_success_and_checkpoint_are_unknown_not_fresh(prepared):
    from datetime import datetime, timezone
    jobs,clock=prepared
    _,_,a,at=make(prepared)
    with jobs._tx("acme") as c:
        j=jobs._get(c,"acme",a["job_id"])
        j["last_success_at"]=clock.now+3600
        j["last_success_checkpoint"]=datetime.fromtimestamp(clock.now+3600,timezone.utc).isoformat()
        jobs._put(c,j)
    row=fleet.report(jobs,"acme")["worklist"][0]
    assert row["last_success_report_at"] is None
    assert row["checkpoint_age_seconds"] is None
    assert "success_report_clock_inconsistent" in row["review_flags"]
    assert "checkpoint_clock_inconsistent" in row["review_flags"]
    assert row["priority"]=="review"
