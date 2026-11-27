"""Integrated evidence journey: actual publication -> quality -> linked review -> triage -> local mining.

The fixtures use the existing real SQLite/PostgreSQL Gateway stores and local
DuckDB workspace; no synthetic replacement of the actual application methods.
"""
import json

import pytest

from gateway.connector_case_review import Plan
from gateway.connector_event_publications import Remove
from gateway.connector_jobs import JobError
from gateway.connector_publication_quality import Review as QualityReview, Export as QualityExport, review as quality_review, export as quality_export, LinkedExport, linked_export
from gateway.connector_portable_linkage import PortableExport, export_portable
from gateway.connector_temporal_review import Alignment, temporal_review
from gateway.connector_linked_conformance import Catalog, Review as ConformanceReview, catalog, review as conformance_review
from gateway.connector_exception_triage import Queues, Save, Page, Change, Export as DispositionExport, disposition_csv
from server import enterprise_connector_bundle_import as importer
from server import event_log_workspace as ws
from shared.connector_bundle_import import inspect
from test_connector_case_review import linked
from test_connector_event_publications import deployed
from test_gateway_connector_jobs import db


def import_reviewed(value,name,request_id,*,linkage):
    raw=json.dumps(value,ensure_ascii=True).encode()
    report=importer.preview(raw)
    result=importer.retain(raw,file_sha256=report["file_sha256"],
        canonical_bundle_sha256=report["canonical_bundle_sha256"],request_id=request_id,
        name=name,confirm_retention=True,confirm_origin_review=True,
        confirm_linkage_review=linkage,confirm_quality_review=True)
    return raw,result


def test_complete_quality_linkage_triage_and_local_import(linked):
    reviewed,spec,ga,gb=linked
    store=reviewed.publications
    qspec={"snapshots":spec["snapshots"],"policy":{"min_events":2,"min_cases":1},
           "confirm_inspection":True}
    q=quality_review(store,"customer",QualityReview.model_validate(qspec),"owner")
    assert q["all_selected_sources_pass"] and q["summary"]["events"]==4
    ordinary=quality_export(store,"customer",QualityExport.model_validate({
        **qspec,"expected_quality_sha256":q["quality_sha256"],
        "confirm_quality_gated_export":True,"confirm_event_export":True}),"owner")
    raw_ordinary,ordinary_log=import_reviewed(ordinary,"Ordinary quality journey","1"*32,linkage=False)
    assert ordinary_log["log"]["events"]==4 and ordinary_log["log"]["objects"]==2
    assert ordinary_log["receipt"]["quality_sha256"]==q["quality_sha256"]

    base=reviewed.preview("customer",Plan.model_validate(spec),"owner")
    exact={**spec,"expected_review_sha256":base["review_sha256"]}
    assert base["summary"]["export_cases"]==1
    time_report=temporal_review(reviewed,"customer",Alignment.model_validate({
        **exact,"confirm_temporal_assumptions":True}),"owner")
    assert time_report["review_sha256"]==base["review_sha256"]
    assert time_report["summary"]["event_pairs"]==4
    assert not time_report["independently_verified_handoffs"]

    activities=catalog(reviewed,"customer",Catalog.model_validate({
        **exact,"confirm_rule_catalog":True}),"owner")
    selected=activities["entries"][0]
    rule={"rule_id":"investigate_observed_activity","kind":"forbidden",
          "left":{"job_id":selected["job_id"],"activity":selected["activity"]}}
    checks={**exact,"rules":[rule],"confirm_conformance_review":True}
    conformance=conformance_review(reviewed,"customer",ConformanceReview.model_validate(checks),"owner")
    assert conformance["review_sha256"]==base["review_sha256"]
    assert conformance["summary"]["rule_outcomes"]["not_satisfied"]==1
    queues=Queues(reviewed);queues.initialize()
    save=Save.model_validate({**checks,"expected_conformance_sha256":conformance["conformance_sha256"],
                              "confirm_exception_retention":True})
    queue=queues.save("customer",save,"owner")["queue"]
    item=queues.page("customer",queue["queue_id"],Page())["items"][0]
    assert item["linked_case_id"]==base["groups"][0]["linked_case_id"]
    result=queues.change("customer",queue["queue_id"],Change(expected_queue_revision=1,
        edits=[{"item_id":item["item_id"],"expected_revision":1,"state":"reviewed",
                "reason":"source_gap_suspected"}],confirm_disposition=True),"owner")
    assert result["queue"]["revision"]==2
    exported=queues.export("customer",queue["queue_id"],DispositionExport(
        expected_revision=2,confirm_disposition_export=True),"owner")
    assert "source_gap_suspected" in disposition_csv(exported)
    assert "not_satisfied" in disposition_csv(exported)

    common={**exact,"confirm_export":True,"confirm_link_assertions":True}
    derived=linked_export(reviewed,"customer",LinkedExport.model_validate({
        **common,"quality":qspec,"expected_quality_sha256":q["quality_sha256"],
        "confirm_quality_gated_export":True}),"owner")
    request=PortableExport.model_validate({**common,"confirm_complete_source_disclosure":True,
        "quality_review_json":json.dumps(q),"confirm_quality_gated_export":True})
    portable=export_portable(reviewed,"customer",request,"owner")
    raw_portable,linked_log=import_reviewed(portable,"Linked quality journey","2"*32,linkage=True)
    projection,report=inspect(raw_portable)
    assert [r.model_dump() for r in projection.events]==derived["events"]
    assert projection.mapping==derived["mapping"]
    assert report["quality_sha256"]==q["quality_sha256"]
    assert linked_log["receipt"]["review_sha256"]==base["review_sha256"]
    assert linked_log["log"]["events"]==4 and linked_log["log"]["objects"]==1
    assert linked_log["log"]["log_id"]!=ordinary_log["log"]["log_id"]
    # A source revocation invalidates every live operation, not a local
    # already-authorized offline copy. Preserve the distinction.
    store.remove("customer",ga["job_id"],Remove(expected_publication_revision=ga["revision"],
        confirm_delete=True),"owner",ga["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        export_portable(reviewed,"customer",request,"owner")
    with pytest.raises(JobError,match="exception_queue_missing_or_invalidated"):
        queues.page("customer",queue["queue_id"],Page())
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        quality_review(store,"customer",QualityReview.model_validate(qspec),"owner")
    queues.purge_expired()
    assert queues.inventory("customer")["queues"]==[]
    assert not inspect(raw_portable)[1]["live_grants_verified"]
    assert not inspect(raw_ordinary)[1]["live_grants_verified"]
    assert importer.history(linked_log["log"]["log_id"])["receipts"][0]["quality_sha256"]==q["quality_sha256"]
    for retained in (ordinary_log,linked_log):
        ws.delete_log(retained["log"]["log_id"])
    assert importer.inventory()["logs"]==[]


def test_specific_delete_routes_precede_generic_publication_delete():
    # Match the actual Starlette route table without invoking storage or
    # accidentally testing only the generic handler's error response.
    from fastapi import FastAPI
    from starlette.routing import Match
    from types import SimpleNamespace
    from gateway.connector_event_routes import install
    app=FastAPI()
    install(app,SimpleNamespace(db=None),lambda *args:None)
    base="/v1/admin/connector-events/customer"
    for suffix,expected in (("/saved-reviews/plan/delete","delete_review"),
                            ("/exception-queues/queue/delete","delete_exception_queue"),
                            ("/cj_job/cep_grant/delete","delete")):
        scope={"type":"http","method":"POST","path":base+suffix,"root_path":""}
        matches=[route.name for route in app.routes if route.matches(scope)[0]==Match.FULL]
        assert matches and matches[0]==expected
