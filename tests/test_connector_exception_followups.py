"""Follow-up work requests remain separate from immutable evidence outcomes."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from gateway.connector_exception_followups import Followups,Preview,Create,Change,Export
from gateway.connector_jobs import JobError
from gateway.connector_exception_triage import Page,Delete
from gateway.connector_linked_conformance import Review,review
from test_connector_exception_triage import prepared,create,items,update
from test_connector_linked_conformance import linked
from test_connector_event_publications import deployed
from test_gateway_connector_jobs import db


def ready(prepared,reason="source_gap_suspected"):
    q,*_=prepared
    d=create(prepared)
    item=items(q,d)[0]
    change=update(q,d,item,"reviewed",reason)
    return Followups(q),change["queue"],change["items"][0]


def selection(d,item):
    return {"expected_queue_revision":d["revision"],"items":[
        {"item_id":item["item_id"],"expected_revision":item["revision"]}]}


def approved(f,d,i,**kw):
    spec=selection(d,i)
    plan=f.preview("customer",d["queue_id"],Preview.model_validate(spec))
    return Create.model_validate({**spec,"expected_plan_sha256":plan["plan_sha256"],
        "confirm_followup_request":True,"confirm_no_execution_authority":True,**kw})


def request(f,d,i):
    return f.create("customer",d["queue_id"],approved(f,d,i),"owner")


def status(f,d,p,state,**kw):
    return f.change("customer",d["queue_id"],p["packet_id"],Change.model_validate({
        "expected_queue_revision":d["revision"],"expected_packet_revision":p["revision"],
        "state":state,"confirm_human_status":True,**kw}),"owner")


def export(f,d,p,**kw):
    return f.export("customer",d["queue_id"],p["packet_id"],Export.model_validate({
        "expected_queue_revision":d["revision"],"expected_packet_revision":p["revision"],
        "confirm_export":True,**kw}),"owner")


def test_exact_followup_lifecycle_and_unchanged_evidence(prepared):
    f,d,i=ready(prepared)
    q=f.queues
    plan=f.preview("customer",d["queue_id"],Preview.model_validate(selection(d,i)))
    assert plan["items"][0]["requested_action"]=="inspect_source_coverage"
    assert not plan["execution_authorized"]
    result=request(f,d,i)
    d,p=result["queue"],result["packet"]
    assert d["states"]["reviewed"]==1 and p["state"]=="requested"
    assert items(q,d)==[i]
    assert p["item_pins_current"] and not p["source_remediation_verified"]
    assert f.inventory("customer",d["queue_id"])["packets"]==[p]
    with pytest.raises(JobError,match="invalid_followup_transition"):
        status(f,d,p,"completed_unverified")
    result=status(f,d,p,"in_progress")
    d,p=result["queue"],result["packet"]
    result=status(f,d,p,"completed_unverified")
    d,p=result["queue"],result["packet"]
    assert p["state"]=="completed_unverified" and len(p["history"])==2
    assert items(q,d)==[i]
    out=export(f,d,p)
    assert not out["external_execution_authorized"] and not out["source_remediation_verified"]
    assert len(out["export_sha256"])==64
    for snap in prepared[-2:]:
        for e in snap.events:
            assert e.event_id not in json.dumps(out) and e.activity not in json.dumps(out)


def test_preview_is_read_only_and_tamper_or_missing_approval_refused(prepared):
    f,d,i=ready(prepared)
    plan=f.preview("customer",d["queue_id"],Preview.model_validate(selection(d,i)))
    assert f.inventory("customer",d["queue_id"])["queue"]==d
    assert not f.inventory("customer",d["queue_id"])["packets"]
    for change in ({"confirm_followup_request":False},{"confirm_no_execution_authority":False},
                   {"expected_plan_sha256":"0"*64}):
        with pytest.raises(JobError):
            f.create("customer",d["queue_id"],approved(f,d,i,**change),"owner")
    assert not f.inventory("customer",d["queue_id"])["packets"]
    assert plan["plan_sha256"]==approved(f,d,i).expected_plan_sha256


def test_disposition_change_invalidates_packet_but_permits_cancellation(prepared):
    f,d,i=ready(prepared)
    result=request(f,d,i);d,p=result["queue"],result["packet"]
    changed=update(f.queues,d,i,"deferred","timestamp_uncertainty")
    d,i=changed["queue"],changed["items"][0]
    assert not f.inventory("customer",d["queue_id"])["packets"][0]["item_pins_current"]
    with pytest.raises(JobError,match="followup_item_pins_changed"):
        status(f,d,p,"in_progress")
    with pytest.raises(JobError,match="followup_item_pins_changed"):
        export(f,d,p)
    result=status(f,d,p,"cancelled")
    new=request(f,result["queue"],i)
    assert new["packet"]["items"][0]["requested_action"]=="review_time_assumptions"


def test_duplicate_active_request_and_concurrent_revision_fencing(prepared):
    f,d,i=ready(prepared)
    spec=approved(f,d,i)
    gate=Barrier(2)
    def attempt(_):
        gate.wait(timeout=10)
        try:
            f.create("customer",d["queue_id"],spec,"owner")
            return "created"
        except JobError as exc:
            assert "exception_queue_revision_changed" in str(exc)
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==["created","stale"]
    live=f.inventory("customer",d["queue_id"])
    with pytest.raises(JobError,match="followup_items_already_requested"):
        f.preview("customer",d["queue_id"],Preview.model_validate(selection(live["queue"],i)))


def test_open_and_nonactionable_items_refused(prepared):
    q,*_=prepared
    d=create(prepared);i=items(q,d)[0];f=Followups(q)
    with pytest.raises(JobError,match="exception_not_actionable"):
        f.preview("customer",d["queue_id"],Preview.model_validate(selection(d,i)))
    r=update(q,d,i,"reviewed","expected_sample_variation")
    with pytest.raises(JobError,match="exception_not_actionable"):
        f.preview("customer",d["queue_id"],Preview.model_validate(selection(r["queue"],r["items"][0])))


def test_expiry_tenant_and_queue_deletion_scope(prepared):
    f,d,i=ready(prepared)
    result=request(f,d,i);d,p=result["queue"],result["packet"]
    with pytest.raises(JobError):
        f.inventory("another",d["queue_id"])
    f.queues.delete("customer",d["queue_id"],Delete(expected_revision=d["revision"],confirm_delete=True),"owner")
    with pytest.raises(JobError):
        f.inventory("customer",d["queue_id"])
    with pytest.raises(JobError):
        export(f,d,p)


def test_expired_packet_is_inaccessible(prepared):
    f,d,i=ready(prepared)
    result=request(f,d,i);d,p=result["queue"],result["packet"]
    f.jobs.clock=lambda:d["expires_at"]+1
    with pytest.raises(JobError):
        export(f,d,p)
    with pytest.raises(JobError):
        f.inventory("customer",d["queue_id"])
    f.queues.purge_expired()
    assert f.queues.inventory("customer")["queues"]==[]
