"""Mapping workbench: real retained publication fixtures and bounded guidance."""
from copy import deepcopy
import json

import pytest

from gateway.connector_followup_mapping import MappingWorkbench,WorkbenchRequest,compatible_sources,group_shape
from gateway.connector_followup_reassessment import Preview
from gateway.connector_event_publications import Remove
from gateway.connector_jobs import JobError
from test_connector_followup_reassessment import comparison
from test_connector_exception_triage import prepared
from test_connector_linked_conformance import linked
from test_connector_event_publications import deployed
from test_gateway_connector_jobs import db


def input_for(value):
    return {"expected_queue_revision":value["expected_queue_revision"],
            "expected_packet_revision":value["expected_packet_revision"],
            "baseline":deepcopy(value["baseline"]),
            "subsequent":{k:deepcopy(v) for k,v in value["subsequent"].items()
                          if k not in ("rules","confirm_conformance_review","filter_status","offset",
                                       "limit","expected_conformance_sha256")},
            "confirm_mapping_inspection":True}


def guidance(f,d,p,value):
    return MappingWorkbench(f.queues).guidance("customer",d["queue_id"],p["packet_id"],
                        WorkbenchRequest.model_validate(value),"owner")


def test_guidance_exact_live_scope_no_automatic_mapping_or_persistence(comparison):
    f,d,p,i,value,later=comparison
    original=f.inventory("customer",d["queue_id"])
    request=input_for(value)
    out=guidance(f,d,p,request)
    assert out==guidance(f,d,p,request)
    assert out["packet_id"]==p["packet_id"]
    assert out["baseline_conformance_sha256"]==d["conformance_sha256"]
    assert out["subsequent_review_sha256"]==value["subsequent"]["expected_review_sha256"]
    assert out["baseline_cases"][0]["linked_case_id"]==i["linked_case_id"]
    assert len(out["baseline_cases"])==1
    assert {r["rule_id"] for r in out["rules"]}=={i["rule_id"]}
    mapped={r["before"]:r["after"] for r in value["jobs"]}
    for source in out["source_candidates"]:
        assert source["candidates"]==[mapped[source["before"]]]
    assert len(out["subsequent_cases"])==1
    assert out["subsequent_cases"][0]["linked_case_id"]==value["cases"][0]["after"]
    assert not out["identity_inferred"] and not out["execution_authorized"]
    serialized=json.dumps(out)
    for grant,snap in later:
        for event in snap.events:
            assert event.event_id not in serialized
            assert event.case_id not in serialized
        cat=next(x for x in out["activity_catalogs"] if x["job_id"]==grant["job_id"])
        assert {x["activity"] for x in cat["activities"]}=={e.activity for e in snap.events}
    assert f.inventory("customer",d["queue_id"])==original
    # Guidance is not a new authorization route; the existing reviewed
    # comparison validates the eventual explicit choices independently.
    assert f.compare("customer",d["queue_id"],p["packet_id"],Preview.model_validate(value),"owner")["summary"]["satisfied_in_later_sample"]==1


def test_guidance_rejects_stale_and_unapproved_inputs(comparison):
    f,d,p,i,value,_=comparison
    for mutate,code in (
        (lambda v:v.update(confirm_mapping_inspection=False),"explicit_mapping_inspection_required"),
        (lambda v:v.update(expected_queue_revision=d["revision"]+1),"exception_queue_revision_changed"),
        (lambda v:v.update(expected_packet_revision=p["revision"]+1),"followup_packet_revision_changed"),
        (lambda v:v["baseline"].update(expected_conformance_sha256="0"*64),"reassessment_baseline_conformance_changed"),
        (lambda v:v["subsequent"].update(expected_review_sha256="0"*64),"cross_source_review_changed"),
    ):
        changed=input_for(value);mutate(changed)
        with pytest.raises(JobError,match=code):
            guidance(f,d,p,changed)


def test_later_clock_and_revocation_remove_eligibility(comparison):
    f,d,p,i,value,later=comparison
    old_clock=f.jobs.clock
    f.jobs.clock=lambda:p["history"][0]["at"]+40
    out=guidance(f,d,p,input_for(value))
    assert all(not x["candidates"] for x in out["source_candidates"])
    f.jobs.clock=old_clock
    g,_=later[0]
    f.queues.reviewed.publications.remove("customer",g["job_id"],
        Remove(expected_publication_revision=g["revision"],confirm_delete=True),"owner",g["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        guidance(f,d,p,input_for(value))


def test_source_shape_counts_not_just_distinct_sources():
    g={"members":[{"job_id":"one"},{"job_id":"two"},{"job_id":"one"}]}
    assert group_shape(g)=={"one":2,"two":1}


def test_source_candidate_window_and_mode_boundaries():
    a={"job_id":"a","provider":"rest","activity_mode":"coded",
       "since":"2026-01-01T00:00:00Z","until":"2026-01-01T01:00:00Z"}
    b={**a,"job_id":"b","since":"2026-01-01T02:00:00Z","until":"2026-01-01T03:00:00Z"}
    from gateway.connector_followup_reassessment import stamp
    completed=stamp(b["since"]);now=stamp(b["until"])
    assert compatible_sources(a,b,completed,now)
    assert not compatible_sources(a,b,completed+1,now)
    assert not compatible_sources(a,b,completed,now-1)
    assert not compatible_sources(a,{**b,"job_id":"a"},completed,now)
    assert not compatible_sources(a,{**b,"provider":"sql"},completed,now)
    assert not compatible_sources(a,{**b,"activity_mode":"labels"},completed,now)
