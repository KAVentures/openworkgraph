"""Follow-up planning over actual grant-bound queues; no source writes."""
import json
import pytest
from gateway.connector_exception_followup import Change,Export,change,export,report
from gateway.connector_exception_triage import Save,Page
from gateway.connector_event_publications import Remove
from gateway.connector_jobs import JobError
from test_connector_exception_triage import prepared,create,items,update
from test_connector_linked_conformance import linked
from test_connector_event_publications import deployed
from test_gateway_connector_jobs import db


def reviewed(prepared):
    q,*_=prepared
    d=create(prepared)
    item=items(q,d)[0]
    r=update(q,d,item,"reviewed","source_gap_suspected")
    return q,r["queue"],r["items"][0]


def operation(q,d,item,op,**kw):
    edit={"item_id":item["item_id"],"expected_revision":item["revision"],"operation":op}
    if op=="plan":edit["due_hours"]=1
    edit.update(kw)
    return change(q,"customer",d["queue_id"],Change(expected_queue_revision=d["revision"],
                  edits=[edit],confirm_followup=True),"owner")


def test_followup_lifecycle_and_evidence_independence(prepared):
    q,d,item=reviewed(prepared)
    assert report(q,"customer",d["queue_id"])["counts"]["suggested_unplanned"]==1
    assert "items" not in report(q,"customer",d["queue_id"])
    r=operation(q,d,item,"plan")
    d2,i2=r["queue"],r["items"][0]
    assert i2["followup"]["action"]=="inspect_source_coverage"
    assert i2["followup"]["state"]=="planned"
    assert not r["source_actions_executed"]
    assert i2["status"]==item["status"] and i2["disposition"]==item["disposition"]
    with pytest.raises(JobError,match="exception_queue_revision_changed"):
        operation(q,d,item,"plan")
    with pytest.raises(JobError,match="followup_already_active"):
        operation(q,d2,i2,"plan")
    r=operation(q,d2,i2,"in_progress")
    r=operation(q,r["queue"],r["items"][0],"reported_done")
    assert r["items"][0]["followup"]["state"]=="reported_done"
    assert not r["remediation_independently_verified"]
    assert r["items"][0]["status"]=="not_satisfied"
    assert r["queue"]["states"]["reviewed"]==1
    out=export(q,"customer",d["queue_id"],Export(expected_revision=r["queue"]["revision"],
               confirm_followup_export=True),"owner")
    assert out["counts"]["reported_done"]==1
    assert not out["actual_process_compliance_verified"]
    assert "linked_case_id" not in out["items"][0]
    assert "export_sha256" in out


def test_disposition_change_invalidates_old_plan_even_if_restored(prepared):
    q,d,item=reviewed(prepared)
    r=operation(q,d,item,"plan")
    r=update(q,r["queue"],r["items"][0],"open")
    assert r["items"][0]["followup"]["stale"]
    r=update(q,r["queue"],r["items"][0],"reviewed","source_gap_suspected")
    assert r["items"][0]["followup"]["stale"]
    with pytest.raises(JobError,match="followup_requires_fresh_plan"):
        operation(q,r["queue"],r["items"][0],"reported_done")
    r=operation(q,r["queue"],r["items"][0],"plan")
    assert not r["items"][0]["followup"]["stale"]
    assert len(r["items"][0]["followup"]["history"])==1


def test_atomic_batch_consent_and_expiry_fences(prepared):
    q,d,item=reviewed(prepared)
    edit={"item_id":item["item_id"],"expected_revision":item["revision"],"operation":"plan","due_hours":1}
    with pytest.raises(JobError,match="explicit_followup"):
        change(q,"customer",d["queue_id"],Change(expected_queue_revision=d["revision"],
               edits=[edit],confirm_followup=False),"owner")
    with pytest.raises(JobError,match="unknown_exception_item"):
        change(q,"customer",d["queue_id"],Change(expected_queue_revision=d["revision"],
               edits=[edit,{**edit,"item_id":"0"*64}],confirm_followup=True),"owner")
    assert items(q,d)==[item]
    with pytest.raises(JobError,match="followup_due_after_publication_expiry"):
        operation(q,d,item,"plan",due_hours=168)
    with pytest.raises(JobError,match="explicit_followup_export"):
        export(q,"customer",d["queue_id"],Export(expected_revision=d["revision"],
               confirm_followup_export=False),"owner")


def test_followup_requires_actionable_disposition_and_revocation(prepared):
    q,spec,ga,*_=prepared
    d=create(prepared);item=items(q,d)[0]
    with pytest.raises(JobError,match="followup_requires_actionable_disposition"):
        operation(q,d,item,"plan")
    r=update(q,d,item,"reviewed","expected_sample_variation")
    with pytest.raises(JobError,match="followup_requires_actionable_disposition"):
        operation(q,r["queue"],r["items"][0],"plan")
    r=update(q,r["queue"],r["items"][0],"reviewed","process_followup_needed")
    r=operation(q,r["queue"],r["items"][0],"plan")
    q.reviewed.publications.remove("customer",ga["job_id"],Remove(
        expected_publication_revision=ga["revision"],confirm_delete=True),"owner",ga["grant_id"])
    with pytest.raises(JobError,match="exception_queue_missing_or_invalidated"):
        report(q,"customer",d["queue_id"])
    q.purge_expired()
    assert q.inventory("customer")["queues"]==[]


def test_overdue_is_observation_not_automatic_action(prepared):
    q,d,item=reviewed(prepared)
    r=operation(q,d,item,"plan")
    due=r["items"][0]["followup"]["due_at"]
    q.jobs.clock=lambda:due+1
    summary=report(q,"customer",d["queue_id"])
    assert summary["counts"]["overdue"]==1
    assert summary["counts"]["planned"]==1
    assert not summary["source_actions_executed"]
