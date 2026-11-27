"""Verify portable unlinked connector bundles without network or storage.

These are self-consistency checks, not signatures or live grant checks.
A party able to replace a bundle can also recompute its unkeyed hashes.
"""
from collections import defaultdict
from hashlib import sha256
import json
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,model_validator
from shared.connector_event_snapshot import Strict,Event,Manifest,Snapshot,DIGEST,digest,manifest_digest

FORMAT="openworkgraph.connector-event-bundle.v1"
MAX_BYTES=16*1024*1024
MAX_EVENTS=20000
JOB=r"^cj_[a-f0-9]{32}$"
GRANT=r"^cep_[a-f0-9]{32}$"
MAPPING={"event_id_column":"event_id","case_column":"case_id","activity_column":"activity",
         "timestamp_column":"observed_at","source_namespace_column":"source_namespace"}


class Source(Strict):
    job_id:str=Field(pattern=JOB)
    grant_id:str=Field(pattern=GRANT)
    manifest:Manifest
    manifest_sha256:str=Field(pattern=DIGEST)


class Row(Event):
    source_namespace:str=Field(pattern=JOB)


class Bundle(Strict):
    format:Literal["openworkgraph.connector-event-bundle.v1"]
    manifests:list[Source]=Field(min_length=1,max_length=10)
    events:list[Row]=Field(min_length=1,max_length=MAX_EVENTS)
    event_count:StrictInt=Field(ge=1,le=MAX_EVENTS)
    cross_source_identity_verified:StrictBool
    provider_completeness_verified:StrictBool
    mapping:dict[str,str]

    @model_validator(mode="after")
    def verify(self):
        if self.cross_source_identity_verified or self.provider_completeness_verified:
            raise ValueError("unverified source claims must remain false")
        if self.mapping!=MAPPING or len(self.events)!=self.event_count:
            raise ValueError("invalid bundle mapping or count")
        sources={s.job_id:s for s in self.manifests}
        if len(sources)!=len(self.manifests) or len({s.grant_id for s in self.manifests})!=len(sources):
            raise ValueError("duplicate source or grant")
        by_job=defaultdict(list)
        for e in self.events:
            if e.source_namespace not in sources:
                raise ValueError("undeclared source namespace")
            by_job[e.source_namespace].append(e.model_dump(exclude={"source_namespace"}))
        for job,src in sources.items():
            if manifest_digest(src.manifest)!=src.manifest_sha256:
                raise ValueError("manifest digest mismatch")
            # Verify the original canonical source rows, including exact
            # event identities, times, case identities and activity values.
            Snapshot(manifest=src.manifest,events=sorted(by_job[job],
                key=lambda r:(r["observed_at"],r["event_id"])))
        self.events.sort(key=lambda e:(e.source_namespace,e.observed_at,e.event_id))
        self.manifests.sort(key=lambda s:s.job_id)
        return self


def decode(raw):
    if not isinstance(raw,(bytes,bytearray)) or not 1<=len(raw)<=MAX_BYTES:
        raise ValueError("invalid_bundle_size")
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise ValueError("duplicate key")
            d[k]=v
        return d
    def constant(_):raise ValueError("nonfinite value")
    try:
        # Accept UTF-8 only; no automatic ZIP, UTF-16 or network retrieval.
        value=json.loads(raw.decode("utf-8"),object_pairs_hook=pairs,parse_constant=constant)
    except (ValueError,UnicodeError,RecursionError):
        raise ValueError("invalid_bundle_json") from None
    if not isinstance(value,dict) or value.get("format")!=FORMAT:
        raise ValueError("unsupported_bundle_format")
    try:
        return Bundle.model_validate(value)
    except (ValueError,RecursionError):
        raise ValueError("bundle_manifest_or_row_integrity_failure") from None


def inspect(raw):
    bundle=decode(raw)
    report={
        "format":"openworkgraph.connector-bundle-import-review.v1",
        "file_sha256":sha256(raw).hexdigest(),
        "canonical_bundle_sha256":digest(bundle.model_dump()),
        "event_count":bundle.event_count,
        "source_local_case_count":sum(s.manifest.case_count for s in bundle.manifests),
        "sources":[s.model_dump() for s in bundle.manifests],
        "activity_labels_present":any(s.manifest.activity_mode=="labels" for s in bundle.manifests),
        "business_date_evidence_present":any(s.manifest.provider=="sap" for s in bundle.manifests),
        "manifest_and_row_integrity_verified":True,
        "gateway_origin_authenticated":False,"live_grants_verified":False,
        "provider_completeness_verified":False,"business_identity_verified":False,
        "source_namespace_isolation":True,
    }
    return bundle,report
