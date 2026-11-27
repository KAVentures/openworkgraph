"""Short-lived, explicitly approved copies of selected retained connector events.

Uses the connector job's organization transaction and worker identity.
Separate from the canonical local event store and operational job receipts.
"""
from datetime import datetime, timezone
import csv
from io import StringIO
import json
import secrets

from pydantic import Field, StrictBool, StrictInt, model_validator

from shared.connector_event_snapshot import Manifest, Snapshot, manifest_digest, Strict, DIGEST, MAX_BYTES
from .connector_jobs import JobError, JOB

SCHEMA="""
CREATE TABLE IF NOT EXISTS connector_event_publications (
 organization_id TEXT NOT NULL, job_id TEXT NOT NULL,
 grant_id TEXT NOT NULL, revision INTEGER NOT NULL,
 job_revision INTEGER NOT NULL, manifest_sha256 TEXT NOT NULL,
 expires_at DOUBLE PRECISION NOT NULL, definition TEXT NOT NULL,
 payload TEXT, PRIMARY KEY(organization_id,job_id)
);
CREATE INDEX IF NOT EXISTS idx_connector_event_publication_expiry
 ON connector_event_publications(expires_at);
"""
MAX_EXPORT_EVENTS=20000
MAX_ORG_SNAPSHOTS=20


class Approve(Strict):
    expected_job_revision: StrictInt=Field(ge=1)
    manifest: Manifest
    expected_publication_revision: StrictInt=Field(ge=0)
    retention_hours: StrictInt=Field(ge=1,le=168)
    confirm_event_disclosure: StrictBool
    confirm_activity_labels: StrictBool=False


class Upload(Strict):
    grant_id: str=Field(pattern=r"^cep_[a-f0-9]{32}$")
    snapshot: Snapshot


class Remove(Strict):
    expected_publication_revision: StrictInt=Field(ge=1)
    confirm_delete: StrictBool


class Pin(Strict):
    job_id: str=Field(pattern=JOB)
    manifest_sha256: str=Field(pattern=DIGEST)
    publication_revision: StrictInt=Field(ge=1)


class Bundle(Strict):
    snapshots: list[Pin]=Field(min_length=1,max_length=10)
    confirm_export: StrictBool

    @model_validator(mode="after")
    def distinct(self):
        if len({x.job_id for x in self.snapshots})!=len(self.snapshots):
            raise ValueError("duplicate job selection")
        return self


class Publications:
    def __init__(self,jobs):
        self.jobs=jobs
        self.db=jobs.db
        self.retention_ok=True

    def initialize(self):
        with self.db.connect() as c:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    self.db._execute(c,statement)

    def q(self,c,sql,*params):
        return self.jobs._sql(c,sql,*params)

    def purge_expired(self):
        # Bounded global cleanup; expired rows are never readable even if
        # physical cleanup has not completed yet.
        with self.db.connect() as c:
            self.q(c,"""DELETE FROM connector_event_publications
                WHERE expires_at<=? AND (organization_id,job_id) IN
                (SELECT organization_id,job_id FROM connector_event_publications
                 WHERE expires_at<=? ORDER BY expires_at LIMIT 100)""",self.jobs.clock(),self.jobs.clock())

    def _row(self,c,org,job):
        r=self.q(c,"SELECT definition,payload FROM connector_event_publications WHERE organization_id=? AND job_id=? AND expires_at>?",
                 org,job,self.jobs.clock()).fetchone()
        return (json.loads(r[0]),r[1]) if r else (None,None)

    def _public(self,d,payload):
        if d is None:
            return None
        return {**d,"state":"published" if payload is not None else "approved",
                "provider_completeness_verified":False,"raw_provider_payload_received":False}

    def inventory(self,org):
        with self.jobs._tx(org) as c:
            rows=self.q(c,"SELECT definition,CASE WHEN payload IS NULL THEN 0 ELSE 1 END FROM connector_event_publications WHERE organization_id=? AND expires_at>? ORDER BY job_id",org,self.jobs.clock()).fetchall()
            return {"publications":[self._public(json.loads(r[0]),True if r[1] else None) for r in rows],
                    "retention_cleanup_ok":self.retention_ok,"source_completeness_verified":False}

    def approve(self,org,job,spec,principal):
        if not spec.confirm_event_disclosure or spec.manifest.activity_mode=="labels" and not spec.confirm_activity_labels:
            raise JobError("explicit_event_and_label_disclosure_required",403)
        with self.jobs._tx(org) as c:
            j=self.jobs._get(c,org,job)
            if j["revision"]!=spec.expected_job_revision:
                raise JobError("job_revision_changed")
            m=spec.manifest
            if (m.profile_id,m.profile_revision,m.profile_sha256,m.provider)!=(j["profile_id"],j["profile_revision"],j["profile_sha256"],j["provider"]):
                raise JobError("snapshot_profile_pin_mismatch")
            if datetime.fromisoformat(m.checkpoint).timestamp()>self.jobs.clock()+300:
                raise JobError("snapshot_checkpoint_in_future")
            old,payload=self._row(c,org,job)
            if (old["revision"] if old else 0)!=spec.expected_publication_revision:
                raise JobError("publication_revision_changed")
            if old:
                raise JobError("delete_prior_publication_before_approval")
            if self.q(c,"SELECT count(*) FROM connector_event_publications WHERE organization_id=? AND expires_at>?",org,self.jobs.clock()).fetchone()[0]>=MAX_ORG_SNAPSHOTS:
                raise JobError("organization_snapshot_limit")
            # No reuse of an old grant identifier even after deletion/expiry.
            grant="cep_"+secrets.token_hex(16)
            d={"job_id":job,"organization_id":org,"grant_id":grant,"revision":1,
               "job_revision":j["revision"],"manifest":m.model_dump(),"manifest_sha256":manifest_digest(m),
               "expires_at":self.jobs.clock()+spec.retention_hours*3600}
            self.q(c,"DELETE FROM connector_event_publications WHERE organization_id=? AND job_id=?",org,job)
            self.q(c,"INSERT INTO connector_event_publications VALUES (?,?,?,?,?,?,?,?,NULL)",org,job,grant,1,
                   j["revision"],d["manifest_sha256"],d["expires_at"],json.dumps(d,separators=(",",":")))
            self.jobs._audit(c,j,principal,"event_publication.approved",{"event_count":m.event_count,"activity_mode":m.activity_mode,"grant_id":grant})
            return self._public(d,None)

    def preflight(self,token):
        org,job,secret=self.jobs._identity(token)
        with self.jobs._tx(org) as c:
            self.jobs._reauth(c,org,job,secret)
            j=self.jobs._get(c,org,job)
            d,payload=self._row(c,org,job)
            if not d or d["job_revision"]!=j["revision"]:
                raise JobError("no_current_event_publication_grant",403)
            return self._public(d,payload)

    def upload(self,token,spec):
        org,job,secret=self.jobs._identity(token)
        with self.jobs._tx(org) as c:
            self.jobs._reauth(c,org,job,secret)
            j=self.jobs._get(c,org,job)
            d,existing=self._row(c,org,job)
            if not d or d["grant_id"]!=spec.grant_id or d["job_revision"]!=j["revision"]:
                raise JobError("publication_grant_expired_or_invalidated",403)
            if manifest_digest(spec.snapshot.manifest)!=d["manifest_sha256"]:
                raise JobError("approved_manifest_mismatch",403)
            payload=json.dumps(spec.snapshot.model_dump(),sort_keys=True,separators=(",",":"),ensure_ascii=True)
            if len(payload.encode())>MAX_BYTES:
                raise JobError("publication_payload_budget_exceeded",413)
            if existing is not None:
                if existing!=payload:
                    raise JobError("conflicting_snapshot_replay")
                return {**self._public(d,existing),"replayed":True}
            self.q(c,"UPDATE connector_event_publications SET payload=? WHERE organization_id=? AND job_id=?",payload,org,job)
            self.jobs._audit(c,j,job,"event_publication.uploaded",{"event_count":spec.snapshot.manifest.event_count,"grant_id":d["grant_id"]})
            return {**self._public(d,payload),"replayed":False}

    def remove(self,org,job,spec,principal,grant_id):
        if not spec.confirm_delete:
            raise JobError("explicit_publication_deletion_required",403)
        with self.jobs._tx(org) as c:
            j=self.jobs._get(c,org,job)
            d,payload=self._row(c,org,job)
            if not d or d["revision"]!=spec.expected_publication_revision or d["grant_id"]!=grant_id:
                raise JobError("publication_changed")
            self.q(c,"DELETE FROM connector_event_publications WHERE organization_id=? AND job_id=?",org,job)
            self.jobs._audit(c,j,principal,"event_publication.deleted",{"grant_id":grant_id})
            return {"deleted":True,"local_source_deleted":False}

    def export(self,org,spec,principal):
        if not spec.confirm_export:
            raise JobError("explicit_event_export_required",403)
        with self.jobs._tx(org) as c:
            rows=[]
            manifests=[]
            for pin in spec.snapshots:
                j=self.jobs._get(c,org,pin.job_id)
                d,payload=self._row(c,org,pin.job_id)
                if (not d or payload is None or d["manifest_sha256"]!=pin.manifest_sha256
                        or d["revision"]!=pin.publication_revision):
                    raise JobError("snapshot_missing_or_changed")
                snap=Snapshot.model_validate_json(payload)
                if manifest_digest(snap.manifest)!=pin.manifest_sha256:
                    raise JobError("retained_snapshot_integrity_failure")
                if len(rows)+len(snap.events)>MAX_EXPORT_EVENTS:
                    raise JobError("combined_snapshot_event_budget_exceeded")
                # Explicit source namespace per job prevents accidental case or
                # event linkage across independent provider profiles.
                rows.extend({"source_namespace":pin.job_id,**event.model_dump()} for event in snap.events)
                manifests.append({"job_id":pin.job_id,"grant_id":d["grant_id"],"manifest":snap.manifest.model_dump(),"manifest_sha256":pin.manifest_sha256})
                self.jobs._audit(c,j,principal,"event_publication.exported",{"event_count":len(snap.events),"grant_id":d["grant_id"]})
            rows.sort(key=lambda r:(r["source_namespace"],r["observed_at"],r["event_id"]))
            return {"format":"openworkgraph.connector-event-bundle.v1","manifests":manifests,"events":rows,
                    "event_count":len(rows),"cross_source_identity_verified":False,"provider_completeness_verified":False,
                    "mapping":{"event_id_column":"event_id","case_column":"case_id","activity_column":"activity",
                               "timestamp_column":"observed_at","source_namespace_column":"source_namespace"}}


def bundle_csv(bundle):
    """Machine-import CSV, not a spreadsheet review export."""
    buf=StringIO(newline="")
    writer=csv.DictWriter(buf,fieldnames=["source_namespace","event_id","case_id","activity","observed_at"])
    writer.writeheader()
    writer.writerows(bundle["events"])
    return buf.getvalue()
