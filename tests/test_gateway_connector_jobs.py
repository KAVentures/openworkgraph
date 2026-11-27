"""Gateway control plane: SQL concurrency, immutable pins and bounded receipts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
from uuid import uuid4

import pytest
from pydantic import ValidationError

from gateway.db import GatewayDB
from gateway.connector_jobs import Jobs, Create, Change, Claim, Receipt, JobError, LEASE_SECONDS, MAX_HISTORY
from gateway.settings import GatewaySettings
from gateway.hardening import HardeningSettings
from gateway.enterprise_app import create_enterprise_app

PROFILE="rest_"+"a"*24
DIGEST="b"*64


@pytest.fixture(params=["sqlite"]+(["postgres"] if os.getenv("OWG_PROCESS_TEST_DATABASE_URL") else []))
def db(tmp_path,request):
    schema=None
    if request.param=="postgres":
        import psycopg
        from psycopg import sql
        target=os.environ["OWG_PROCESS_TEST_DATABASE_URL"]
        parts=urlsplit(target)
        assert parts.hostname in ("localhost","127.0.0.1","postgres")
        schema="owg_connector_test_"+uuid4().hex
        with psycopg.connect(target) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        query=dict(parse_qsl(parts.query));query["options"]="-csearch_path="+schema
        url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))
    else:
        url="sqlite:///"+str(tmp_path/"gateway.db")
    try:
        db=GatewayDB(url);db.init()
        yield db
    finally:
        if schema:
            with psycopg.connect(target) as conn:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


class Clock:
    def __init__(self):
        self.now=1_800_000_000.0
    def __call__(self):
        return self.now


@pytest.fixture
def store(db):
    clock=Clock();jobs=Jobs(db,clock);jobs.initialize()
    return jobs,clock


def spec(**changes):
    return Create.model_validate({"organization_id":"acme","name":"Approved feed","provider":"rest",
            "profile_id":PROFILE,"profile_revision":1,"profile_sha256":DIGEST,"interval_minutes":15,
            "confirm_dispatch":True,"confirm_counts_only_receipts":True,**changes})


def make(store,**changes):
    jobs,clock=store;r=jobs.create(spec(**changes),"admin:fixture")
    return jobs,clock,r["job"],r["worker_token"]


def pin(job):
    return Claim(profile_id=job["profile_id"],profile_revision=job["profile_revision"],profile_sha256=job["profile_sha256"])


def claim(jobs,job,token):
    return jobs.claim(token,pin(job))


def receipt(c,clock,**changes):
    return Receipt.model_validate({"lease_id":c["lease_id"],"job_revision":c["job"]["revision"],
            "outcome":"succeeded","code":"window_committed","events_added":2,"duplicates_skipped":1,
            "checkpoint":datetime.fromtimestamp(clock.now-3600,timezone.utc).isoformat(),**changes})


def change(jobs,job,enabled,**kwargs):
    return jobs.change(job["organization_id"],job["job_id"],Change(expected_revision=job["revision"],
                       enabled=enabled,confirm_change=True,**kwargs),"admin:fixture")


def test_two_replica_claim_race_only_one_lease(store,db):
    jobs,clock,j,token=make(store)
    other=Jobs(db,clock)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda i:claim(jobs if i%2 else other,j,token),range(8)))
    winners=[r for r in results if r["claimed"]]
    assert len(winners)==1
    assert all(r["job"]["state"]=="leased" for r in results)
    done=jobs.complete(token,receipt(winners[0],clock))
    assert done["accepted"] and not done["replayed"]
    assert not claim(other,j,token)["claimed"]


def test_completion_is_idempotent_exact_digest_only(store):
    jobs,clock,j,token=make(store)
    c=claim(jobs,j,token);r=receipt(c,clock)
    first=jobs.complete(token,r)
    second=jobs.complete(token,r)
    assert second["replayed"] and first["job"]==second["job"]
    with pytest.raises(JobError,match="conflicting_completion_replay"):
        jobs.complete(token,receipt(c,clock,events_added=999))
    assert len([h for h in jobs.history("acme",j["job_id"])["history"] if h["outcome"]=="completed"])==1


def test_expired_lease_pauses_and_requires_reconciled_resume(store):
    jobs,clock,j,token=make(store)
    c=claim(jobs,j,token)
    clock.now+=LEASE_SECONDS+1
    assert not claim(jobs,j,token)["claimed"]
    paused=jobs.inventory("acme")["jobs"][0]
    assert paused["paused_reason"]=="expired_lease_requires_reconciliation"
    with pytest.raises(JobError,match="lease_expired"):
        jobs.complete(token,receipt(c,clock))
    with pytest.raises(JobError,match="reconcile_local_checkpoint"):
        change(jobs,paused,True)
    enabled=change(jobs,paused,True,confirm_ambiguous_recovery=True)
    again=claim(jobs,enabled,token)
    assert again["claimed"] and again["lease_id"]!=c["lease_id"]
    assert again["job"]["revision"]==2


def test_cancel_active_lease_and_rotate_revokes_old_worker(store):
    jobs,clock,j,token=make(store)
    c=claim(jobs,j,token)
    paused=change(jobs,j,False)
    assert paused["paused_reason"]=="cancelled_active_lease"
    with pytest.raises(JobError,match="lease_expired"):
        jobs.complete(token,receipt(c,clock))
    rotated=jobs.rotate("acme",j["job_id"],paused["revision"],True,"admin:fixture")
    with pytest.raises(JobError,match="worker_credential_required"):
        claim(jobs,j,token)
    current=rotated["job"]
    assert not current["enabled"]
    with pytest.raises(JobError,match="reconcile_local_checkpoint"):
        change(jobs,current,True)
    enabled=change(jobs,current,True,confirm_ambiguous_recovery=True)
    assert claim(jobs,enabled,rotated["worker_token"])["claimed"]


def test_worker_scope_org_isolation_and_profile_pin(store):
    jobs,clock,j,token=make(store)
    _,_,foreign,foreign_token=make(store,organization_id="foreign")
    assert len(jobs.inventory("acme")["jobs"])==1
    assert jobs.inventory("foreign")["jobs"][0]["job_id"]==foreign["job_id"]
    with pytest.raises(JobError,match="unknown_connector_job"):
        jobs.history("foreign",j["job_id"])
    with pytest.raises(JobError,match="worker_profile_pin_mismatch"):
        jobs.claim(token,Claim(profile_id=PROFILE,profile_revision=2,profile_sha256=DIGEST))
    c=claim(jobs,j,token)
    claim(jobs,foreign,foreign_token)
    with pytest.raises(JobError,match="lease_expired"):
        jobs.complete(foreign_token,receipt(c,clock))
    with pytest.raises(JobError,match="profile_already_assigned"):
        jobs.create(spec(),"admin:fixture")
    with pytest.raises(JobError,match="worker_credential_required"):
        claim(jobs,j,"device-token")


def test_cooldown_and_failure_threshold_survive_resume(store):
    jobs,clock,j,token=make(store)
    c=claim(jobs,j,token)
    result=jobs.complete(token,receipt(c,clock,outcome="failed",code="rate_limited",
           events_added=0,duplicates_skipped=0,checkpoint=None,retry_after_seconds=7200))
    job=result["job"]
    assert job["next_due_at"]==clock.now+7200
    job=change(jobs,job,False);job=change(jobs,job,True)
    assert not claim(jobs,job,token)["claimed"]
    clock.now+=7200
    for _ in range(5):
        c=claim(jobs,job,token)
        assert c["claimed"]
        job=jobs.complete(token,receipt(c,clock,outcome="failed",code="source_failed",
                      events_added=0,duplicates_skipped=0,checkpoint=None))["job"]
        clock.now=job["next_due_at"]
    assert not job["enabled"] and job["paused_reason"]=="repeated_failures"


def test_ambiguous_receipt_requires_reconciliation(store):
    jobs,clock,j,token=make(store)
    c=claim(jobs,j,token)
    paused=jobs.complete(token,receipt(c,clock,outcome="blocked",code="local_run_uncertain",
                       events_added=0,duplicates_skipped=0,checkpoint=None))["job"]
    assert paused["paused_reason"]=="worker_uncertain"
    with pytest.raises(JobError,match="reconcile_local_checkpoint"):
        change(jobs,paused,True)


def test_checkpoint_cannot_regress_or_repeat(store):
    jobs,clock,j,token=make(store)
    c=claim(jobs,j,token);r=receipt(c,clock);done=jobs.complete(token,r)
    clock.now=done["job"]["next_due_at"]
    c2=claim(jobs,j,token)
    with pytest.raises(JobError,match="checkpoint_not_monotonic"):
        jobs.complete(token,receipt(c2,clock,checkpoint=r.checkpoint))
    assert jobs.complete(token,receipt(c2,clock))["accepted"]


def test_history_is_bounded_and_secret_free(store,db):
    jobs,clock,j,token=make(store)
    for i in range(MAX_HISTORY//2+2):
        j=change(jobs,j,False)
        j=change(jobs,j,True)
    history=jobs.history("acme",j["job_id"],100)["history"]
    assert len(history)==MAX_HISTORY
    c=claim(jobs,j,token)
    jobs.complete(token,receipt(c,clock))
    text=json.dumps(jobs.history("acme",j["job_id"],100))+json.dumps(db.audit_rows("acme"))
    assert token not in text and c["lease_id"] not in text
    with db.connect() as conn:
        raw=str(db._execute(conn,"SELECT worker_token_hash,definition FROM connector_jobs").fetchall())
        assert token not in raw and c["lease_id"] not in raw


@pytest.mark.parametrize("changes",[{"profile_revision":True},{"profile_sha256":"bad"},
       {"interval_minutes":14},{"provider":"unknown"},{"organization_id":"bad org"},{"unexpected":"raw data"}])
def test_strict_create_contract(changes):
    with pytest.raises(ValidationError):
        spec(**changes)


def test_explicit_consent(store):
    jobs,clock=store
    with pytest.raises(JobError,match="explicit_dispatch"):
        jobs.create(spec(confirm_dispatch=False),"admin:fixture")


def test_enterprise_http_routes_and_scoped_worker(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from gateway import admin_accounts
    monkeypatch.setenv("OWG_GATEWAY_ADMIN_TOKEN_API","enabled")
    settings=GatewaySettings(database_url="sqlite:///"+str(tmp_path/"http.db"),admin_token="test-admin",enrollment_token="test-enroll")
    app=create_enterprise_app(settings=settings,hardening=HardeningSettings())
    with TestClient(app) as c:
        h={"Authorization":"Bearer test-admin"}
        base="/v1/admin/connector-jobs"
        assert c.get(base+"/acme").status_code==401
        r=c.post(base,json=spec().model_dump(),headers=h)
        assert r.status_code==200,r.text
        made=r.json();worker={"Authorization":"Bearer "+made["worker_token"]}
        assert r.headers["cache-control"]=="no-store"
        assert c.get(base+"/acme",headers=worker).status_code==401
        assert c.post("/v1/connector-worker/claim",json=pin(made["job"]).model_dump(),headers=h).status_code==401
        assert c.post("/v1/connector-worker/claim",json=pin(made["job"]).model_dump(),headers=worker).json()["claimed"]
        assert c.get(base+"/acme",headers=h).json()["jobs"][0]["state"]=="leased"
        assert c.get(base+"/foreign",headers=h).json()["jobs"]==[]
        assert c.post(base,content=b'{"name":"a","name":"b"}',headers={**h,"Content-Type":"application/json"}).status_code==422
        assert c.get("/admin/connector-jobs").status_code==200
        # The real named-admin middleware rejects a read-only session.
        viewer=admin_accounts.AdminIdentity("viewer-fixture","viewer@example.test","Viewer","viewer","test")
        monkeypatch.setattr(admin_accounts,"validate_session",lambda db,token:viewer)
        viewer_header={"Authorization":"Bearer "+admin_accounts.SESSION_PREFIX+"test"}
        assert c.get(base+"/acme",headers=viewer_header).status_code==200
        assert c.post(base,json=spec().model_dump(),headers=viewer_header).status_code==403
