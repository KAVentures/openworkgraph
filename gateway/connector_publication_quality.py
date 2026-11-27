"""Reviewed quality gates for published connector evidence.

Quality preview is counts-only. Gated export atomically rechecks live
publication pins and the exact quality report before disclosing event rows.
The existing ordinary export remains available with its separate consent.
"""
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import Snapshot,Strict,DIGEST,digest,manifest_digest,MAX_EVENTS
from shared.connector_publication_quality import summarize,evaluate
from .connector_event_publications import Pin,MAX_EXPORT_EVENTS
from .connector_jobs import JobError

FORMAT="openworkgraph.connector-publication-quality.v1"


class Policy(Strict):
    min_events:StrictInt|None=Field(default=None,ge=1,le=MAX_EVENTS)
    min_cases:StrictInt|None=Field(default=None,ge=1,le=MAX_EVENTS)
    max_singleton_case_rate_bps:StrictInt|None=Field(default=None,ge=0,le=10000)
    max_single_activity_case_rate_bps:StrictInt|None=Field(default=None,ge=0,le=10000)
    max_largest_activity_rate_bps:StrictInt|None=Field(default=None,ge=0,le=10000)
    max_timestamp_tie_event_rate_bps:StrictInt|None=Field(default=None,ge=0,le=10000)
    min_cases_eligible_for_reported_order:StrictInt|None=Field(default=None,ge=1,le=MAX_EVENTS)

    @model_validator(mode="after")
    def configured(self):
        if not any(v is not None for v in self.model_dump().values()):
            raise ValueError("at least one explicit evidence gate required")
        return self


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
    sources=[]
    for pin,job,d,snap in loaded:
        policy=spec.source_overrides.get(pin.job_id,spec.policy).model_dump()
        m=snap.manifest
        metrics=summarize([e.model_dump() for e in snap.events],day_precision=m.provider=="sap")
        checks=evaluate(metrics,policy)
        status="fail" if any(g["status"]=="fail" for g in checks) else (
            "indeterminate" if any(g["status"]=="indeterminate" for g in checks) else "pass")
        # No labels, case/event identities, source credentials or raw rows.
        sources.append({**pin.model_dump(),"provider":m.provider,
                        "window":{"since":m.since,"until":m.until,"change_checkpoint":m.checkpoint},
                        "expires_at":d["expires_at"],"activity_mode":m.activity_mode,
                        "policy_origin":"source_override" if pin.job_id in spec.source_overrides else "default",
                        "policy":policy,"metrics":metrics,"gates":checks,"status":status})
    summary={"sources":len(sources),"events":sum(x["metrics"]["events"] for x in sources),
             "source_local_cases":sum(x["metrics"]["cases"] for x in sources),
             "passing_sources":sum(x["status"]=="pass" for x in sources),
             "failing_sources":sum(x["status"]=="fail" for x in sources),
             "indeterminate_sources":sum(x["status"]=="indeterminate" for x in sources)}
    report={"format":FORMAT,"organization_id":org,"summary":summary,"sources":sources,
            "default_policy":spec.policy.model_dump(),
            "expires_at":min(x["expires_at"] for x in sources),
            "all_selected_sources_pass":all(x["status"]=="pass" for x in sources),
            "provider_completeness_verified":False,"business_identity_verified":False,
            "physical_occurrence_times_verified":False,"cross_source_identity_inferred":False,
            "interpretation":"Thresholds apply to the selected disclosed sample only. Singleton cases and repeated labels may be legitimate. Reported timestamp ties are not verified simultaneous actions. SAP business dates do not establish occurrence order. Source-local cases are not distinct enterprise business cases."}
    return {**report,"quality_sha256":digest(report)}


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
