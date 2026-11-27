"""Tenant-scoped connector job control plane; no provider credentials or event payloads.

Serialize mutations using one database row per organization (PostgreSQL row
lock or SQLite BEGIN IMMEDIATE). Worker secrets are individually job-scoped.
An expired lease is ambiguous, not permission to run a different window.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import time
from typing import Literal
from shared.enterprise_connector_contract import Provider

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from .auth import token_hash, issue_token
from .db import GatewayDB

FORMAT="openworkgraph.gateway.connector-jobs.v1"
MAX_JOBS=100
MAX_HISTORY=100
LEASE_SECONDS=240
MAX_FAILURES=5
ID=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$"
PROFILE=r"^rest_[a-f0-9]{24}$"
DIGEST=r"^[a-f0-9]{64}$"
JOB=r"^cj_[a-f0-9]{32}$"
LEASE=r"^[a-f0-9]{64}$"
SCHEMA="""
CREATE TABLE IF NOT EXISTS connector_job_orgs (
 organization_id TEXT PRIMARY KEY, fence INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS connector_jobs (
 organization_id TEXT NOT NULL, job_id TEXT NOT NULL,
 worker_token_hash TEXT NOT NULL UNIQUE, profile_id TEXT NOT NULL,
 definition TEXT NOT NULL, PRIMARY KEY(organization_id,job_id),
 UNIQUE(organization_id,profile_id)
);
CREATE TABLE IF NOT EXISTS connector_job_history (
 organization_id TEXT NOT NULL, job_id TEXT NOT NULL,
 sequence BIGINT NOT NULL, receipt TEXT NOT NULL,
 PRIMARY KEY(organization_id,job_id,sequence)
);
"""


class JobError(ValueError):
    def __init__(self,code,status=409):
        super().__init__(code)
        self.code,self.status=code,status


class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)


class Create(Strict):
    organization_id: str=Field(pattern=ID)
    name: str=Field(min_length=1,max_length=100)
    provider: Provider
    profile_id: str=Field(pattern=PROFILE)
    profile_revision: StrictInt=Field(ge=1,le=1_000_000)
    profile_sha256: str=Field(pattern=DIGEST)
    interval_minutes: StrictInt=Field(ge=15,le=10080)
    confirm_dispatch: StrictBool
    confirm_counts_only_receipts: StrictBool


class Change(Strict):
    expected_revision: StrictInt=Field(ge=1,le=1_000_000)
    enabled: StrictBool
    confirm_change: StrictBool
    confirm_ambiguous_recovery: StrictBool=False


class Rebind(Strict):
    expected_revision: StrictInt=Field(ge=1)
    profile_id: str=Field(pattern=PROFILE)
    old_profile_revision: StrictInt=Field(ge=1,le=1_000_000)
    old_profile_sha256: str=Field(pattern=DIGEST)
    new_profile_revision: StrictInt=Field(ge=1,le=1_000_000)
    new_profile_sha256: str=Field(pattern=DIGEST)
    confirm_same_source: StrictBool
    confirm_workers_reconciled: StrictBool
    confirm_dispatch_and_receipts: StrictBool


class Claim(Strict):
    profile_id: str=Field(pattern=PROFILE)
    profile_revision: StrictInt=Field(ge=1,le=1_000_000)
    profile_sha256: str=Field(pattern=DIGEST)


class Readiness(Claim):
    code: Literal["ready","local_profile_unavailable","local_profile_changed",
                  "local_schedule_conflict","local_recording_paused","local_cooldown",
                  "pending_receipt","pending_intent","invalid_outbox","workspace_unavailable"]


class Receipt(Strict):
    lease_id: str=Field(pattern=LEASE)
    job_revision: StrictInt=Field(ge=1,le=1_000_000)
    outcome: Literal["succeeded","failed","skipped","blocked"]
    code: Literal["window_committed","source_failed","rate_limited","no_new_window",
                  "local_profile_unavailable","local_profile_changed","local_schedule_conflict",
                  "local_recording_paused","local_run_uncertain"]
    events_added: StrictInt=Field(ge=0,le=10000,default=0)
    duplicates_skipped: StrictInt=Field(ge=0,le=10000,default=0)
    retry_after_seconds: StrictInt=Field(ge=0,le=86400,default=0)
    checkpoint: str | None=Field(default=None,max_length=40)

    @model_validator(mode="after")
    def consistent(self):
        allowed={"succeeded":{"window_committed"},"failed":{"source_failed","rate_limited"},
                 "skipped":{"no_new_window","local_recording_paused"},
                 "blocked":{"local_profile_unavailable","local_profile_changed","local_schedule_conflict","local_run_uncertain"}}
        if self.code not in allowed[self.outcome]:
            raise ValueError("receipt outcome and code mismatch")
        if self.outcome!="succeeded" and (self.events_added or self.duplicates_skipped or self.checkpoint is not None):
            raise ValueError("non-success receipts cannot assert committed events")
        if self.outcome=="succeeded":
            try:
                at=datetime.fromisoformat(self.checkpoint.replace("Z","+00:00"))
                if at.utcoffset() is None or not 1900<=at.year<=2100:
                    raise ValueError()
                self.checkpoint=at.astimezone(timezone.utc).isoformat()
            except (AttributeError,ValueError,OverflowError):
                raise ValueError("success requires a timezone-qualified checkpoint") from None
        if self.retry_after_seconds and self.code!="rate_limited":
            raise ValueError("retry cooldown only applies to rate limiting")
        return self


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def profile_digest(profile):
    """Fingerprint immutable local configuration, never the source token."""
    return canonical_digest({"provider":profile["provider"],"profile_id":profile["profile_id"],
                             "revision":profile["revision"],"config":profile["config"]})


def init_schema(db):
    with db.connect() as conn:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db._execute(conn,statement)


class Jobs:
    def __init__(self,db:GatewayDB,clock=time.time):
        self.db=db
        self.clock=clock

    def initialize(self):
        init_schema(self.db)

    def _sql(self,c,s,*args):
        return self.db._execute(c,s,args)

    @contextmanager
    def _tx(self,org):
        with self.db.connect() as c:
            if not self.db.is_postgres:
                c.execute("BEGIN IMMEDIATE")
            self._sql(c,"INSERT INTO connector_job_orgs(organization_id) VALUES (?) ON CONFLICT(organization_id) DO NOTHING",org)
            # UPDATE obtains the PostgreSQL row lock even on pooled connections.
            self._sql(c,"UPDATE connector_job_orgs SET fence=0 WHERE organization_id=?",org)
            yield c

    def _get(self,c,org,job):
        r=self._sql(c,"SELECT definition FROM connector_jobs WHERE organization_id=? AND job_id=?",org,job).fetchone()
        if not r:
            raise JobError("unknown_connector_job",404)
        return json.loads(r[0])

    def _put(self,c,j):
        self._sql(c,"UPDATE connector_jobs SET definition=? WHERE organization_id=? AND job_id=?",
                  json.dumps(j,separators=(",",":")),j["organization_id"],j["job_id"])

    def _audit(self,c,j,principal,action,details):
        self._sql(c,"INSERT INTO audit_log(observed_at,organization_id,principal_id,action,details_json) VALUES (?,?,?,?,?)",
                  datetime.fromtimestamp(self.clock(),timezone.utc).isoformat(),j["organization_id"],principal,
                  "connector_job."+action,json.dumps({"job_id":j["job_id"],**details},separators=(",",":")))

    def _history(self,c,j,outcome,details):
        j["sequence"]+=1
        value={"sequence":j["sequence"],"at":self.clock(),"outcome":outcome,**details}
        self._sql(c,"INSERT INTO connector_job_history VALUES (?,?,?,?)",j["organization_id"],j["job_id"],j["sequence"],json.dumps(value,separators=(",",":")))
        self._sql(c,"DELETE FROM connector_job_history WHERE organization_id=? AND job_id=? AND sequence<=?",
                  j["organization_id"],j["job_id"],j["sequence"]-MAX_HISTORY)
        return value

    def _expire(self,c,j,now):
        if j["lease"] and now>=j["lease"]["expires_at"]:
            # We cannot know whether the worker's local transaction committed.
            j.update(enabled=False,paused_reason="expired_lease_requires_reconciliation",lease=None)
            self._history(c,j,"lease_expired",{"revision":j["revision"]})
            self._put(c,j)

    def _public(self,j):
        result={k:j[k] for k in ("organization_id","job_id","name","provider","profile_id","profile_revision",
                               "profile_sha256","interval_minutes","revision","enabled","paused_reason","next_due_at",
                               "consecutive_failures","last_receipt","last_success_at","sequence","created_at")}
        lease=j["lease"]
        result["lease_expires_at"]=lease["expires_at"] if lease else None
        result["state"]="leased" if lease else "paused" if not j["enabled"] else "due" if j["next_due_at"]<=self.clock() else "waiting"
        readiness=j.get("worker_readiness")
        result["worker_readiness"]=readiness
        result["worker_readiness_stale"]=readiness is None or self.clock()-readiness["observed_at"]>120
        result.update(raw_events_received=False,source_credentials_received=False,provider_health_verified=False,
                      worker_receipts_independently_verified=False)
        return result

    def create(self,spec:Create,principal):
        if spec.confirm_dispatch is not True or spec.confirm_counts_only_receipts is not True:
            raise JobError("explicit_dispatch_and_receipt_consent_required",403)
        if not spec.name.strip() or any(ord(c)<32 or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in spec.name):
            raise JobError("invalid_job_name",422)
        now=self.clock()
        token=issue_token("owg_connector_worker")
        j=spec.model_dump(exclude={"confirm_dispatch","confirm_counts_only_receipts"})
        j.update(name=spec.name.strip(),job_id="cj_"+secrets.token_hex(16),revision=1,enabled=True,
                 paused_reason=None,next_due_at=now,consecutive_failures=0,last_receipt=None,last_success_at=None,
                 sequence=0,created_at=now,lease=None,last_completion=None)
        with self._tx(spec.organization_id) as c:
            if self._sql(c,"SELECT count(*) FROM connector_jobs WHERE organization_id=?",spec.organization_id).fetchone()[0]>=MAX_JOBS:
                raise JobError("connector_job_limit")
            if self._sql(c,"SELECT 1 FROM connector_jobs WHERE organization_id=? AND profile_id=?",spec.organization_id,spec.profile_id).fetchone():
                raise JobError("profile_already_assigned")
            self._sql(c,"INSERT INTO connector_jobs VALUES (?,?,?,?,?)",spec.organization_id,j["job_id"],token_hash(token),
                      spec.profile_id,json.dumps(j,separators=(",",":")))
            self._audit(c,j,principal,"created",{"revision":1,"provider":spec.provider})
        return {"job":self._public(j),"worker_token":token,"shown_once":True}

    def inventory(self,org):
        if not org or len(org)>80:
            raise JobError("invalid_organization",422)
        with self._tx(org) as c:
            rows=self._sql(c,"SELECT definition FROM connector_jobs WHERE organization_id=? ORDER BY job_id",org).fetchall()
            out=[]
            for row in rows:
                j=json.loads(row[0]);self._expire(c,j,self.clock());out.append(self._public(j))
            return {"format":FORMAT,"jobs":out,"counts_only":True,"raw_events_received":False}

    def history(self,org,job,limit=50):
        with self._tx(org) as c:
            j=self._get(c,org,job);self._expire(c,j,self.clock())
            rows=self._sql(c,"SELECT receipt FROM connector_job_history WHERE organization_id=? AND job_id=? ORDER BY sequence DESC LIMIT ?",org,job,limit).fetchall()
            return {"job":self._public(j),"history":[json.loads(r[0]) for r in rows],"bounded_history":MAX_HISTORY}

    def change(self,org,job,spec:Change,principal):
        if spec.confirm_change is not True:
            raise JobError("explicit_job_change_required",403)
        with self._tx(org) as c:
            j=self._get(c,org,job);self._expire(c,j,self.clock())
            if j["revision"]!=spec.expected_revision:
                raise JobError("job_revision_changed")
            if spec.enabled and (j["lease"] or j["paused_reason"] in ("expired_lease_requires_reconciliation","cancelled_active_lease","worker_uncertain")) and not spec.confirm_ambiguous_recovery:
                raise JobError("reconcile_local_checkpoint_before_resuming")
            if spec.enabled and j["lease"]:
                raise JobError("cannot_resume_active_lease")
            if spec.enabled:
                # Do not let an administrative resume waive provider cooldown.
                j.update(enabled=True,paused_reason=None,consecutive_failures=0,next_due_at=max(self.clock(),j["next_due_at"]))
            else:
                ambiguous=bool(j["lease"]) or j["paused_reason"] in ("expired_lease_requires_reconciliation","cancelled_active_lease","worker_uncertain")
                j.update(enabled=False,paused_reason="cancelled_active_lease" if ambiguous else "operator_paused",lease=None)
            j["revision"]+=1
            self._history(c,j,"enabled" if spec.enabled else "paused",{"revision":j["revision"],"reason":j["paused_reason"]})
            self._put(c,j);self._audit(c,j,principal,"changed",{"revision":j["revision"],"enabled":j["enabled"]})
            return self._public(j)

    def rotate(self,org,job,revision,confirmed,principal):
        if confirmed is not True:
            raise JobError("explicit_worker_rotation_required",403)
        with self._tx(org) as c:
            j=self._get(c,org,job);self._expire(c,j,self.clock())
            if j["revision"]!=revision or j["enabled"] or j["lease"]:
                raise JobError("pause_job_before_rotating_worker")
            token=issue_token("owg_connector_worker")
            self._sql(c,"UPDATE connector_jobs SET worker_token_hash=? WHERE organization_id=? AND job_id=?",token_hash(token),org,job)
            j["revision"]+=1
            j["last_completion"]=None
            j.pop("worker_readiness",None)
            self._put(c,j);self._audit(c,j,principal,"worker_rotated",{"revision":j["revision"]})
            return {"job":self._public(j),"worker_token":token,"shown_once":True}

    def rebind(self,org,job,spec:Rebind,principal):
        """Reauthorize a newer revision of the same local source profile."""
        if not (spec.confirm_same_source and spec.confirm_workers_reconciled and spec.confirm_dispatch_and_receipts):
            raise JobError("explicit_profile_rebind_and_reconciliation_required",403)
        with self._tx(org) as c:
            j=self._get(c,org,job)
            self._expire(c,j,self.clock())
            if j["revision"]!=spec.expected_revision or j["enabled"] or j["lease"]:
                raise JobError("pause_job_before_rebinding")
            if (j["profile_id"],j["profile_revision"],j["profile_sha256"])!=(spec.profile_id,spec.old_profile_revision,spec.old_profile_sha256):
                raise JobError("old_profile_pin_changed")
            if spec.new_profile_revision<=spec.old_profile_revision or spec.new_profile_sha256==spec.old_profile_sha256:
                raise JobError("strictly_newer_profile_pin_required",422)
            # Old publications and saved reviews may still disclose the prior
            # revision. Require explicit deletion or expiry before rebinding.
            if self._sql(c,"SELECT 1 FROM connector_event_publications WHERE organization_id=? AND job_id=? AND expires_at>?",
                         org,job,self.clock()).fetchone():
                raise JobError("delete_prior_publication_before_rebind")
            token=issue_token("owg_connector_worker")
            self._sql(c,"UPDATE connector_jobs SET worker_token_hash=? WHERE organization_id=? AND job_id=?",
                      token_hash(token),org,job)
            j.update(profile_revision=spec.new_profile_revision,profile_sha256=spec.new_profile_sha256,
                     revision=j["revision"]+1,last_completion=None,paused_reason="operator_paused",
                     consecutive_failures=0)
            j.pop("worker_readiness",None)
            # Preserve last receipt/success and provider retry deadline.
            self._history(c,j,"profile_rebound",{"revision":j["revision"],"old_profile_revision":spec.old_profile_revision,
                          "new_profile_revision":spec.new_profile_revision})
            self._put(c,j)
            self._audit(c,j,principal,"profile_rebound",{"revision":j["revision"],
                       "old_profile_revision":spec.old_profile_revision,"new_profile_revision":spec.new_profile_revision})
            return {"job":self._public(j),"worker_token":token,"shown_once":True,
                    "source_identity_independently_verified":False}

    def _identity(self,token):
        if not isinstance(token,str) or not token.startswith("owg_connector_worker_") or len(token)>180:
            raise JobError("worker_credential_required",401)
        digest=token_hash(token)
        with self.db.connect() as c:
            row=self._sql(c,"SELECT organization_id,job_id FROM connector_jobs WHERE worker_token_hash=?",digest).fetchone()
        if not row:
            raise JobError("worker_credential_required",401)
        return row[0],row[1],digest

    def _reauth(self,c,org,job,digest):
        row=self._sql(c,"SELECT worker_token_hash FROM connector_jobs WHERE organization_id=? AND job_id=?",org,job).fetchone()
        if not row or not hmac.compare_digest(row[0],digest):
            raise JobError("worker_credential_required",401)

    def _verify_pin(self,j,spec):
        if (spec.profile_id,spec.profile_revision,spec.profile_sha256)!=(j["profile_id"],j["profile_revision"],j["profile_sha256"]):
            raise JobError("worker_profile_pin_mismatch",403)

    def inspect_worker(self,token,spec:Claim):
        # Serialize with token rotation and profile rebinding; no lease,
        # job revision or history mutation.
        org,job,digest=self._identity(token)
        with self._tx(org) as c:
            self._reauth(c,org,job,digest)
            j=self._get(c,org,job)
            self._verify_pin(j,spec)
            return {"job":self._public(j),"dispatch_authorized":False,"provider_contacted":False}

    def readiness(self,token,spec:Readiness):
        org,job,digest=self._identity(token)
        with self._tx(org) as c:
            self._reauth(c,org,job,digest)
            j=self._get(c,org,job)
            self._verify_pin(j,spec)
            now=self.clock()
            previous=j.get("worker_readiness")
            # A bounded latest-only receipt; no event history or source data.
            # Coalesce all reports within 30 seconds. Changes are observations,
            # never lease cancellation or execution authorization.
            if previous is None or now-previous["observed_at"]>=30:
                j["worker_readiness"]={"code":spec.code,"observed_at":now,
                                       "profile_revision":spec.profile_revision,
                                       "independently_verified":False}
                self._put(c,j)
            return {"job":self._public(j),"recorded":j.get("worker_readiness"),
                    "dispatch_authorized":False}

    def claim(self,token,spec:Claim):
        org,job,digest=self._identity(token)
        with self._tx(org) as c:
            self._reauth(c,org,job,digest)
            j=self._get(c,org,job);now=self.clock();self._expire(c,j,now)
            if (spec.profile_id,spec.profile_revision,spec.profile_sha256)!=(j["profile_id"],j["profile_revision"],j["profile_sha256"]):
                raise JobError("worker_profile_pin_mismatch",403)
            if not j["enabled"] or j["lease"] or j["next_due_at"]>now:
                return {"claimed":False,"job":self._public(j)}
            lease_id=secrets.token_hex(32)
            j["lease"]={"hash":token_hash(lease_id),"expires_at":now+LEASE_SECONDS,"revision":j["revision"]}
            self._put(c,j);self._history(c,j,"claimed",{"revision":j["revision"]});self._put(c,j)
            self._audit(c,j,job,"claimed",{"revision":j["revision"]})
            return {"claimed":True,"job":self._public(j),"lease_id":lease_id}

    def complete(self,token,spec:Receipt):
        org,job,digest=self._identity(token)
        receipt_digest=canonical_digest(spec.model_dump(exclude={"lease_id"}))
        lease_hash=token_hash(spec.lease_id)
        with self._tx(org) as c:
            self._reauth(c,org,job,digest)
            j=self._get(c,org,job);now=self.clock();self._expire(c,j,now)
            old=j["last_completion"]
            if old and hmac.compare_digest(old["lease_hash"],lease_hash):
                if not hmac.compare_digest(old["receipt_sha256"],receipt_digest):
                    raise JobError("conflicting_completion_replay")
                return {"accepted":True,"replayed":True,"job":self._public(j)}
            lease=j["lease"]
            if not j["enabled"] or not lease or spec.job_revision!=j["revision"] or not hmac.compare_digest(lease["hash"],lease_hash):
                raise JobError("lease_expired_or_invalidated")
            if spec.outcome=="succeeded":
                prior=(j.get("last_success_checkpoint"))
                new=datetime.fromisoformat(spec.checkpoint)
                if new.timestamp()>now+300 or prior and new<=datetime.fromisoformat(prior):
                    raise JobError("checkpoint_not_monotonic_or_future")
                j.update(last_success_at=now,last_success_checkpoint=spec.checkpoint,consecutive_failures=0)
            elif spec.outcome=="failed":
                j["consecutive_failures"]+=1
            delay=j["interval_minutes"]*60
            if spec.outcome=="failed":
                delay=min(86400,delay*2**(j["consecutive_failures"]-1))
            j.update(lease=None,next_due_at=now+max(delay,spec.retry_after_seconds))
            if spec.outcome=="blocked":
                j.update(enabled=False,paused_reason="worker_uncertain" if spec.code=="local_run_uncertain" else spec.code)
            elif j["consecutive_failures"]>=MAX_FAILURES:
                j.update(enabled=False,paused_reason="repeated_failures")
            data=spec.model_dump(exclude={"lease_id"})
            j["last_receipt"]=self._history(c,j,"completed",data)
            j["last_completion"]={"lease_hash":lease_hash,"receipt_sha256":receipt_digest}
            self._put(c,j);self._audit(c,j,job,"completed",{"outcome":spec.outcome,"code":spec.code,"events_added":spec.events_added})
            return {"accepted":True,"replayed":False,"job":self._public(j)}
