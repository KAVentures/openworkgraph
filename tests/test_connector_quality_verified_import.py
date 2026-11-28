"""Reproduce the existing quality report contract and verify exact local import."""
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest

from shared.connector_bundle_import import inspect,decode_value
from shared.connector_quality_verification import Policy,SourceDeclaration,calculate,verify_quality
from shared.connector_event_snapshot import digest,manifest_digest
from gateway.connector_portable_linkage import PortableExport,export_portable
from gateway.connector_jobs import JobError
from gateway.connector_case_review import Plan
from server import enterprise_connector_bundle_import as importer
from server.enterprise_rest_feed import FeedError
from server import event_log_workspace as ws
from test_connector_bundle_import import published,approved,later,raw
from test_connector_portable_linkage import portable,retain_args
from test_connector_case_review import linked
from test_connector_event_publications import deployed,bundle,pin
from test_gateway_connector_jobs import db


def make_report(value,policy=None,overrides=None,*,org="customer",pins=None):
    original=decode_value(value)
    default=Policy.model_validate(policy or {"min_events":1,"min_cases":1})
    overrides=overrides or {}
    by_job={p["job_id"]:p for p in (pins or [])}
    declarations=[]
    for src in original.manifests:
        p=by_job.get(src.job_id,{"job_id":src.job_id,"grant_id":src.grant_id,
              "manifest_sha256":src.manifest_sha256,"publication_revision":1,"expires_at":1900000000})
        override=overrides.get(src.job_id)
        declarations.append(SourceDeclaration.model_validate({**p,
            "policy_origin":"source_override" if override is not None else "default",
            "policy":Policy.model_validate(override) if override is not None else default}))
    return calculate(org,default,declarations,original)


def annotate(value,**kwargs):
    return {**value,"quality_review":make_report(value,**kwargs)}


def test_ordinary_quality_recomputed_and_canonical(published):
    value=annotate(published)
    data,report=inspect(raw(value))
    assert data.event_count==4 and report["quality_report_recomputed"]
    assert report["quality_review"]==value["quality_review"]
    assert report["quality_review"]["summary"]["source_local_cases"]==2
    assert report["quality_applies_to_original_source_sample"]
    assert report["quality_is_not_current_log_certification"]
    assert not report["gateway_origin_authenticated"] and not report["live_grants_verified"]
    shuffled=deepcopy(value);shuffled["events"].reverse();shuffled["manifests"].reverse()
    _,other=inspect(raw(shuffled))
    assert other["canonical_bundle_sha256"]==report["canonical_bundle_sha256"]
    assert other["file_sha256"]!=report["file_sha256"]
    _,plain=inspect(raw(published))
    assert report["canonical_bundle_sha256"]!=plain["canonical_bundle_sha256"]
    for row in published["events"]:
        assert row["event_id"] not in json.dumps(report)
        assert row["case_id"] not in json.dumps(report)
        assert row["activity"] not in json.dumps(report)


def redigest(report):
    report["quality_sha256"]=digest({k:v for k,v in report.items() if k!="quality_sha256"})


@pytest.mark.parametrize("mutate",[
    lambda q:q.update(all_selected_sources_pass=1),
    lambda q:q["summary"].update(events=99),
    lambda q:q["sources"][0]["metrics"].update(events=99),
    lambda q:q["sources"][0]["gates"][0].update(actual=99),
    lambda q:q["sources"][0].update(status="fail"),
    lambda q:q["sources"][0].update(publication_revision=True),
    lambda q:q["sources"][0].update(manifest_sha256="f"*64),
    lambda q:q["sources"][0].update(grant_id="cep_"+"f"*32),
    lambda q:q["sources"][0].update(policy_origin="unknown"),
    lambda q:q["sources"][0]["policy"].update(min_events=99),
    lambda q:q["sources"][0].update(extra="unexpected"),
    lambda q:q["sources"].append(deepcopy(q["sources"][0])),
    lambda q:q["sources"].pop(),
    lambda q:q.update(provider_completeness_verified=True),
    lambda q:q.update(physical_occurrence_times_verified=True),
])
def test_even_redigested_tampering_is_rejected(published,mutate):
    value=annotate(published)
    mutate(value["quality_review"])
    redigest(value["quality_review"])
    with pytest.raises(ValueError,match="quality_report_integrity_failure"):
        inspect(raw(value))


def test_malformed_and_nonpassing_reports(published):
    for bad in ({},None,[],True):
        with pytest.raises(ValueError,match="quality_report_integrity_failure"):
            inspect(raw({**published,"quality_review":bad}))
    value=annotate(published,policy={"min_events":3})
    assert not value["quality_review"]["all_selected_sources_pass"]
    with pytest.raises(ValueError,match="quality_gates_not_satisfied"):
        inspect(raw(value))
    q=value["quality_review"];q["all_selected_sources_pass"]=True;redigest(q)
    with pytest.raises(ValueError,match="quality_report_integrity_failure"):
        inspect(raw(value))


def test_source_override_replaces_policy_and_is_part_of_policy_pin(published):
    job=published["manifests"][0]["job_id"]
    a=annotate(published)
    b=annotate(published,overrides={job:{"min_cases":1}})
    _,pa=inspect(raw(a));_,pb=inspect(raw(b))
    assert pa["quality_policy_sha256"]!=pb["quality_policy_sha256"]
    source=next(s for s in pb["quality_review"]["sources"] if s["job_id"]==job)
    assert source["policy"]["min_events"] is None and source["policy_origin"]=="source_override"


def test_atomic_import_quality_consent_and_same_policy_append(published):
    value=annotate(published);data,args=approved(value,confirm_quality_review=True)
    before=ws.list_logs()
    with pytest.raises(FeedError,match="explicit_offline_quality_review"):
        importer.retain(data,**{**args,"confirm_quality_review":False})
    assert ws.list_logs()==before
    r=importer.retain(data,**args)
    assert r["receipt"]["quality_review"]==value["quality_review"]
    assert r["log"]["connector_bundle_import"]["quality_sha256"]==value["quality_review"]["quality_sha256"]
    target=r["log"]["log_id"]
    assert importer.retain(data,**args)["replayed_receipt"]
    updated=annotate(later(published))
    next_data,next_args=approved(updated,target=target,request="b"*32,confirm_quality_review=True)
    result=importer.retain(next_data,**next_args)
    assert result["log"]["events"]==5 and result["receipt"]["events_added"]==1
    assert result["receipt"]["quality_policy_sha256"]==r["receipt"]["quality_policy_sha256"]
    assert result["receipt"]["quality_sha256"]!=r["receipt"]["quality_sha256"]
    assert len(importer.history(target)["receipts"])==2
    for changed in (later(published),annotate(later(published),policy={"min_cases":1}),
                    annotate(later(published),org="another")):
        with pytest.raises(FeedError,match="bundle_quality_policy_changed"):
            importer.preview(raw(changed),target)
    ws.delete_log(target)
    assert not any(log["log_id"]==target for log in importer.inventory()["logs"])


def test_quality_cannot_be_added_to_ordinary_target_without_new_log(published):
    data,args=approved(published);r=importer.retain(data,**args)
    with pytest.raises(FeedError,match="bundle_quality_policy_changed"):
        importer.preview(raw(annotate(published)),r["log"]["log_id"])


def test_quality_portable_live_export_and_local_dual_consent(linked):
    reviewed,spec,_,_=linked
    package=portable(reviewed,spec)
    quality=make_report(package["source_bundle"],pins=package["pins"])
    gated=portable(reviewed,spec,quality_review_json=json.dumps(quality),confirm_quality_gated_export=True)
    assert gated["quality_review"]==quality
    projection,p=inspect(raw(gated))
    assert p["bundle_kind"]=="reviewed_linkage" and p["quality_report_recomputed"]
    assert p["retained_projection_case_count"]==1 and p["event_count"]==4
    assert p["quality_review"]["summary"]["source_local_cases"]==2
    data,args=retain_args(gated)
    args["confirm_quality_review"]=True
    for flag in ("confirm_quality_review","confirm_linkage_review"):
        with pytest.raises(FeedError):
            importer.retain(data,**{**args,flag:False})
    r=importer.retain(data,**args)
    assert r["log"]["events"]==4 and r["log"]["objects"]==1
    assert r["receipt"]["quality_sha256"]==quality["quality_sha256"]
    assert r["receipt"]["review_sha256"]==gated["review_sha256"]
    assert importer.retain(data,**args)["replayed_receipt"]
    with pytest.raises(FeedError,match="bundle_quality_policy_changed"):
        importer.preview(raw(package),r["log"]["log_id"])


@pytest.mark.parametrize("bad",[{},None,[],True])
def test_malformed_portable_quality_reports_fail_closed(linked,bad):
    reviewed,spec,_,_=linked
    package=portable(reviewed,spec)
    with pytest.raises(ValueError,match="quality_report_integrity_failure"):
        inspect(raw({**package,"quality_review":bad}))


def test_linked_quality_exact_pins_org_and_expiry_required(linked):
    reviewed,spec,_,_=linked
    package=portable(reviewed,spec)
    q=make_report(package["source_bundle"],pins=package["pins"])
    with pytest.raises(JobError,match="explicit_portable_quality_review"):
        portable(reviewed,spec,quality_review_json=json.dumps(q))
    with pytest.raises(JobError,match="explicit_portable_quality_review"):
        portable(reviewed,spec,confirm_quality_gated_export=True)
    for field in ("publication_revision","expires_at"):
        pins=deepcopy(package["pins"]);pins[0][field]+=1
        wrong=make_report(package["source_bundle"],pins=pins)
        # Recomputed internally consistent report, but wrong exact live scope.
        with pytest.raises(JobError,match="quality_report_integrity_failure"):
            portable(reviewed,spec,quality_review_json=json.dumps(wrong),confirm_quality_gated_export=True)
        with pytest.raises(ValueError,match="quality_report_integrity_failure"):
            inspect(raw({**package,"quality_review":wrong}))
    wrong=make_report(package["source_bundle"],pins=package["pins"],org="another")
    with pytest.raises(ValueError,match="quality_report_integrity_failure"):
        inspect(raw({**package,"quality_review":wrong}))
    with pytest.raises(JobError,match="invalid_bundle_json"):
        portable(reviewed,spec,quality_review_json='{"format":"x","format":"y"}',confirm_quality_gated_export=True)


def test_live_quality_console_report_roundtrips_into_both_export_paths(linked):
    """Use the actual Gateway report rather than a synthetic offline report."""
    from gateway.connector_publication_quality import Review, LinkedExport, review, linked_export
    reviewed,spec,_,_=linked
    quality_spec={"snapshots":spec["snapshots"],"policy":{"min_events":1,"min_cases":1},
                  "confirm_inspection":True}
    quality=review(reviewed.publications,"customer",Review.model_validate(quality_spec),"owner")
    assert quality["all_selected_sources_pass"]
    report=reviewed.preview("customer",Plan.model_validate(spec),"owner")
    direct=linked_export(reviewed,"customer",LinkedExport.model_validate({
        **spec,"expected_review_sha256":report["review_sha256"],
        "confirm_link_assertions":True,"confirm_export":True,"quality":quality_spec,
        "expected_quality_sha256":quality["quality_sha256"],
        "confirm_quality_gated_export":True}),"owner")
    package=portable(reviewed,spec,quality_review_json=json.dumps(quality),
                     confirm_quality_gated_export=True)
    projection,preview=inspect(raw(package))
    assert direct["quality_review"]==package["quality_review"]==quality
    assert [r.model_dump() for r in projection.events]==direct["events"]
    assert projection.mapping==direct["mapping"]
    assert preview["quality_report_recomputed"]
    data,args=retain_args(package)
    result=importer.retain(data,**{**args,"confirm_quality_review":True})
    assert result["log"]["events"]==direct["event_count"]
    assert result["receipt"]["quality_sha256"]==quality["quality_sha256"]
    assert result["receipt"]["review_sha256"]==report["review_sha256"]
