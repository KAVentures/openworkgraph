"""Owner-reviewed cross-source case reconstruction from exact published snapshots.

A link is an operator assertion, not an independently verified business
identity. No automatic fuzzy matches, transitive closure or raw identifiers.
No derived data persists in the Gateway; each export rechecks the grants.
"""
from collections import Counter, defaultdict
from datetime import datetime
import csv
from io import StringIO
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from shared.connector_event_snapshot import Snapshot, Strict, DIGEST, digest, manifest_digest
from .connector_event_publications import Pin, MAX_EXPORT_EVENTS
from .connector_jobs import JobError, JOB

FORMAT="openworkgraph.reviewed-connector-cases.v1"
MAX_GROUPS=500
MAX_MEMBERS=2000
MAX_REQUEST=512*1024
MAX_CASE_PAGE=100


class Scope(Strict):
    snapshots:list[Pin]=Field(min_length=2,max_length=10)
    confirm_inspection:StrictBool

    @model_validator(mode="after")
    def distinct(self):
        if len({x.job_id for x in self.snapshots})!=len(self.snapshots):
            raise ValueError("duplicate snapshot job")
        return self


class Catalog( Scope):
    job_id:str|None=Field(default=None,pattern=JOB)
    offset:StrictInt=Field(ge=0,le=MAX_EXPORT_EVENTS,default=0)
    limit:StrictInt=Field(ge=1,le=MAX_CASE_PAGE,default=50)
    expected_scope_sha256:str|None=Field(default=None,pattern=DIGEST)

    @model_validator(mode="after")
    def pagination(self):
        if self.offset and self.expected_scope_sha256 is None:
            raise ValueError("pin scope before requesting further pages")
        if self.job_id and self.job_id not in {p.job_id for p in self.snapshots}:
            raise ValueError("job outside selected scope")
        return self


class Member(Strict):
    job_id:str=Field(pattern=JOB)
    case_id:str=Field(pattern=DIGEST)


class Group(Strict):
    members:list[Member]=Field(min_length=2,max_length=20)
    basis:Literal["reviewed_native_reference","reviewed_business_record","operator_assertion"]
    evidence_sha256:str|None=Field(default=None,pattern=DIGEST)

    @model_validator(mode="after")
    def distinct(self):
        if len({(m.job_id,m.case_id) for m in self.members})!=len(self.members):
            raise ValueError("duplicate case member")
        if len({m.job_id for m in self.members})<2:
            raise ValueError("each group must span distinct source jobs")
        return self


class Plan(Scope):
    groups:list[Group]=Field(min_length=1,max_length=MAX_GROUPS)
    confirm_many_to_one:StrictBool=False
    include_unlinked:StrictBool=True

    @model_validator(mode="after")
    def disjoint(self):
        seen=set();jobs={p.job_id for p in self.snapshots}
        for g in self.groups:
            for m in g.members:
                k=(m.job_id,m.case_id)
                if k in seen or m.job_id not in jobs:
                    raise ValueError("duplicate group membership or member outside source scope")
                seen.add(k)
            if len({m.job_id for m in g.members})<len(g.members) and not self.confirm_many_to_one:
                raise ValueError("same-source many-to-one linkage requires separate confirmation")
        if len(seen)>MAX_MEMBERS:
            raise ValueError("link member budget exceeded")
        return self


class Export(Plan):
    expected_review_sha256:str=Field(pattern=DIGEST)
    confirm_link_assertions:StrictBool
    confirm_export:StrictBool


def sorted_pins(pins):
    return sorted((p.model_dump() for p in pins),key=lambda p:p["job_id"])


def member_key(member):
    return (member.job_id,member.case_id)


def group_key(group):
    return sorted(member_key(m) for m in group.members)


def elapsed(first,last):
    return int((datetime.fromisoformat(last)-datetime.fromisoformat(first)).total_seconds())


class ReviewedCases:
    def __init__(self,publications):
        self.publications=publications
        self.jobs=publications.jobs

    def _read(self,c,org,spec):
        if not spec.confirm_inspection:
            raise JobError("explicit_cross_source_inspection_required",403)
        manifests=[];cases=defaultdict(list);providers={};expires=[];count=0
        for pin in sorted(spec.snapshots,key=lambda p:p.job_id):
            self.jobs._get(c,org,pin.job_id)
            definition,payload=self.publications._row(c,org,pin.job_id)
            if (not definition or payload is None or definition["grant_id"]!=pin.grant_id
                    or definition["revision"]!=pin.publication_revision
                    or definition["manifest_sha256"]!=pin.manifest_sha256):
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
            expires.append(definition["expires_at"])
            providers[pin.job_id]=snap.manifest.provider
            manifests.append({**pin.model_dump(),"manifest":snap.manifest.model_dump(),"expires_at":definition["expires_at"]})
            for e in snap.events:
                cases[(pin.job_id,e.case_id)].append(e.model_dump())
        scope=digest({"format":FORMAT,"organization_id":org,"snapshots":manifests})
        return cases,manifests,providers,count,scope,min(expires)

    def _fresh(self,expires):
        if self.jobs.clock()>=expires:
            raise JobError("snapshot_expired_during_review")

    def _audit(self,c,org,spec,principal,action,details):
        # One bounded counts-only audit per participating source.
        for pin in spec.snapshots:
            j=self.jobs._get(c,org,pin.job_id)
            self.jobs._audit(c,j,principal,"cross_source_cases."+action,details)

    def catalog(self,org,spec,principal):
        with self.jobs._tx(org) as c:
            cases,manifests,providers,count,scope,expires=self._read(c,org,spec)
            if spec.expected_scope_sha256 and spec.expected_scope_sha256!=scope:
                raise JobError("catalog_scope_changed")
            keys=sorted(k for k in cases if not spec.job_id or k[0]==spec.job_id)
            rows=[]
            for job,case in keys[spec.offset:spec.offset+spec.limit]:
                events=cases[job,case]
                rows.append({"job_id":job,"case_id":case,"provider":providers[job],
                             "events":len(events),"distinct_activities":len({e["activity"] for e in events}),
                             "first_observed":events[0]["observed_at"],"last_observed":events[-1]["observed_at"],
                             "day_precision":providers[job]=="sap"})
            next_offset=spec.offset+len(rows)
            self._fresh(expires)
            self._audit(c,org,spec,principal,"catalog",{"case_count":len(rows),"scope_sha256":scope})
            return {"format":FORMAT,"scope_sha256":scope,"expires_at":expires,"manifests":manifests,
                    "cases":rows,"total_cases":len(keys),"total_scope_cases":len(cases),"total_scope_events":count,
                    "next_offset":next_offset if next_offset<len(keys) else None,
                    "identity_independently_verified":False}

    def _compute(self,org,spec,loaded):
        from shared.connector_case_projection import compute_review
        try:
            return compute_review(spec,loaded)
        except ValueError as exc:
            raise JobError(str(exc)) from None

    def preview(self,org,spec,principal):
        with self.jobs._tx(org) as c:
            loaded=self._read(c,org,spec)
            _,report=self._compute(org,spec,loaded)
            self._fresh(report["expires_at"])
            self._audit(c,org,spec,principal,"preview",{"review_sha256":report["review_sha256"],
                        "linked_groups":report["summary"]["linked_groups"],"linked_events":report["summary"]["linked_events"]})
            return report

    def export(self,org,spec,principal):
        if not spec.confirm_link_assertions or not spec.confirm_export:
            raise JobError("explicit_link_assertion_and_export_required",403)
        with self.jobs._tx(org) as c:
            loaded=self._read(c,org,spec)
            return self._export_loaded(c,org,spec,principal,loaded)

    def _export_loaded(self,c,org,spec,principal,loaded):
        """One shared projection under the caller's existing organization transaction."""
        assigned,report=self._compute(org,spec,loaded)
        if report["review_sha256"]!=spec.expected_review_sha256:
            raise JobError("cross_source_review_changed")
        cases,manifests,providers,count,scope,expires=loaded
        from shared.connector_case_projection import project_rows
        rows=project_rows(spec,loaded,assigned)
        self._fresh(expires)
        self._audit(c,org,spec,principal,"export",{"review_sha256":report["review_sha256"],
                    "export_events":len(rows),"linked_groups":len(spec.groups)})
        return {**report,"events":rows,"event_count":len(rows),
                "mapping":{"event_id_column":"event_id","case_column":"case_id","activity_column":"activity",
                           "timestamp_column":"observed_at","source_namespace_column":"source_namespace",
                           "case_namespace":"reviewed_"+report["review_sha256"][:24]},
                "new_log_required_for_changed_plan":True,"source_activity_labels_namespaced":True,
                "local_copies_not_revoked_by_gateway_expiry":True}


def linked_csv(bundle):
    buf=StringIO(newline="")
    writer=csv.DictWriter(buf,fieldnames=["source_namespace","event_id","case_id","activity","observed_at"])
    writer.writeheader();writer.writerows(bundle["events"])
    return buf.getvalue()
