"""Actual Gateway publications, human follow-ups and later sample reassessment."""
from datetime import datetime,timezone
from copy import deepcopy
import json

import pytest

from shared.connector_event_snapshot import Snapshot,digest,utc
from gateway.connector_case_review import Plan
from gateway.connector_followup_reassessment import Reassessments,Preview,Apply
from gateway.connector_event_publications import Remove
from gateway.connector_jobs import JobError
from test_connector_exception_followups import ready,request,status
from test_connector_exception_triage import prepared,items
from test_connector_linked_conformance import linked
from test_connector_event_publications import deployed,source,grant,upload,pin
from test_gateway_connector_jobs import db


def time_string(t):
    return utc(datetime.fromtimestamp(t,timezone.utc).isoformat())


@pytest.fixture
def comparison(prepared,deployed):
    f,d,i=ready(prepared)
    r=request(f,d,i);r=status(f,r["queue"],r["packet"],"in_progress")
    r=status(f,r["queue"],r["packet"],"completed_unverified")
    d,p=r["queue"],r["packet"]
    completed=p["history"][0]["at"]
    jobs,store=deployed
    jobs.clock=lambda:completed+120
    later=[]
    for labels in (True,False):
        job,token,snap,_=source(deployed,labels=labels)
        rows=[{**event.model_dump(),"observed_at":time_string(completed+30+n)}
              for n,event in enumerate(snap.events)]
        definition={**snap.manifest.model_dump(),"since":time_string(completed+20),
                    "until":time_string(completed+90),"checkpoint":time_string(completed+90),
                    "rows_sha256":digest(rows)}
        revised=Snapshot.model_validate({"manifest":definition,"events":rows})
        g=grant(store,job,revised,confirm_activity_labels=labels)
        upload(store,token,g,revised)
        later.append((g,revised))
    _,base,ga,gb,sa,sb=prepared
    baseline={k:v for k,v in base.items() if k!="confirm_exception_retention"}
    # Only the packet's forbidden rule is assessed. A separately asserted
    # later code is absent; the absence is a sample outcome, not proof.
    new_a,new_b=[g for g,_ in later]
    old=next(x for x in baseline["rules"] if x["rule_id"]==i["rule_id"])
    after_rule={**old,"left":{"job_id":new_b["job_id"],"activity":"A_"+"f"*64}}
    spec={"snapshots":[pin(g) for g,_ in later],"confirm_inspection":True,"groups":[
        {"members":[{"job_id":g["job_id"],"case_id":s.events[0].case_id} for g,s in later],
         "basis":"reviewed_business_record"}]}
    reviewed=f.queues.reviewed
    report=reviewed.preview("customer",Plan.model_validate(spec),"owner")
    subsequent={**spec,"expected_review_sha256":report["review_sha256"],
                "rules":[after_rule],"confirm_conformance_review":True}
    value={"expected_queue_revision":d["revision"],"expected_packet_revision":p["revision"],
           "baseline":baseline,"subsequent":subsequent,
           "jobs":[{"before":a["job_id"],"after":b["job_id"]} for a,b in ((ga,new_a),(gb,new_b))],
           "cases":[{"before":i["linked_case_id"],"after":report["groups"][0]["linked_case_id"]}],
           "confirm_case_and_selector_correspondence":True}
    return Reassessments(f.queues),d,p,i,value,later


def compare(f,d,p,value):
    return f.compare("customer",d["queue_id"],p["packet_id"],Preview.model_validate(value),"owner")


def record(f,d,p,value,report,**kw):
    return f.record("customer",d["queue_id"],p["packet_id"],Apply.model_validate({
        **value,"expected_reassessment_sha256":report["reassessment_sha256"],
        "confirm_record_reassessment":True,"confirm_sample_only":True,**kw}),"owner")


def test_later_sample_and_digest_only_receipt(comparison):
    f,d,p,i,value,later=comparison
    report=compare(f,d,p,value)
    assert report["summary"]["satisfied_in_later_sample"]==1
    assert report["rows"][0]["baseline_status"]=="not_satisfied"
    assert report["rows"][0]["subsequent_status"]=="satisfied"
    assert not report["source_remediation_verified"]
    assert not report["case_correspondence_independently_verified"]
    assert not report["causal_effect_verified"]
    assert report==compare(f,d,p,value)
    saved=record(f,d,p,value,report)
    assert saved["report"]==report
    assert saved["packet"]["state"]=="completed_unverified"
    assert saved["queue"]["revision"]==d["revision"]+1
    assert saved["packet"]["revision"]==p["revision"]+1
    assert items(f.queues,saved["queue"])==[i]
    assert set(saved["receipt"])=={"reassessment_sha256","comparison_content_sha256","recorded_at","source_remediation_verified"}
    with f.jobs._tx("customer") as c:
        state=f.queues._require(c,"customer",d["queue_id"])
        serialized=json.dumps(state)
        for g,s in later:
            assert g["job_id"] not in serialized and g["grant_id"] not in serialized
            assert s.events[0].activity not in serialized
    with pytest.raises(JobError,match="exception_queue_revision_changed"):
        record(f,d,p,value,report)
    fresh={**value,"expected_queue_revision":saved["queue"]["revision"],
           "expected_packet_revision":saved["packet"]["revision"]}
    repeated=compare(f,saved["queue"],saved["packet"],fresh)
    assert repeated["comparison_content_sha256"]==report["comparison_content_sha256"]
    with pytest.raises(JobError,match="reassessment_already_recorded"):
        record(f,saved["queue"],saved["packet"],fresh,repeated)


def test_wrong_rule_mapping_and_consents_refused(comparison):
    f,d,p,i,value,_=comparison
    for mutate,code in (
        (lambda v:v["subsequent"]["rules"][0].update(kind="required"),"reassessment_rule_semantics_changed"),
        (lambda v:v["subsequent"]["rules"][0]["left"].update(job_id=v["jobs"][0]["after"]),"reassessment_selector_source_changed"),
        (lambda v:v["cases"][0].update(before="0"*64),"reassessment_case_population_changed"),
        (lambda v:v["cases"][0].update(after="0"*64),"reassessment_later_case_or_rule_missing"),
        (lambda v:v.update(confirm_case_and_selector_correspondence=False),"explicit_reassessment_correspondence_required"),
    ):
        changed=deepcopy(value);mutate(changed)
        with pytest.raises(JobError,match=code):
            compare(f,d,p,changed)
    report=compare(f,d,p,value)
    with pytest.raises(JobError,match="reassessment_review_changed"):
        record(f,d,p,value,report,expected_reassessment_sha256="0"*64)
    with pytest.raises(JobError,match="explicit_reassessment_record_approval_required"):
        record(f,d,p,value,report,confirm_sample_only=False)


def test_post_completion_window_and_grant_revocation(comparison):
    f,d,p,i,value,later=comparison
    # A clock that has not reached the end of the later window cannot
    # establish a completed post-follow-up request.
    original=f.jobs.clock
    f.jobs.clock=lambda:p["history"][0]["at"]+40
    with pytest.raises(JobError,match="reassessment_requires_completed_post_followup_window"):
        compare(f,d,p,value)
    f.jobs.clock=original
    report=compare(f,d,p,value)
    g,_=later[0]
    f.queues.reviewed.publications.remove("customer",g["job_id"],
        Remove(expected_publication_revision=g["revision"],confirm_delete=True),"owner",g["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        record(f,d,p,value,report)
    assert not f.inventory("customer",d["queue_id"])["packets"][0].get("reassessment_receipts")
