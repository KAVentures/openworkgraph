"""Exact grant-bound exception lifecycle using actual connector publication fixtures."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from gateway.connector_exception_triage import Queues,Save,Page,Change,Delete,MAX_HISTORY
from gateway.connector_linked_conformance import Review,review
from gateway.connector_event_publications import Remove
from gateway.connector_jobs import JobError
from test_connector_linked_conformance import linked,selected_rules
from test_connector_event_publications import deployed
from test_gateway_connector_jobs import db


@pytest.fixture
def prepared(linked):
    reviewed,req,ga,gb,sa,sb=linked
    spec={**req,"rules":selected_rules(ga,gb,sa,sb),"confirm_conformance_review":True}
    result=review(reviewed,"customer",Review.model_validate(spec),"owner")
    queue=Queues(reviewed);queue.initialize()
    save={**spec,"expected_conformance_sha256":result["conformance_sha256"],"confirm_exception_retention":True}
    return queue,save,ga,gb,sa,sb


def create(prepared):
    q,spec,*_=prepared
    return q.save("customer",Save.model_validate(spec),"owner")["queue"]


def items(q,d):
    return q.page("customer",d["queue_id"],Page(expected_revision=d["revision"]))["items"]


def update(q,d,item,state,reason=None,**overrides):
    spec={"expected_queue_revision":d["revision"],"edits":[{"item_id":item["item_id"],
          "expected_revision":item["revision"],"state":state,"reason":reason}],
          "confirm_disposition":True,**overrides}
    return q.change("customer",d["queue_id"],Change.model_validate(spec),"owner")


def test_create_is_recomputed_idempotent_and_does_not_store_labels(prepared):
    q,spec,ga,gb,sa,sb=prepared
    d=create(prepared)
    assert d["exception_count"]==1 and d["states"]=={"open":1,"investigating":0,"reviewed":0,"deferred":0}
    assert d["revision"]==1 and not d["actual_process_compliance_verified"]
    item=items(q,d)[0]
    assert item["rule_id"]=="forbidden_b" and item["status"]=="not_satisfied"
    assert item["state"]=="open" and item["revision"]==1 and item["history"]==[]
    replay=q.save("customer",Save.model_validate(spec),"owner")
    assert replay=={"queue":d,"already_exists":True}
    with q.jobs._tx("customer") as c:
        persisted=json.dumps([list(r) for table in ("connector_exception_queues","connector_exception_items")
            for r in q.q(c,"SELECT definition FROM "+table+" WHERE organization_id=?","customer").fetchall()])
    for event in [*sa.events,*sb.events]:
        assert event.activity not in persisted and event.case_id not in persisted and event.event_id not in persisted
    assert "rules" not in persisted and "members" not in persisted
    assert not q.inventory("customer")["raw_events_stored"]


def test_disposition_lifecycle_and_revision_fencing(prepared):
    q,*_=prepared;d=create(prepared);item=items(q,d)[0]
    result=update(q,d,item,"investigating")
    d2=result["queue"];i2=result["items"][0]
    assert d2["revision"]==2 and d2["states"]["investigating"]==1
    assert i2["history"][0]["previous"]["state"]=="open"
    assert i2["status"]==item["status"] and i2["reason"]==item["reason"]
    with pytest.raises(JobError,match="exception_queue_revision_changed"):
        update(q,d,item,"deferred","timestamp_uncertainty")
    with pytest.raises(JobError,match="exception_item_revision_changed"):
        update(q,d2,item,"deferred","timestamp_uncertainty")
    with pytest.raises(JobError,match="exception_queue_revision_changed"):
        q.page("customer",d["queue_id"],Page(expected_revision=d["revision"]))
    reviewed=update(q,d2,i2,"reviewed","source_gap_suspected")
    assert reviewed["queue"]["states"]["reviewed"]==1
    assert reviewed["items"][0]["disposition"]=="source_gap_suspected"
    reopened=update(q,reviewed["queue"],reviewed["items"][0],"open")
    assert reopened["queue"]["states"]["open"]==1 and reopened["items"][0]["disposition"] is None
    assert len(reopened["items"][0]["history"])==3
    assert q.page("customer",d["queue_id"],Page(state="reviewed"))["total"]==0


def test_bad_batch_is_atomic_and_requires_consent(prepared):
    q,*_=prepared;d=create(prepared);item=items(q,d)[0]
    with pytest.raises(JobError,match="explicit_exception_disposition"):
        update(q,d,item,"investigating",confirm_disposition=False)
    edit={"item_id":item["item_id"],"expected_revision":1,"state":"investigating"}
    with pytest.raises(JobError,match="unknown_exception_item"):
        q.change("customer",d["queue_id"],Change(expected_queue_revision=1,
            edits=[edit,{**edit,"item_id":"0"*64}],confirm_disposition=True),"owner")
    assert items(q,d)==[item]
    with pytest.raises(ValidationError):
        Change(expected_queue_revision=1,edits=[edit,edit],confirm_disposition=True)
    with pytest.raises(ValidationError):
        Change(expected_queue_revision=1,edits=[{**edit,"state":"reviewed"}],confirm_disposition=True)
    with pytest.raises(ValidationError):
        Page(offset=1)


def test_same_revision_concurrent_edits_have_one_winner(prepared):
    q,*_=prepared;d=create(prepared);item=items(q,d)[0]
    gate=Barrier(2)
    def attempt(state):
        gate.wait(timeout=10)
        try:
            update(q,d,item,state,"process_followup_needed" if state=="deferred" else None)
            return "success"
        except JobError as exc:
            assert "exception_queue_revision_changed" in str(exc)
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,["investigating","deferred"]))==["conflict","success"]
    assert q.inventory("customer")["queues"][0]["revision"]==2


def test_retention_and_history_bound(prepared):
    q,*_=prepared;d=create(prepared);item=items(q,d)[0]
    for i in range(MAX_HISTORY+3):
        changed=update(q,d,item,"investigating" if i%2==0 else "open")
        d,item=changed["queue"],changed["items"][0]
    assert len(item["history"])==MAX_HISTORY
    assert item["history"][0]["revision"]==item["revision"]
    assert item["history"][-1]["revision"]==item["revision"]-MAX_HISTORY+1


def test_no_queue_for_passing_or_changed_unreviewed_report(prepared):
    q,spec,*_=prepared
    with pytest.raises(JobError,match="explicit_exception_retention"):
        q.save("customer",Save.model_validate({**spec,"confirm_exception_retention":False}),"owner")
    with pytest.raises(JobError,match="conformance_review_changed"):
        q.save("customer",Save.model_validate({**spec,"expected_conformance_sha256":"e"*64}),"owner")
    passing={**spec,"rules":[r for r in spec["rules"] if r["kind"]!="forbidden"]}
    req={k:v for k,v in passing.items() if k not in ("expected_conformance_sha256","confirm_exception_retention")}
    report=review(q.reviewed,"customer",Review.model_validate(req),"owner")
    with pytest.raises(JobError,match="no_exceptions"):
        q.save("customer",Save.model_validate({**passing,"expected_conformance_sha256":report["conformance_sha256"]}),"owner")
    assert q.inventory("customer")["queues"]==[]


def test_revocation_blocks_all_access_and_purges_items(prepared):
    q,spec,ga,*_=prepared;d=create(prepared);item=items(q,d)[0]
    q.reviewed.publications.remove("customer",ga["job_id"],Remove(expected_publication_revision=ga["revision"],
        confirm_delete=True),"owner",ga["grant_id"])
    with pytest.raises(JobError,match="exception_queue_missing_or_invalidated"):
        q.page("customer",d["queue_id"],Page())
    with pytest.raises(JobError,match="exception_queue_missing_or_invalidated"):
        update(q,d,item,"investigating")
    q.purge_expired()
    with q.jobs._tx("customer") as c:
        for table in ("connector_exception_queues","connector_exception_pins","connector_exception_items"):
            assert q.q(c,"SELECT count(*) FROM "+table+" WHERE organization_id=?","customer").fetchone()[0]==0


def test_tenant_isolation_expiry_and_explicit_deletion(prepared):
    q,*_=prepared;d=create(prepared)
    assert q.inventory("another")["queues"]==[]
    with pytest.raises(JobError,match="exception_queue_missing_or_invalidated"):
        q.page("another",d["queue_id"],Page())
    with pytest.raises(JobError,match="explicit_exception_queue_deletion"):
        q.delete("customer",d["queue_id"],Delete(expected_revision=1,confirm_delete=False),"owner")
    result=q.delete("customer",d["queue_id"],Delete(expected_revision=1,confirm_delete=True),"owner")
    assert result=={"deleted":True,"publications_deleted":False,"source_data_deleted":False}
    assert len(q.reviewed.publications.inventory("customer")["publications"])==2
    d=create(prepared)
    q.jobs.clock=lambda:d["expires_at"]+1
    with pytest.raises(JobError,match="exception_queue_missing_or_invalidated"):
        q.page("customer",d["queue_id"],Page())
    q.purge_expired()
    assert q.inventory("customer")["queues"]==[]


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
        base="/v1/admin/connector-events/customer/exception-queues"
        assert c.get("/admin/connector-exception-triage").status_code==200
        assert c.get(base).status_code==401
        admin={"Authorization":"Bearer test-admin"}
        assert c.get(base,headers=admin).json()["queues"]==[]
        for suffix in ("","/cet_fixture/page","/cet_fixture/change","/cet_fixture/delete","/cet_fixture/export"):
            assert c.post(base+suffix,json={},headers=admin).status_code in (404,422)
            assert c.post(base+suffix,json={}).status_code==401
        ordinary=admin_accounts.AdminIdentity("admin-fixture","admin@example.test","Admin","admin","test")
        monkeypatch.setattr(admin_accounts,"validate_session",lambda db,token:ordinary)
        header={"Authorization":"Bearer "+admin_accounts.SESSION_PREFIX+"test"}
        assert c.get(base,headers=header).status_code==403
        for suffix in ("","/cet_fixture/page","/cet_fixture/change","/cet_fixture/delete","/cet_fixture/export"):
            assert c.post(base+suffix,json={},headers=header).status_code==403


def test_full_disposition_export_is_exact_and_preserves_original_outcome(prepared):
    from gateway.connector_exception_triage import Export,disposition_csv
    from shared.connector_event_snapshot import digest
    from io import StringIO
    import csv
    q,*_=prepared;d=create(prepared);item=items(q,d)[0]
    updated=update(q,d,item,"reviewed","source_gap_suspected")
    new=updated["queue"]
    with pytest.raises(JobError,match="exception_queue_revision_changed"):
        q.export("customer",d["queue_id"],Export(expected_revision=1,confirm_disposition_export=True),"owner")
    with pytest.raises(JobError,match="explicit_disposition_export"):
        q.export("customer",d["queue_id"],Export(expected_revision=2,confirm_disposition_export=False),"owner")
    report=q.export("customer",d["queue_id"],Export(expected_revision=2,confirm_disposition_export=True),"owner")
    assert report["queue"]==new
    assert report["export_sha256"]==digest({k:v for k,v in report.items() if k!="export_sha256"})
    rows=list(csv.DictReader(StringIO(disposition_csv(report))))
    assert len(rows)==1 and rows[0]["state"]=="reviewed"
    assert rows[0]["status"]==item["status"] and rows[0]["reason"]==item["reason"]
    assert rows[0]["disposition"]=="source_gap_suspected" and rows[0]["queue_revision"]=="2"
    assert rows[0]["export_sha256"]==report["export_sha256"]
    assert "history" not in rows[0]
