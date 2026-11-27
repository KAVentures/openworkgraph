"""Reviewed quality gates for published connector evidence.

Quality preview is counts-only. Gated export atomically rechecks live
publication pins and the exact quality report before disclosing event rows.
The existing ordinary export remains available with its separate consent.
"""
from pydantic import Field,StrictBool,model_validator
from shared.connector_event_snapshot import Snapshot,Strict,DIGEST,manifest_digest
from shared.connector_quality_verification import Policy,SourceDeclaration,calculate,FORMAT
from shared.connector_bundle_import import Bundle as PortableBundle,FORMAT as BUNDLE_FORMAT,MAPPING
from .connector_event_publications import Pin,MAX_EXPORT_EVENTS
from .connector_jobs import JobError
from .connector_case_review import Export as CaseExport,sorted_pins

class Review(Strict):
    snapshots:list[Pin]=Field(min_length=1,max_length=10)
    policy:Policy
    source_overrides:dict[str,Policy]=Field(default_factory=dict,max_length=10)
    confirm_inspection:StrictBool

    @model_validator(mode="after")
    def scope(self):
        ids={p.job_id for p in self.snapshots}
        if len(ids)!=len(self.snapshots) or set(self.source_overrides)-ids:
            raise ValueError("duplicate or unselected source")
        # Overrides replace the policy explicitly for the source, rather
        # than silently merging or dropping selected thresholds.
        return self


class Export(Review):
    expected_quality_sha256:str=Field(pattern=DIGEST)
    confirm_quality_gated_export:StrictBool
    confirm_event_export:StrictBool


def _load(store,c,org,spec):
    if not spec.confirm_inspection:
        raise JobError("explicit_publication_quality_inspection_required",403)
    loaded=[];count=0
    for pin in sorted(spec.snapshots,key=lambda p:p.job_id):
        job=store.jobs._get(c,org,pin.job_id)
        d,payload=store._row(c,org,pin.job_id)
        if (not d or payload is None or d["grant_id"]!=pin.grant_id
                or d["revision"]!=pin.publication_revision or d["manifest_sha256"]!=pin.manifest_sha256):
            raise JobError("snapshot_missing_or_changed")
        try:
            snap=Snapshot.model_validate_json(payload)
        except ValueError:
            raise JobError("retained_snapshot_integrity_failure") from None
        if manifest_digest(snap.manifest)!=pin.manifest_sha256:
            raise JobError("retained_snapshot_integrity_failure")
        count+=len(snap.events)
        if count>MAX_EXPORT_EVENTS:
            raise JobError("combined_snapshot_event_budget_exceeded")
        loaded.append((pin,job,d,snap))
    return loaded


def _report(org,spec,loaded):
    """Use the exact same quality contract as the offline verifier."""
    manifests=[];events=[];declarations=[]
    for pin,_,definition,snap in loaded:
        manifests.append({"job_id":pin.job_id,"grant_id":pin.grant_id,
                          "manifest":snap.manifest.model_dump(),"manifest_sha256":pin.manifest_sha256})
        events.extend({"source_namespace":pin.job_id,**event.model_dump()} for event in snap.events)
        declarations.append(SourceDeclaration(**pin.model_dump(),expires_at=definition["expires_at"],
            policy_origin="source_override" if pin.job_id in spec.source_overrides else "default",
            policy=spec.source_overrides.get(pin.job_id,spec.policy)))
    try:
        bundle=PortableBundle(format=BUNDLE_FORMAT,manifests=manifests,events=events,
            event_count=len(events),cross_source_identity_verified=False,
            provider_completeness_verified=False,mapping=MAPPING)
        return calculate(org,spec.policy,declarations,bundle)
    except ValueError:
        raise JobError("retained_snapshot_integrity_failure") from None


def _fresh(store,report):
    if store.jobs.clock()>=report["expires_at"]:
        raise JobError("snapshot_expired_during_quality_review")


def _audit(store,c,loaded,principal,report,action):
    for pin,job,d,snap in loaded:
        store.jobs._audit(c,job,principal,"event_publication."+action,
            {"quality_sha256":report["quality_sha256"],"source_count":report["summary"]["sources"],
             "event_count":len(snap.events),"all_selected_sources_pass":report["all_selected_sources_pass"]})


def review(store,org,spec,principal):
    with store.jobs._tx(org) as c:
        loaded=_load(store,c,org,spec)
        report=_report(org,spec,loaded)
        _fresh(store,report)
        _audit(store,c,loaded,principal,report,"quality_reviewed")
        _fresh(store,report)
        return report


def export(store,org,spec,principal):
    if not spec.confirm_quality_gated_export or not spec.confirm_event_export:
        raise JobError("explicit_quality_and_event_export_required",403)
    with store.jobs._tx(org) as c:
        loaded=_load(store,c,org,spec)
        report=_report(org,spec,loaded)
        if report["quality_sha256"]!=spec.expected_quality_sha256:
            raise JobError("quality_review_changed")
        if not report["all_selected_sources_pass"]:
            raise JobError("publication_quality_gates_not_satisfied")
        rows=[];manifests=[]
        for pin,job,d,snap in loaded:
            rows.extend({"source_namespace":pin.job_id,**e.model_dump()} for e in snap.events)
            manifests.append({"job_id":pin.job_id,"grant_id":pin.grant_id,
                              "manifest":snap.manifest.model_dump(),"manifest_sha256":pin.manifest_sha256})
        rows.sort(key=lambda r:(r["source_namespace"],r["observed_at"],r["event_id"]))
        _fresh(store,report)
        _audit(store,c,loaded,principal,report,"quality_gated_exported")
        _fresh(store,report)
        return {"format":"openworkgraph.connector-event-bundle.v1","manifests":manifests,
                "events":rows,"event_count":len(rows),"quality_review":report,
                "cross_source_identity_verified":False,"provider_completeness_verified":False,
                "mapping":{"event_id_column":"event_id","case_column":"case_id","activity_column":"activity",
                           "timestamp_column":"observed_at","source_namespace_column":"source_namespace"}}


class LinkedExport(CaseExport):
    quality:Review
    expected_quality_sha256:str=Field(pattern=DIGEST)
    confirm_quality_gated_export:StrictBool

    @model_validator(mode="after")
    def identical_quality_scope(self):
        if sorted_pins(self.snapshots)!=sorted_pins(self.quality.snapshots):
            raise ValueError("quality and linkage must pin exactly the same publications")
        return self


def linked_export(reviewed,org,spec,principal):
    """Gate the same reviewed linkage without an unlocked second transaction."""
    if not spec.confirm_quality_gated_export or not spec.confirm_link_assertions or not spec.confirm_export:
        raise JobError("explicit_quality_linkage_and_export_required",403)
    store=reviewed.publications
    with reviewed.jobs._tx(org) as c:
        quality_loaded=_load(store,c,org,spec.quality)
        quality_report=_report(org,spec.quality,quality_loaded)
        if quality_report["quality_sha256"]!=spec.expected_quality_sha256:
            raise JobError("quality_review_changed")
        if not quality_report["all_selected_sources_pass"]:
            raise JobError("publication_quality_gates_not_satisfied")
        linkage_loaded=reviewed._read(c,org,spec)
        result=reviewed._export_loaded(c,org,spec,principal,linkage_loaded)
        _fresh(store,quality_report)
        _audit(store,c,quality_loaded,principal,quality_report,"quality_gated_linked_exported")
        _fresh(store,quality_report)
        return {**result,"quality_review":quality_report}
