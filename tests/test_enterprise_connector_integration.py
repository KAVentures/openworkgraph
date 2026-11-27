"""Integrated enterprise connector acceptance across actual retained workspaces.

All native provider HTTP endpoints use synthetic transports. The central
publication, crosswalk, review and mining paths use real storage.
"""
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import get_args
import json
import re

import httpx
import pytest

from shared.enterprise_connector_contract import (PROVIDERS,NATIVE_ADAPTERS,
    CROSSWALK_PROVIDERS,ROTATION_PROVIDERS,FILE_SECRET_PROVIDERS,capabilities)
from shared.connector_event_snapshot import Manifest
from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import enterprise_connector_crosswalk as crosswalk
from server import enterprise_connector_rotation as rotation
from server import enterprise_connector_snapshot as snapshot
from server import event_log_workspace as ws
from server import hubspot_history_feed as hs
from server import dataverse_audit_feed as dv
from gateway.connector_jobs import Create,JobError,profile_digest
from gateway.connector_event_publications import Remove
from gateway.connector_case_review import ReviewedCases,Plan,Export,linked_csv
from gateway.connector_crosswalk_proposals import Propose,propose
from gateway.connector_review_plans import SavedPlans,Save
from test_connector_event_publications import deployed,source,grant,upload,pin
from test_gateway_connector_jobs import db
from test_enterprise_rest_feed import config,URL,ENV,TOKEN

ROOT=Path(__file__).resolve().parents[1]


def test_provider_contract_agrees_with_wire_models_catalog_and_console():
    assert set(get_args(Create.model_fields["provider"].annotation))==PROVIDERS
    assert set(get_args(Manifest.model_fields["provider"].annotation))==PROVIDERS
    assert set(NATIVE_ADAPTERS)==PROVIDERS-{"rest"}
    assert CROSSWALK_PROVIDERS==crosswalk.SUPPORTED
    assert ROTATION_PROVIDERS==rotation.SUPPORTED
    assert ROTATION_PROVIDERS<=FILE_SECRET_PROVIDERS<=PROVIDERS
    assert PROVIDERS<={x[0] for x in ops.PROVIDERS}
    html=(ROOT/"gateway/connector_jobs.html").read_text(encoding="utf-8")
    selector=html.split('id="provider">',1)[1].split("</select>",1)[0]
    assert set(re.findall(r'<option value="([^"]+)"',selector))==PROVIDERS
    assert rest._adapter("rest") is None
    for provider,module in NATIVE_ADAPTERS.items():
        adapter=rest._adapter(provider)
        assert adapter.__name__=="server."+module
        assert callable(adapter.check_binding) and callable(adapter.extract) and callable(adapter.create)
        if provider not in {"google","sql"}:
            assert callable(adapter.cutoff)
        assert capabilities(provider)["live_provider_acceptance_verified"] is False
    with pytest.raises(rest.FeedError,match="unknown_enterprise_connector_provider"):
        rest._adapter("unapproved.module")
    with pytest.raises(rest.FeedError,match="unknown_enterprise_connector_provider"):
        rest.create_profile({},"synthetic",rest._now(),provider="unapproved")


@pytest.fixture
def local(tmp_path,monkeypatch):
    monkeypatch.setattr(ws,"DATA_DIR",tmp_path)
    monkeypatch.setenv("OWG_REST_ALLOWED_ENDPOINTS",json.dumps([URL,"https://other.example.test/events"]))
    monkeypatch.setenv(ENV,TOKEN)
    monkeypatch.setenv("OWG_REST_TOKEN_SECOND","other-fixture-token")
    monkeypatch.delenv("OWG_REST_ENDPOINT_CREDENTIALS",raising=False)
    monkeypatch.delenv("OWG_CONNECTOR_SECRET_FILES",raising=False)


def test_endpoint_allowlist_denial_is_structured(local):
    with pytest.raises(rest.FeedError) as err:
        rest.create(config(endpoint="https://not-approved.example.test/events"))
    assert err.value.code=="endpoint_not_deployment_allowlisted" and err.value.status==403
    assert rest.inventory()["profiles"]==[]


def test_endpoint_credential_catalog_prevents_cross_destination_reuse(local,monkeypatch):
    other="https://other.example.test/events"
    monkeypatch.setenv("OWG_REST_ENDPOINT_CREDENTIALS",json.dumps({URL:ENV,other:"OWG_REST_TOKEN_SECOND"}))
    p=rest.create(config())
    assert p["credential_binding_available"]
    with pytest.raises(rest.FeedError,match="rest_endpoint_credential_pair_not_approved"):
        rest.create(config(endpoint=other,credential_env=ENV))
    monkeypatch.setenv("OWG_REST_ENDPOINT_CREDENTIALS",json.dumps({URL:"OWG_REST_TOKEN_SECOND"}))
    assert not rest.inventory()["profiles"][0]["credential_binding_available"]
    def never(**kwargs):
        pytest.fail("No source connection may be opened for a changed binding")
    with pytest.raises(rest.FeedError,match="rest_endpoint_credential_pair_not_approved"):
        rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,client_factory=never)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


@pytest.mark.parametrize("invalid",['[]','{}','{"x":"PATH"}',
    '{"https://other.example.test/events":"OWG_REST_TOKEN_SECOND","https://other.example.test/events":"OWG_REST_TOKEN_TEST"}'])
def test_invalid_endpoint_binding_catalog_never_falls_back(local,monkeypatch,invalid):
    monkeypatch.setenv("OWG_REST_ENDPOINT_CREDENTIALS",invalid)
    with pytest.raises(rest.FeedError,match="invalid_rest_endpoint_credential_catalog"):
        rest.create(config())


def test_public_inventory_capabilities_are_passive(local):
    p=rest.create(config())
    report=ops.inventory()
    assert {x["provider"] for x in report["retained_adapter_capabilities"]}==PROVIDERS
    assert all(not x["live_provider_acceptance_verified"] for x in report["retained_adapter_capabilities"])
    assert report["provider_reads_performed"] is False
    assert rest.inventory()["profiles"][0]["adapter_capabilities"]==capabilities("rest")


def register(jobs,retained):
    start=rest._stamp(retained["config"]["initial_since"])
    snap=snapshot.prepare(retained["profile_id"],rest._iso(start),retained["checkpoint"])
    made=jobs.create(Create(organization_id="customer",name="Native fixture",provider=retained["provider"],
        profile_id=retained["profile_id"],profile_revision=retained["revision"],
        profile_sha256=profile_digest(retained),interval_minutes=60,
        confirm_dispatch=True,confirm_counts_only_receipts=True),"owner")
    return made["job"],made["worker_token"],snap,retained


def test_rest_dataverse_hubspot_native_crosswalk_to_saved_review_and_mining(deployed,monkeypatch):
    from test_hubspot_history_feed import config as hc,record as hr,run as hrun,batch as hb,ENV as HE,TOKEN as HT,SCOPES as HS
    from test_dataverse_audit_feed import config as dc,item as di,run as drun,page as dp,gid,ENV as DE,SECRET as DS,ORIGIN
    jobs,pubs=deployed
    monkeypatch.setenv(HE,HT)
    monkeypatch.setenv(hs.CATALOG,json.dumps({"primary":{"hub_id":1001,"app_id":2001,"credential_env":HE,"scopes":HS}}))
    monkeypatch.setenv(DE,DS)
    monkeypatch.setenv(dv.CATALOG,json.dumps({"primary":{"origin":ORIGIN,"tenant_id":gid(1),"client_id":gid(2),
          "organization_id":gid(3),"application_user_id":gid(4),"credential_env":DE}}))
    h=hs.create(hc(selections=[{"object_type":"deals","record_ids":["102"],"properties":["status","owner"]}]))
    hret=hrun(h,lambda obj,body:hb([hr(h,obj)]))
    d=dv.create(dc(selections=[{"table":"opportunity","record_ids":[gid(103)]}]))
    dret=drun(d,lambda req:dp([di(d,"opportunity")]))
    sources=[source(deployed),register(jobs,hret),register(jobs,dret)]
    assert {j["provider"] for j,_,_,_ in sources}=={"rest","hubspot","dataverse"}
    inputs=[("raw-case-1",None),("102","deals"),(gid(103),"opportunity")]
    pins=[];artifacts=[];secret=bytes(range(32));business="reviewed-synthetic-business-record"
    for (j,token,snap,_),(native,kind) in zip(sources,inputs):
        g=grant(pubs,j,snap)
        upload(pubs,token,g,snap)
        mapping=crosswalk.Input.model_validate({"rows":[{"native_case_id":native,"native_type":kind,"business_key":business}],
            "basis":"reviewed_business_record","confirm_reviewed_mapping":True,"confirm_match_tag_disclosure":True})
        artifact=crosswalk.prepare(snap,mapping,job_id=j["job_id"],grant_id=g["grant_id"],
            publication_revision=g["revision"],batch_id="a"*32,domain="sales_case",secret=secret)
        assert artifact.rows[0].case_id in {e.case_id for e in snap.events}
        pins.append(pin(g));artifacts.append(artifact.model_dump())
    reviewed=ReviewedCases(pubs)
    saved=SavedPlans(reviewed);saved.initialize()
    proposal=propose(reviewed,"customer",Propose.model_validate({"snapshots":pins,"crosswalks":artifacts,
         "confirm_inspection":True,"confirm_tag_processing":True}),"owner")
    assert proposal["summary"]["candidate_groups"]==1 and proposal["summary"]["candidate_members"]==3
    plan=Plan.model_validate(proposal["plan"])
    report=proposal["review"]
    assert report["summary"]["input_events"]==5
    assert report["summary"]["export_cases"]==1 and report["summary"]["linked_input_cases"]==3
    retained=saved.save("customer",Save(plan=plan,expected_revision=0,expected_review_sha256=report["review_sha256"],
        confirm_assertions=True,confirm_plan_retention=True),"owner")
    reopened=saved.read("customer",retained["plan_id"],retained["revision"],"owner")
    spec=Export.model_validate({**reopened["plan"],"expected_review_sha256":reopened["review_sha256"],
          "confirm_link_assertions":True,"confirm_export":True})
    exported=reviewed.export("customer",spec,"owner")
    log=ws.import_log(BytesIO(linked_csv(exported).encode()),name="Three provider acceptance",
                       source_format="csv",mapping=exported["mapping"])
    assert log["events"]==5 and log["objects"]==1
    assert len(log["source_identity"]["namespaces"])==3
    replay=ws.append_log(log["log_id"],BytesIO(linked_csv(exported).encode()),source_format="csv",mapping=exported["mapping"])
    assert replay["added_events"]==0 and replay["duplicate_events_skipped"]==5
    rendered=json.dumps(exported)
    for private in (HT,DS,secret.hex(),business,"raw-case-1",gid(103),"private-new-value","private-field-value"):
        assert private not in rendered
    p=pins[1]
    pubs.remove("customer",p["job_id"],Remove(expected_publication_revision=p["publication_revision"],confirm_delete=True),"owner",p["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        reviewed.export("customer",spec,"owner")
    with pytest.raises(JobError,match="saved_review_missing_or_invalidated"):
        saved.read("customer",retained["plan_id"],retained["revision"],"owner")


def test_worker_response_is_bounded_and_strict():
    from server.enterprise_connector_worker import Worker,WorkerError
    worker=Worker(gateway="https://gateway.example.test",token="owg_connector_worker_fixture",
        organization="customer",job="cj_"+"a"*32,profile_id="rest_"+"a"*24,
        revision=1,digest="b"*64,outbox=str(ROOT.resolve()/"unused"))
    for payload,code in [(b"x"*65537,"gateway_reply_budget_exceeded"),
                         (b'{"job":{},"job":{}}',"invalid_gateway_reply"),
                         (b'[]',"invalid_gateway_reply")]:
        with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=payload))) as c:
            with pytest.raises(WorkerError,match=code):
                worker._api(c,"inspect",worker.pin())


def test_publication_review_at_maximum_combined_event_budget(deployed):
    """Four full publication windows, then reject a fifth rather than truncate."""
    from hashlib import sha256
    from shared.connector_event_snapshot import Snapshot,Event,digest,utc,MAX_EVENTS
    from gateway.connector_event_publications import Bundle,MAX_EXPORT_EVENTS
    jobs,pubs=deployed
    reviewed=ReviewedCases(pubs)
    now=rest._now()
    since=utc(rest._iso(now-timedelta(days=2)))
    until=utc(rest._iso(now-timedelta(days=1)))
    observed=utc(rest._iso(now-timedelta(days=1,hours=1)))

    def h(value):
        return sha256(value.encode()).hexdigest()

    def make_source(i):
        p={"provider":"rest","profile_id":"rest_"+f"{i:024x}","revision":1,"config":{"fixture":i}}
        job=jobs.create(Create(organization_id="customer",name="Scale fixture",
            provider=p["provider"],profile_id=p["profile_id"],profile_revision=1,
            profile_sha256=profile_digest(p),interval_minutes=60,confirm_dispatch=True,
            confirm_counts_only_receipts=True),"owner")
        events=[Event(event_id=h(f"event:{i}:{j}"),case_id=h(f"case:{i}:{j%50}"),
                      activity="A_"+h(f"activity:{j%5}"),observed_at=observed).model_dump()
                for j in range(MAX_EVENTS)]
        events.sort(key=lambda e:(e["observed_at"],e["event_id"]))
        m=Manifest(profile_id=p["profile_id"],profile_revision=1,profile_sha256=profile_digest(p),
                   provider=p["provider"],since=since,until=until,checkpoint=until,
                   event_count=len(events),case_count=50,activity_mode="coded",rows_sha256=digest(events))
        snap=Snapshot(manifest=m,events=events)
        g=grant(pubs,job["job"],snap)
        upload(pubs,job["worker_token"],g,snap)
        return job["job"],g

    sources=[make_source(i) for i in range(1,5)]
    pins=[pin(g) for _,g in sources]
    groups=[{"members":[{"job_id":j["job_id"],"case_id":h(f"case:{i}:{k}")}
                        for i,(j,_) in enumerate(sources,1)],"basis":"operator_assertion"}
            for k in range(50)]
    plan=Plan.model_validate({"snapshots":pins,"groups":groups,"confirm_inspection":True})
    preview=reviewed.preview("customer",plan,"owner")
    assert preview["summary"]["input_events"]==MAX_EXPORT_EVENTS==4*MAX_EVENTS
    assert preview["summary"]["export_cases"]==50 and preview["summary"]["linked_input_cases"]==200
    export=reviewed.export("customer",Export.model_validate({**plan.model_dump(),
         "expected_review_sha256":preview["review_sha256"],"confirm_link_assertions":True,"confirm_export":True}),"owner")
    assert export["event_count"]==MAX_EXPORT_EVENTS and len({x["case_id"] for x in export["events"]})==50
    assert len({(x["source_namespace"],x["event_id"]) for x in export["events"]})==MAX_EXPORT_EVENTS
    _,extra=make_source(5)
    too_many=[*pins,pin(extra)]
    with pytest.raises(JobError,match="combined_snapshot_event_budget_exceeded"):
        pubs.export("customer",Bundle(snapshots=too_many,confirm_export=True),"owner")
    with pytest.raises(JobError,match="combined_snapshot_event_budget_exceeded"):
        reviewed.preview("customer",Plan.model_validate({**plan.model_dump(),"snapshots":too_many}),"owner")


def test_endpoint_credential_catalog_change_during_read_prevents_commit(local,monkeypatch):
    from test_enterprise_rest_feed import event,page,run
    monkeypatch.setenv("OWG_REST_ENDPOINT_CREDENTIALS",json.dumps({URL:ENV}))
    p=rest.create(config())
    def change(_):
        monkeypatch.setenv("OWG_REST_ENDPOINT_CREDENTIALS",json.dumps({URL:"OWG_REST_TOKEN_SECOND"}))
        return page([event(p)])
    with pytest.raises(rest.FeedError):
        run(p,change)
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert ws.list_logs()["logs"]==[]


def test_new_profile_keeps_required_pair_catalog_when_removed(local,monkeypatch):
    monkeypatch.setenv("OWG_REST_ENDPOINT_CREDENTIALS",json.dumps({URL:ENV}))
    p=rest.create(config())
    assert p["config"]["credential_pair_policy"] is True
    monkeypatch.delenv("OWG_REST_ENDPOINT_CREDENTIALS")
    assert not rest.inventory()["profiles"][0]["credential_binding_available"]
    with pytest.raises(rest.FeedError,match="rest_endpoint_credential_catalog_required"):
        rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,
                 client_factory=lambda **_:pytest.fail("no source read"))
