"""Private crosswalk preparation, proposal review, retention and mining import."""
from io import BytesIO
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from server import enterprise_connector_crosswalk as local
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from gateway.connector_case_review import ReviewedCases,Plan,Export,linked_csv
from gateway.connector_crosswalk_proposals import Propose,propose
from gateway.connector_review_plans import SavedPlans,Save,Delete
from gateway.connector_jobs import JobError
from gateway.connector_event_publications import Remove
from shared.connector_crosswalk import Crosswalk,artifact_digest
from shared.connector_event_snapshot import digest
from test_connector_event_publications import deployed,source,grant,upload,pin
from test_gateway_connector_jobs import db

SECRET=bytes(range(32))
BATCH="a"*32
DOMAIN="purchase_order"
BUSINESS="private-external-business-id"


def mapping(**overrides):
    return local.Input.model_validate({"rows":[{"native_case_id":"raw-case-1","business_key":BUSINESS}],
       "basis":"reviewed_business_record","confirm_reviewed_mapping":True,
       "confirm_match_tag_disclosure":True,**overrides})


def artifact(job,g,snap,*,secret=SECRET,batch=BATCH,domain=DOMAIN,inputs=None):
    return local.prepare(snap,inputs or mapping(),job_id=job["job_id"],grant_id=g["grant_id"],
        publication_revision=g["revision"],batch_id=batch,domain=domain,secret=secret)


@pytest.fixture
def prepared(deployed):
    jobs,pubs=deployed
    reviewed=ReviewedCases(pubs);saved=SavedPlans(reviewed);saved.initialize()
    items=[]
    for _ in range(2):
        j,t,s,_=source(deployed);g=grant(pubs,j,s);upload(pubs,t,g,s)
        items.append((j,g,s,artifact(j,g,s)))
    spec={"snapshots":[pin(g) for _,g,_,_ in items],"crosswalks":[a.model_dump() for *_,a in items],
          "confirm_inspection":True,"confirm_tag_processing":True}
    return reviewed,saved,items,spec


def make_review(prepared):
    reviewed,saved,items,spec=prepared
    r=propose(reviewed,"customer",Propose.model_validate(spec),"owner")
    return r,Plan.model_validate(r["plan"])


def save_spec(r,p,**overrides):
    return Save.model_validate({"plan":p.model_dump(),"expected_review_sha256":r["review"]["review_sha256"],
        "expected_revision":0,"confirm_assertions":True,"confirm_plan_retention":True,**overrides})


def test_source_host_exact_match_proposal_and_mining_import(prepared):
    reviewed,saved,items,spec=prepared
    a,b=[x[3] for x in items]
    assert a.rows[0].case_id!=b.rows[0].case_id
    assert a.rows[0].match_tag==b.rows[0].match_tag
    assert a.key_check==b.key_check
    for private in (BUSINESS,"raw-case-1",SECRET.hex()):
        assert private not in a.model_dump_json()
    r,p=make_review(prepared)
    assert r["summary"]["candidate_groups"]==1 and r["summary"]["candidate_members"]==2
    assert not r["business_identity_independently_verified"]
    assert a.rows[0].match_tag not in json.dumps(r) and a.key_check not in json.dumps(r)
    assert p.groups[0].evidence_sha256
    result=reviewed.export("customer",Export.model_validate({**p.model_dump(),
         "expected_review_sha256":r["review"]["review_sha256"],"confirm_link_assertions":True,"confirm_export":True}),"owner")
    log=ws.import_log(BytesIO(linked_csv(result).encode()),name="Crosswalk import",source_format="csv",mapping=result["mapping"])
    assert log["events"]==4 and log["objects"]==1


def test_key_batch_domain_separation_and_mismatch(prepared):
    reviewed,saved,items,spec=prepared
    j,g,s,a=items[0]
    for changes in ({"secret":bytes(reversed(range(32)))},{"batch":"b"*32},{"domain":"invoice"}):
        other=artifact(j,g,s,**changes)
        assert other.rows[0].match_tag!=a.rows[0].match_tag
        with pytest.raises(ValidationError):
            Propose.model_validate({**spec,"crosswalks":[other.model_dump(),spec["crosswalks"][1]]})


def test_unmatched_and_missing_case_and_tamper(prepared):
    reviewed,saved,items,spec=prepared
    j,g,s,a=items[0]
    different=artifact(j,g,s,inputs=mapping(rows=[{"native_case_id":"raw-case-1","business_key":"different"}]))
    r=propose(reviewed,"customer",Propose.model_validate({**spec,"crosswalks":[different.model_dump(),spec["crosswalks"][1]]}),"owner")
    assert r["plan"] is None and r["summary"]["single_source_members"]==2
    changed=a.model_dump();changed["rows"][0]["case_id"]="f"*64
    with pytest.raises(ValidationError):
        Crosswalk.model_validate(changed)
    changed["rows_sha256"]=digest(changed["rows"])
    with pytest.raises(JobError,match="crosswalk_case_missing"):
        propose(reviewed,"customer",Propose.model_validate({**spec,"crosswalks":[changed,spec["crosswalks"][1]]}),"owner")


def test_exact_grants_and_no_implicit_processing(prepared):
    reviewed,saved,items,spec=prepared
    with pytest.raises(JobError,match="explicit_crosswalk"):
        propose(reviewed,"customer",Propose.model_validate({**spec,"confirm_tag_processing":False}),"owner")
    with pytest.raises(JobError,match="unknown_connector_job"):
        propose(reviewed,"other",Propose.model_validate(spec),"owner")
    changed={**spec["crosswalks"][0],"grant_id":"cep_"+"0"*32}
    with pytest.raises(JobError,match="crosswalk_publication_pin_mismatch"):
        propose(reviewed,"customer",Propose.model_validate({**spec,"crosswalks":[changed,spec["crosswalks"][1]]}),"owner")


def test_private_input_missing_duplicate_or_unapproved(prepared):
    _,_,items,_=prepared
    j,g,s,a=items[0]
    for inp in (mapping(confirm_reviewed_mapping=False),mapping(confirm_match_tag_disclosure=False),
                mapping(rows=[{"native_case_id":"not-in-snapshot","business_key":BUSINESS}]),
                mapping(rows=[{"native_case_id":"raw-case-1","business_key":BUSINESS}]*2)):
        with pytest.raises(ValueError):
            artifact(j,g,s,inputs=inp)


def test_native_adapter_case_domains():
    p={"key":"ab"*32,"provider":"sap","config":{"purchase_orders":["4500001234"]}}
    row=local.InputRow(native_case_id="4500001234",native_type="purchase_order",business_key="fixture")
    assert local.native_case(p,row)==rest._key(p,"sap_po_case","4500001234")
    p.update(provider="salesforce",config={"selections":[{"object":"CaseHistory","record_ids":["500000000000001"]}]})
    row=local.InputRow(native_case_id="500000000000001",native_type="CaseHistory",business_key="fixture")
    assert local.native_case(p,row)==rest._key(p,"salesforce_case","CaseHistory:500000000000001")
    p.update(provider="servicenow",config={"selections":[{"table":"incident","record_ids":["1"*32]}]})
    row=local.InputRow(native_case_id="1"*32,native_type="incident",business_key="fixture")
    assert local.native_case(p,row)==rest._key(p,"servicenow_case","incident:"+"1"*32)
    p.update(provider="sql",config={})
    row=local.InputRow(native_case_id="abc",business_key="fixture")
    assert local.native_case(p,row)==rest._key(p,"case","abc")
    p["provider"]="slack"
    with pytest.raises(ValueError):local.native_case(p,row)


def test_private_file_permissions_and_exclusive_output(tmp_path,prepared):
    _,_,items,_=prepared
    p=tmp_path/"mapping.json";p.write_text(mapping().model_dump_json());p.chmod(0o644)
    with pytest.raises(ValueError):local.read_input(p)
    p.chmod(0o600);assert local.read_input(p)==mapping()
    out=tmp_path/"crosswalk.json";local.write_output(out,items[0][3])
    assert out.stat().st_mode&0o077==0
    with pytest.raises(FileExistsError):local.write_output(out,items[0][3])


def test_saved_plan_versions_no_tags_and_reopen(prepared):
    reviewed,saved,items,spec=prepared;r,p=make_review(prepared)
    first=saved.save("customer",save_spec(r,p),"owner")
    reopened=saved.read("customer",first["plan_id"],1,"owner")
    assert reopened["plan"]==p.model_dump() and reopened["export_requires_new_confirmation"]
    assert reopened["review"]["review_sha256"]==first["review_sha256"]
    second=saved.save("customer",save_spec(r,p,plan_id=first["plan_id"],expected_revision=1),"owner")
    assert second["revision"]==2
    with pytest.raises(JobError,match="saved_review_revision_changed"):
        saved.save("customer",save_spec(r,p,plan_id=first["plan_id"],expected_revision=1),"owner")
    assert len(saved.read("customer",first["plan_id"],1,"owner")["history"])==2
    with saved.db.connect() as c:
        raw=str(saved.q(c,"SELECT definition FROM connector_review_plan_versions").fetchall())
    for _,_,_,a in items:
        assert a.rows[0].match_tag not in raw and a.key_check not in raw
    assert BUSINESS not in raw


def test_concurrent_revision_cas_and_bounded_history(prepared):
    reviewed,saved,_,_=prepared;r,p=make_review(prepared)
    first=saved.save("customer",save_spec(r,p),"owner")
    def update(_):
        try:return saved.save("customer",save_spec(r,p,plan_id=first["plan_id"],expected_revision=1),"owner")["revision"]
        except JobError:return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(update,range(4)))
    assert results.count(2)==1
    for rev in range(2,7):
        saved.save("customer",save_spec(r,p,plan_id=first["plan_id"],expected_revision=rev),"owner")
    assert len(saved.read("customer",first["plan_id"],7,"owner")["history"])==5
    with pytest.raises(JobError,match="saved_review_missing"):
        saved.read("customer",first["plan_id"],1,"owner")


def test_saved_plan_invalidated_on_grant_delete_and_cleanup(prepared):
    reviewed,saved,items,_=prepared;r,p=make_review(prepared)
    first=saved.save("customer",save_spec(r,p),"owner")
    j,g,_,_=items[0]
    reviewed.publications.remove("customer",j["job_id"],Remove(expected_publication_revision=g["revision"],confirm_delete=True),"owner",g["grant_id"])
    with pytest.raises(JobError,match="saved_review_missing"):
        saved.read("customer",first["plan_id"],1,"owner")
    saved.purge_expired()
    assert saved.inventory("customer")["plans"]==[]
    with saved.db.connect() as c:
        assert saved.q(c,"SELECT count(*) FROM connector_review_plan_versions").fetchone()[0]==0


def test_saved_plan_expiry_and_delete(prepared):
    reviewed,saved,_,_=prepared;r,p=make_review(prepared)
    first=saved.save("customer",save_spec(r,p),"owner")
    assert saved.inventory("other")["plans"]==[]
    with pytest.raises(JobError,match="explicit_review_plan"):
        saved.delete("customer",first["plan_id"],Delete(expected_revision=1,confirm_delete=False),"owner")
    assert saved.delete("customer",first["plan_id"],Delete(expected_revision=1,confirm_delete=True),"owner")["deleted"]
    other=saved.save("customer",save_spec(r,p),"owner")
    reviewed.jobs.clock=lambda:other["expires_at"]+1
    saved.purge_expired()
    assert saved.inventory("customer")["plans"]==[]


def test_save_requires_exact_preview_and_consent(prepared):
    reviewed,saved,_,_=prepared;r,p=make_review(prepared)
    with pytest.raises(JobError,match="explicit_review"):
        saved.save("customer",save_spec(r,p,confirm_plan_retention=False),"owner")
    with pytest.raises(JobError,match="cross_source_review_changed"):
        saved.save("customer",save_spec(r,p,expected_review_sha256="f"*64),"owner")


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
        assert c.get("/admin/connector-crosswalks").status_code==200
        assert c.get(base+"/saved-reviews",headers=admin).json()["plans"]==[]
        assert c.post(base+"/crosswalk-proposals",json={},headers=admin).status_code==422
        assert c.get(base+"/saved-reviews").status_code==401
        ordinary=admin_accounts.AdminIdentity("admin-fixture","admin@example.test","Admin","admin","test")
        monkeypatch.setattr(admin_accounts,"validate_session",lambda db,token:ordinary)
        header={"Authorization":"Bearer "+admin_accounts.SESSION_PREFIX+"test"}
        assert c.get(base+"/saved-reviews",headers=header).status_code==403
        assert c.post(base+"/crosswalk-proposals",json={},headers=header).status_code==403


def test_ambiguous_source_matches_withheld_until_separately_selected(prepared,deployed):
    from datetime import timedelta
    from gateway.connector_jobs import Create,profile_digest
    from server import enterprise_connector_snapshot as snapshots
    from test_enterprise_rest_feed import config,event,page,run
    reviewed,saved,items,spec=prepared
    jobs,pubs=deployed
    p=rest.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    run(p,lambda _:page([event(p),{**event(p,"event-second"),"case":"second-native-case"}]))
    snap=snapshots.prepare(p["profile_id"],rest._iso(start),rest._iso(start+timedelta(days=1)))
    made=jobs.create(Create(organization_id="customer",name="Ambiguous fixture",provider="rest",
       profile_id=p["profile_id"],profile_revision=p["revision"],profile_sha256=profile_digest(p),
       interval_minutes=15,confirm_dispatch=True,confirm_counts_only_receipts=True),"owner")
    j=made["job"];g=grant(pubs,j,snap);upload(pubs,made["worker_token"],g,snap)
    a=artifact(j,g,snap,inputs=mapping(rows=[
       {"native_case_id":"raw-case-1","business_key":BUSINESS},
       {"native_case_id":"second-native-case","business_key":BUSINESS}]))
    spec={**spec,"snapshots":[*spec["snapshots"],pin(g)],"crosswalks":[*spec["crosswalks"],a.model_dump()]}
    withheld=propose(reviewed,"customer",Propose.model_validate(spec),"owner")
    assert withheld["summary"]["ambiguous_groups"]==1 and withheld["plan"] is None
    included=propose(reviewed,"customer",Propose.model_validate({**spec,"include_ambiguous":True}),"owner")
    assert included["summary"]["candidate_groups"]==1
    assert included["plan"]["confirm_many_to_one"] is True
    assert included["review"]["summary"]["many_to_one_groups"]==1
    assert len(included["plan"]["groups"][0]["members"])==4
    with pytest.raises(ValidationError):
        Plan.model_validate({**included["plan"],"confirm_many_to_one":False})


def test_reapproved_identical_snapshot_does_not_restore_saved_plan(prepared):
    reviewed,saved,items,_=prepared;r,p=make_review(prepared)
    first=saved.save("customer",save_spec(r,p),"owner")
    j,g,s,a=items[0]
    reviewed.publications.remove("customer",j["job_id"],Remove(expected_publication_revision=g["revision"],confirm_delete=True),"owner",g["grant_id"])
    replacement=grant(reviewed.publications,j,s)
    assert replacement["grant_id"]!=g["grant_id"]
    assert saved.inventory("customer")["plans"]==[]
    with pytest.raises(JobError,match="saved_review_missing"):
        saved.read("customer",first["plan_id"],1,"owner")
