"""Guided reassessment uses the same canonical comparison as manual review."""
from copy import deepcopy
import pytest
from gateway.connector_guided_reassessment import Guided,Discover,Build
from gateway.connector_jobs import JobError
from test_connector_followup_reassessment import comparison,compare,record
from test_connector_exception_triage import prepared
from test_connector_linked_conformance import linked
from test_connector_event_publications import deployed
from test_gateway_connector_jobs import db


def inputs(comparison):
    f,d,p,i,value,later=comparison
    guided=Guided(f.queues)
    subsequent={k:v for k,v in value["subsequent"].items()
                if k not in ("expected_review_sha256","rules","confirm_conformance_review")}
    spec={"expected_queue_revision":d["revision"],"expected_packet_revision":p["revision"],
          "baseline":value["baseline"],"subsequent":subsequent,
          "expected_subsequent_review_sha256":value["subsequent"]["expected_review_sha256"],
          "confirm_catalog_inspection":True}
    return guided,d,p,value,spec


def discover(g,d,p,spec):
    return g.discover("customer",d["queue_id"],p["packet_id"],Discover.model_validate(spec),"owner")


def mapped(value,spec,catalog):
    original={r["rule_id"]:r for r in value["baseline"]["rules"]}
    selectors={}
    for rule in value["subsequent"]["rules"]:
        for side in ("left","right"):
            a,b=original[rule["rule_id"]].get(side),rule.get(side)
            if a:
                selectors[a["job_id"],a["activity"]]={"before_job":a["job_id"],"before_activity":a["activity"],
                    "after_job":b["job_id"],"after_activity":b["activity"]}
    return {**spec,"expected_catalog_sha256":catalog["catalog_sha256"],"jobs":value["jobs"],
            "cases":value["cases"],"selectors":list(selectors.values()),
            "confirm_case_and_selector_correspondence":True}


def build(g,d,p,value):
    return g.build("customer",d["queue_id"],p["packet_id"],Build.model_validate(value),"owner")


def test_guided_matches_canonical_manual_comparison(comparison):
    g,d,p,value,spec=inputs(comparison)
    catalog=discover(g,d,p,spec)
    assert not catalog["automatic_correspondence"]
    assert not catalog["identity_independently_verified"]
    assert len(catalog["original_groups"])==1
    assert len(catalog["required_rules"])==1
    assert catalog==discover(g,d,p,spec)
    for m in value["jobs"]:
        assert m["after"] in catalog["source_candidates"][m["before"]]
    result=build(g,d,p,mapped(value,spec,catalog))
    assert result["report"]==compare(g,d,p,value)
    assert result["report"]["summary"]["satisfied_in_later_sample"]==1
    assert not result["source_remediation_verified"]
    assert g.inventory("customer",d["queue_id"])["queue"]["revision"]==d["revision"]
    saved=record(g,d,p,result["request"],result["report"])
    assert saved["receipt"]["reassessment_sha256"]==result["report"]["reassessment_sha256"]


def test_catalog_requires_approval_and_exact_review(comparison):
    g,d,p,value,spec=inputs(comparison)
    for changes,code in (
        ({"confirm_catalog_inspection":False},"explicit_reassessment_catalog_inspection_required"),
        ({"expected_subsequent_review_sha256":"0"*64},"reassessment_subsequent_review_changed"),
    ):
        with pytest.raises(JobError,match=code):
            discover(g,d,p,{**spec,**changes})


def test_build_rejects_stale_catalog_and_wrong_source_mapping(comparison):
    g,d,p,value,spec=inputs(comparison)
    catalog=discover(g,d,p,spec)
    request=mapped(value,spec,catalog)
    with pytest.raises(JobError,match="reassessment_catalog_changed"):
        build(g,d,p,{**request,"expected_catalog_sha256":"0"*64})
    changed=deepcopy(request)
    changed["jobs"][0]["after"],changed["jobs"][1]["after"]=changed["jobs"][1]["after"],changed["jobs"][0]["after"]
    with pytest.raises(JobError,match="reassessment_source_mapping_outside_catalog"):
        build(g,d,p,changed)
    with pytest.raises(JobError,match="explicit_reassessment_correspondence_required"):
        build(g,d,p,{**request,"confirm_case_and_selector_correspondence":False})


def test_catalog_does_not_authorize_early_windows(comparison):
    g,d,p,value,spec=inputs(comparison)
    g.jobs.clock=lambda:p["history"][0]["at"]+40
    with pytest.raises(JobError,match="reassessment_requires_completed_post_followup_window"):
        discover(g,d,p,spec)
