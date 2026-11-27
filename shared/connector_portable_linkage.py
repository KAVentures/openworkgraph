"""Portable linkage evidence package, not an authenticated Gateway signature.

The entire approved source scope is disclosed, even if the linked projection
omits unlinked cases. The importer independently reconstructs the projection
and retains only the selected projected events.
"""
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,StrictFloat,model_validator
from shared.connector_bundle_import import Bundle,JOB,GRANT,MAPPING
from shared.connector_event_snapshot import Strict,DIGEST,digest
from shared.connector_case_projection import compute_review,project_rows,group_key,FORMAT as REVIEW_FORMAT

FORMAT="openworkgraph.portable-reviewed-connector-cases.v1"


class Pin(Strict):
    job_id:str=Field(pattern=JOB)
    grant_id:str=Field(pattern=GRANT)
    publication_revision:StrictInt=Field(ge=1)
    manifest_sha256:str=Field(pattern=DIGEST)
    expires_at:StrictFloat=Field(gt=0,allow_inf_nan=False)


class Member(Strict):
    job_id:str=Field(pattern=JOB)
    case_id:str=Field(pattern=DIGEST)


class Group(Strict):
    members:list[Member]=Field(min_length=2,max_length=20)
    basis:Literal["reviewed_native_reference","reviewed_business_record","operator_assertion"]
    evidence_sha256:str|None=Field(default=None,pattern=DIGEST)


class Envelope(Strict):
    format:Literal["openworkgraph.portable-reviewed-connector-cases.v1"]
    organization_id:str=Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
    source_bundle:Bundle
    pins:list[Pin]=Field(min_length=2,max_length=10)
    groups:list[Group]=Field(min_length=1,max_length=500)
    confirm_many_to_one:StrictBool
    include_unlinked:StrictBool
    scope_sha256:str=Field(pattern=DIGEST)
    review_sha256:str=Field(pattern=DIGEST)
    projected_rows_sha256:str=Field(pattern=DIGEST)

    @model_validator(mode="after")
    def shape(self):
        src={s.job_id:s for s in self.source_bundle.manifests}
        if len({p.job_id for p in self.pins})!=len(self.pins) or set(src)!={p.job_id for p in self.pins}:
            raise ValueError("portable publication scope mismatch")
        for p in self.pins:
            s=src[p.job_id]
            if p.grant_id!=s.grant_id or p.manifest_sha256!=s.manifest_sha256:
                raise ValueError("portable publication pin mismatch")
        seen=set()
        for g in self.groups:
            members=group_key(g)
            jobs={j for j,_ in members}
            if len(jobs)<2 or len(set(members))!=len(members) or jobs-set(src) or seen.intersection(members):
                raise ValueError("invalid or overlapping group membership")
            if len(jobs)!=len(members) and not self.confirm_many_to_one:
                raise ValueError("many-to-one confirmation missing")
            seen.update(members)
            g.members.sort(key=lambda m:(m.job_id,m.case_id))
        if len(seen)>2000:
            raise ValueError("portable linkage member budget exceeded")
        self.pins.sort(key=lambda p:p.job_id)
        self.groups.sort(key=group_key)
        return self


@dataclass
class ProjectedEvent:
    source_namespace:str
    event_id:str
    case_id:str
    activity:str
    observed_at:str
    def model_dump(self):
        return vars(self).copy()


@dataclass
class Projection:
    events:list[ProjectedEvent]
    event_count:int
    mapping:dict


def reconstruct(envelope):
    bundle=envelope.source_bundle
    src={s.job_id:s for s in bundle.manifests}
    cases=defaultdict(list)
    for e in bundle.events:
        cases[(e.source_namespace,e.case_id)].append(e.model_dump(exclude={"source_namespace"}))
    manifests=[{**p.model_dump(),"manifest":src[p.job_id].manifest.model_dump()} for p in envelope.pins]
    scope=digest({"format":REVIEW_FORMAT,"organization_id":envelope.organization_id,"snapshots":manifests})
    expires=min(p.expires_at for p in envelope.pins)
    providers={s.job_id:s.manifest.provider for s in bundle.manifests}
    loaded=cases,manifests,providers,bundle.event_count,scope,expires
    assigned,review=compute_review(envelope,loaded)
    rows=project_rows(envelope,loaded,assigned)
    if scope!=envelope.scope_sha256 or review["review_sha256"]!=envelope.review_sha256 or digest(rows)!=envelope.projected_rows_sha256:
        raise ValueError("portable_linkage_digest_mismatch")
    if len(rows)!=review["summary"]["export_events"] or len({r["case_id"] for r in rows})!=review["summary"]["export_cases"]:
        raise ValueError("portable_linkage_projection_count_mismatch")
    mapping={**MAPPING,"case_namespace":"reviewed_"+envelope.review_sha256[:24]}
    return Projection([ProjectedEvent(**r) for r in rows],len(rows),mapping),review


def inspect_portable(value,raw):
    try:
        envelope=Envelope.model_validate(value)
        projection,review=reconstruct(envelope)
    except (ValueError,RecursionError,OverflowError):
        raise ValueError("portable_linkage_integrity_failure") from None
    bundle=envelope.source_bundle
    report={
        "format":"openworkgraph.connector-bundle-import-review.v1",
        "bundle_kind":"reviewed_linkage",
        "file_sha256":sha256(raw).hexdigest(),
        "canonical_bundle_sha256":digest(envelope.model_dump()),
        "event_count":projection.event_count,
        "source_local_case_count":sum(s.manifest.case_count for s in bundle.manifests),
        "retained_projection_case_count":review["summary"]["export_cases"],
        "sources":[s.model_dump() for s in bundle.manifests],
        "activity_labels_present":any(s.manifest.activity_mode=="labels" for s in bundle.manifests),
        "business_date_evidence_present":any(s.manifest.provider=="sap" for s in bundle.manifests),
        "manifest_and_row_integrity_verified":True,
        "linkage_projection_integrity_verified":True,
        "scope_sha256":envelope.scope_sha256,"review_sha256":envelope.review_sha256,
        "projected_rows_sha256":envelope.projected_rows_sha256,
        "declared_publication_expiry":review["expires_at"],
        "linkage_summary":review["summary"],
        "complete_source_rows_in_file":bundle.event_count,
        "excluded_source_rows_not_retained":bundle.event_count-projection.event_count,
        "gateway_origin_authenticated":False,"live_grants_verified":False,
        "provider_completeness_verified":False,"business_identity_verified":False,
        "source_namespace_isolation":False,
        "source_event_namespace_isolation":True,
        "new_log_required_for_changed_plan":True,
    }
    return projection,report
