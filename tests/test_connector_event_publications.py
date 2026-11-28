"""Exact disclosure grants and retained local -> central -> local mining round trips."""
from datetime import timedelta
from io import BytesIO
import json

import httpx
import pytest
from pydantic import ValidationError

from gateway.connector_jobs import Jobs, Create, Change, JobError, profile_digest
from gateway.connector_event_publications import Publications, Approve, Upload, Remove, Bundle, bundle_csv
from shared.connector_event_snapshot import Snapshot, manifest_digest, digest
from server import enterprise_rest_feed as rest
from server import enterprise_connector_snapshot as local
from server import event_log_workspace as ws
from test_enterprise_rest_feed import config, event, page, run, ENV, TOKEN, URL
from test_gateway_connector_jobs import db  # SQLite and disposable PostgreSQL fixture


@pytest.fixture
def deployed(tmp_path,monkeypatch,db):
    root=tmp_path/"local"
    root.mkdir()
    monkeypatch.setattr(ws,"DATA_DIR",root)
    monkeypatch.setenv("OWG_REST_ALLOWED_ENDPOINTS",json.dumps([URL]))
    monkeypatch.setenv(ENV,TOKEN)
    jobs=Jobs(db);jobs.initialize()
    store=Publications(jobs);store.initialize()
    return jobs,store


def source(deployed,*,org="customer",labels=False):
    jobs,store=deployed
    p=rest.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    retained=run(p,lambda _:page([event(p,step="Private label"),event(p,"raw-event-2",step="Approve")]))
    snap=local.prepare(p["profile_id"],rest._iso(start),rest._iso(start+timedelta(days=1)),
                       activity_mode="labels" if labels else "coded",confirm_labels=labels)
    made=jobs.create(Create(organization_id=org,name="Approved source",provider="rest",
        profile_id=p["profile_id"],profile_revision=p["revision"],profile_sha256=profile_digest(p),
        interval_minutes=15,confirm_dispatch=True,confirm_counts_only_receipts=True),"owner:fixture")
    return made["job"],made["worker_token"],snap,retained


def approval(job,snap,**kwargs):
    return Approve.model_validate({"expected_job_revision":job["revision"],
        "manifest":snap.manifest.model_dump(),"expected_publication_revision":0,
        "retention_hours":24,"confirm_event_disclosure":True,
        "confirm_activity_labels":False,**kwargs})


def grant(store,job,snap,**kwargs):
    return store.approve(job["organization_id"],job["job_id"],approval(job,snap,**kwargs),"owner:fixture")


def upload(store,token,g,snap):
    return store.upload(token,Upload(grant_id=g["grant_id"],snapshot=snap))


def pin(g):
    return {"job_id":g["job_id"],"grant_id":g["grant_id"],"manifest_sha256":g["manifest_sha256"],
            "publication_revision":g["revision"]}


def bundle(store,*gs,org="customer"):
    return store.export(org,Bundle(snapshots=[pin(g) for g in gs],confirm_export=True),"owner:fixture")


def test_retained_snapshot_roundtrip_and_case_namespace_isolation(deployed):
    jobs,store=deployed
    a,at,sa,ra=source(deployed)
    b,bt,sb,rb=source(deployed)
    ga=grant(store,a,sa);gb=grant(store,b,sb)
    assert upload(store,at,ga,sa)["state"]=="published"
    assert upload(store,bt,gb,sb)["state"]=="published"
    result=bundle(store,ga,gb)
    assert result["event_count"]==4
    assert len({e["source_namespace"] for e in result["events"]})==2
    assert all(e["activity"].startswith("A_") for e in result["events"])
    serialized=json.dumps(result)
    for private in (TOKEN,ENV,URL,"raw-event","raw-case","raw-user","Private label","Approve"):
        assert private not in serialized
    imported=ws.import_log(BytesIO(bundle_csv(result).encode()),name="Central integration review",
                          source_format="csv",mapping=result["mapping"])
    assert imported["events"]==4 and imported["objects"]==2
    assert len(imported["source_identity"]["namespaces"])==2
    appended=ws.append_log(imported["log_id"],BytesIO(bundle_csv(result).encode()),
                           source_format="csv",mapping=result["mapping"])
    assert appended["added_events"]==0 and appended["duplicate_events_skipped"]==4


def test_label_disclosure_requires_both_explicit_source_and_gateway_approval(deployed):
    _,store=deployed
    j,token,snap,retained=source(deployed,labels=True)
    with pytest.raises(ValueError,match="explicit activity"):
        local.prepare(j["profile_id"],snap.manifest.since,snap.manifest.until,activity_mode="labels")
    with pytest.raises(JobError,match="explicit_event"):
        grant(store,j,snap)
    g=grant(store,j,snap,confirm_activity_labels=True)
    upload(store,token,g,snap)
    assert {e["activity"] for e in bundle(store,g)["events"]}=={"Private label","Approve"}


def test_upload_requires_exact_grant_and_worker_and_digest(deployed):
    _,store=deployed
    j,token,snap,_=source(deployed)
    foreign,other,foreign_snap,_=source(deployed,org="other")
    with pytest.raises(JobError,match="no_current"):
        store.preflight(token)
    g=grant(store,j,snap)
    with pytest.raises(JobError,match="publication_grant_expired"):
        upload(store,other,g,snap)
    rows=[e.model_dump() for e in snap.events]
    rows[0]["activity"]="A_"+"0"*64
    changed=Snapshot(manifest={**snap.manifest.model_dump(),"rows_sha256":digest(rows)},events=rows)
    with pytest.raises(JobError,match="approved_manifest_mismatch"):
        upload(store,token,g,changed)
    assert upload(store,token,g,snap)["replayed"] is False
    assert upload(store,token,g,snap)["replayed"] is True
    assert store.preflight(token)["state"]=="published"
    assert "events" not in store.preflight(token)
    assert store.inventory("other")["publications"]==[]


def test_stale_job_revision_invalidates_pending_upload(deployed):
    jobs,store=deployed
    j,token,snap,_=source(deployed)
    g=grant(store,j,snap)
    jobs.change(j["organization_id"],j["job_id"],Change(expected_revision=j["revision"],
        enabled=False,confirm_change=True),"owner:fixture")
    with pytest.raises(JobError,match="no_current"):
        store.preflight(token)
    with pytest.raises(JobError,match="publication_grant_expired"):
        upload(store,token,g,snap)


def test_delete_and_reapprove_exact_digest_cannot_resurrect_stale_grant(deployed):
    _,store=deployed
    j,token,snap,_=source(deployed)
    old=grant(store,j,snap)
    upload(store,token,old,snap)
    with pytest.raises(JobError,match="delete_prior"):
        grant(store,j,snap,expected_publication_revision=1)
    assert store.remove("customer",j["job_id"],Remove(expected_publication_revision=1,
                        confirm_delete=True),"owner:fixture",old["grant_id"])["deleted"]
    new=grant(store,j,snap)
    assert old["grant_id"]!=new["grant_id"]
    with pytest.raises(JobError,match="publication_grant_expired"):
        upload(store,token,old,snap)
    upload(store,token,new,snap)
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        bundle(store,old)
    assert bundle(store,new)["event_count"]==2


def test_expiry_immediately_denies_reads_and_purge_removes_payload(deployed,db):
    jobs,store=deployed
    j,token,snap,_=source(deployed)
    g=grant(store,j,snap)
    upload(store,token,g,snap)
    jobs.clock=lambda:g["expires_at"]+1
    assert store.inventory("customer")["publications"]==[]
    with pytest.raises(JobError):
        bundle(store,g)
    with pytest.raises(JobError):
        store.preflight(token)
    store.purge_expired()
    with db.connect() as c:
        assert db._execute(c,"SELECT count(*) FROM connector_event_publications").fetchone()[0]==0


def test_local_source_deleted_prevents_preparation(deployed):
    _,store=deployed
    j,token,snap,retained=source(deployed)
    ws.delete_log(retained["log_id"])
    with pytest.raises(ValueError):
        local.prepare(j["profile_id"],snap.manifest.since,snap.manifest.until)


def test_private_artifact_exclusive_write_and_publish_protocol(deployed,tmp_path):
    _,store=deployed
    j,token,snap,_=source(deployed)
    g=grant(store,j,snap)
    path=tmp_path/"approved.json"
    local.write_private(path,snap)
    assert path.stat().st_mode & 0o077==0
    assert local.read_private(path)==snap
    with pytest.raises(FileExistsError):
        local.write_private(path,snap)
    calls=[]
    def factory(**kwargs):
        def dispatch(request):
            calls.append(request.method)
            assert request.headers["Authorization"]=="Bearer "+token
            if request.method=="GET":
                return httpx.Response(200,json=store.preflight(token))
            return httpx.Response(200,json=store.upload(token,Upload.model_validate_json(request.content)))
        return httpx.Client(transport=httpx.MockTransport(dispatch),**kwargs)
    assert local.publish(snap,gateway="https://gateway.example.test",token=token,
                         organization="customer",job=j["job_id"],expected_grant=g["grant_id"],client_factory=factory)["published"]
    assert calls==["GET","POST"]


@pytest.mark.parametrize("mutate",[
    lambda data:data["events"][0].update(activity="raw label"),
    lambda data:data["events"][0].update(resource="private"),
    lambda data:data["events"][0].update(observed_at="2020-01-01T00:00:00Z"),
    lambda data:data["events"].append(data["events"][0]),
    lambda data:data["manifest"].update(case_count=2),
    lambda data:data["manifest"].update(profile_revision=True),
])
def test_strict_snapshot_projection(deployed,mutate):
    j,token,snap,_=source(deployed)
    value=snap.model_dump()
    mutate(value)
    with pytest.raises(ValidationError):
        Snapshot.model_validate(value)


def test_http_owner_only_and_worker_preflight(tmp_path,monkeypatch):
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
        assert c.get(base,headers=admin).status_code==200
        assert c.get(base).status_code==401
        assert c.get("/admin/connector-events").status_code==200
        ordinary=admin_accounts.AdminIdentity("admin-fixture","admin@example.test","Admin","admin","test")
        monkeypatch.setattr(admin_accounts,"validate_session",lambda db,token:ordinary)
        header={"Authorization":"Bearer "+admin_accounts.SESSION_PREFIX+"test"}
        assert c.get(base,headers=header).status_code==403
        assert c.post(base+"/export",json={},headers=header).status_code==403
        assert c.post("/v1/connector-worker/event-publication",content=b"x"*100,
                      headers={"Content-Type":"application/json","Authorization":"Bearer invalid"}).status_code==401
