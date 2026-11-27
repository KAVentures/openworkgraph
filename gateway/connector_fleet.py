"""Organization-scoped operational fleet review and atomic dispatch changes.

No provider requests, event payload reads, or automatic worker cancellation.
Use the same organization transaction as claims, completion and publication.
"""
from collections import Counter, defaultdict
from datetime import datetime
import math
import json
import re
from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from .connector_jobs import Strict, JobError, ID, JOB, DIGEST, MAX_JOBS, canonical_digest
from shared.enterprise_connector_contract import PROVIDERS

FORMAT="openworkgraph.connector-fleet.v1"
JobID=Annotated[str,Field(pattern=JOB)]
AMBIGUOUS={"expired_lease_requires_reconciliation","cancelled_active_lease","worker_uncertain"}
MAX_BULK=100


class Selection(Strict):
    job_ids:list[JobID]=Field(min_length=1,max_length=MAX_BULK)
    action:Literal["pause","resume"]
    allow_active_lease_cancellation:StrictBool=False

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.job_ids))!=len(self.job_ids):
            raise ValueError("duplicate job selection")
        if self.action=="resume" and self.allow_active_lease_cancellation:
            raise ValueError("cannot cancel leases when resuming")
        return self


class Apply(Selection):
    expected_plan_sha256:str=Field(pattern=DIGEST)
    confirm_exact_dispatch_changes:StrictBool
    confirm_active_lease_uncertainty:StrictBool=False


def _organization(org):
    if not isinstance(org,str) or not re.fullmatch(ID,org):
        raise JobError("invalid_organization",422)


def _read(jobs,c,org):
    result=jobs._sql(c,"SELECT definition FROM connector_jobs WHERE organization_id=? ORDER BY job_id LIMIT ?",
                     org,MAX_JOBS+1).fetchall()
    if len(result)>MAX_JOBS:
        raise JobError("connector_fleet_budget_exceeded")
    return [json.loads(row[0]) for row in result]


def _publication_rows(jobs,c,org):
    # No payload bytes, manifests' event rows or credentials enter this view.
    # Retain exact grant/revision internally for the reviewed plan fence.
    result=jobs._sql(c,"""SELECT job_id, definition,
                    CASE WHEN payload IS NULL THEN 0 ELSE 1 END
                    FROM connector_event_publications WHERE organization_id=?
                    ORDER BY job_id LIMIT ?""",org,MAX_JOBS+1).fetchall()
    if len(result)>MAX_JOBS:
        raise JobError("connector_fleet_publication_budget_exceeded")
    return {job:(json.loads(definition),bool(published)) for job,definition,published in result}


def _publication(j,record,now):
    if record is None:
        return {"state":"none","event_count":None,"expires_at":None,
                "upload_pin_current":False}
    d,published=record
    current=d["job_revision"]==j["revision"]
    expired=d["expires_at"]<=now
    # A retained published snapshot may remain readable after job revision
    # changes; the upload grant itself cannot be reused.
    state="expired" if expired else "published" if published else "approved"
    return {"state":state,"event_count":d["manifest"]["event_count"],"expires_at":d["expires_at"],
            "upload_pin_current":current and not expired,
            "publication_revision_matches_job":current}


def _observed(j,now):
    lease=j["lease"]
    if lease:
        return "expired_lease" if lease["expires_at"]<=now else "leased"
    if not j["enabled"]:
        return "paused"
    return "due" if j["next_due_at"]<=now else "waiting"


def _time(value):
    if not value:
        return None
    try:
        dt=datetime.fromisoformat(value)
        return dt.timestamp() if dt.utcoffset() is not None else None
    except (ValueError,TypeError,OverflowError,OSError):
        return None


def _row(j,publication,now):
    state=_observed(j,now)
    readiness=j.get("worker_readiness")
    fresh=bool(readiness and 0<=now-readiness["observed_at"]<=120)
    last=j["last_success_at"]
    checkpoint=_time(j.get("last_success_checkpoint"))
    last_valid=type(last) in (int,float) and math.isfinite(last) and 0<=last<=now
    checkpoint_valid=checkpoint is not None and math.isfinite(checkpoint) and checkpoint<=now
    interval=j["interval_minutes"]*60
    flags=[]
    if state=="expired_lease" or j["paused_reason"] in AMBIGUOUS:
        flags.append("local_outcome_requires_reconciliation")
    if j["paused_reason"] and j["paused_reason"]!="operator_paused" and j["paused_reason"] not in AMBIGUOUS:
        flags.append("paused_for_review")
    if j["consecutive_failures"]:
        flags.append("consecutive_reported_failures")
    if j["enabled"] and not fresh:
        flags.append("worker_readiness_missing_or_stale")
    if j["enabled"] and fresh and readiness["code"]!="ready":
        flags.append("worker_reported_blocker")
    overdue=max(0,now-j["next_due_at"]) if state=="due" else 0
    if overdue>=interval:
        flags.append("dispatch_overdue_by_interval")
    if j["enabled"] and last is None:
        flags.append("no_success_report")
    elif j["enabled"] and not last_valid:
        flags.append("success_report_clock_inconsistent")
    elif j["enabled"] and now-last>max(3600,2*interval):
        flags.append("success_report_older_than_threshold")
    if j.get("last_success_checkpoint") is not None and not checkpoint_valid:
        flags.append("checkpoint_clock_inconsistent")
    pub=_publication(j,publication,now)
    if pub["state"] in ("approved","published") and not pub["upload_pin_current"]:
        flags.append("publication_upload_pin_changed")
    if pub["state"] in ("approved","published") and pub["expires_at"]-now<=3600:
        flags.append("publication_expiring_within_hour")
    priority="urgent" if "local_outcome_requires_reconciliation" in flags else (
        "review" if flags else "normal")
    return {"job_id":j["job_id"],"provider":j["provider"],"revision":j["revision"],
            "observed_state":state,"enabled":j["enabled"],"pause_reason":j["paused_reason"],
            "next_due_at":j["next_due_at"],"overdue_seconds":overdue,
            "lease_expires_at":j["lease"]["expires_at"] if j["lease"] else None,
            "last_success_report_at":last if last_valid else None,
            "checkpoint_age_seconds":now-checkpoint if checkpoint_valid else None,
            "consecutive_failures":j["consecutive_failures"],"readiness_code":readiness["code"] if readiness else None,
            "readiness_fresh":fresh,"publication":pub,"review_flags":flags,"priority":priority}


def report(jobs,org,*,support_only=False):
    _organization(org)
    with jobs._tx(org) as c:
        now=jobs.clock()
        data=_read(jobs,c,org)
        pubs=_publication_rows(jobs,c,org)
        rows=[_row(j,pubs.get(j["job_id"]),now) for j in data]
        by_provider=defaultdict(Counter)
        for provider in PROVIDERS:
            by_provider[provider]["jobs"]=0
            by_provider[provider]["requires_review"]=0
        flags=Counter()
        states=Counter();priorities=Counter();publications=Counter()
        for row in rows:
            flags.update(row["review_flags"])
            states[row["observed_state"]]+=1
            priorities[row["priority"]]+=1
            publications[row["publication"]["state"]]+=1
            by_provider[row["provider"]]["jobs"]+=1
            by_provider[row["provider"]][row["observed_state"]]+=1
            by_provider[row["provider"]]["requires_review"]+=row["priority"]!="normal"
        rank={"urgent":0,"review":1,"normal":2}
        rows.sort(key=lambda r:(rank[r["priority"]],-r["overdue_seconds"],r["job_id"]))
        result={"format":FORMAT,"organization_id":org,"observed_at":now,
                "summary":{"jobs":len(rows),"states":dict(sorted(states.items())),
                           "priorities":dict(sorted(priorities.items())),
                           "publication_states":dict(sorted(publications.items())),
                           "review_flags":dict(sorted(flags.items())),
                           "jobs_with_fresh_worker_report":sum(r["readiness_fresh"] for r in rows),
                           "reported_published_event_count":sum(r["publication"]["event_count"] or 0
                               for r in rows if r["publication"]["state"]=="published")},
                "providers":[{"provider":key,**dict(sorted(value.items()))} for key,value in sorted(by_provider.items())],
                "worklist":rows,"counts_only":True,"scope":"owner_worklist","provider_contacted":False,
                "provider_health_verified":False,"event_payloads_read":False,
                "lease_state_mutated":False,"snapshot_payload_integrity_verified":False}
        if support_only:
            # A separate server projection: no individual job/organization IDs,
            # per-job timestamps, pause codes or publication grant metadata.
            result.pop("organization_id")
            result.pop("worklist")
            result["scope"]="aggregate_support"
        return result


def _decision(j,spec,now):
    state=_observed(j,now)
    if state=="expired_lease":
        return "blocked","expired_lease_requires_individual_reconciliation"
    if spec.action=="resume":
        if j["enabled"]:
            return "blocked","already_enabled"
        if j["lease"] or j["paused_reason"]!="operator_paused":
            return "blocked","only_unambiguous_operator_pauses_can_bulk_resume"
        return "eligible",None
    if not j["enabled"]:
        return "blocked","already_paused"
    if j["lease"] and not spec.allow_active_lease_cancellation:
        return "blocked","active_lease_requires_separate_cancellation_approval"
    return "eligible",None


def _plan(jobs,c,org,spec,now):
    rows=[];pins=[];selected=[]
    pubs=_publication_rows(jobs,c,org)
    for job_id in sorted(spec.job_ids):
        j=jobs._get(c,org,job_id)
        publication=pubs.get(job_id)
        decision,reason=_decision(j,spec,now)
        pub=_publication(j,publication,now)
        rows.append({"job_id":job_id,"provider":j["provider"],"revision":j["revision"],
                     "enabled":j["enabled"],"decision":decision,"reason":reason,
                     "active_lease":bool(j["lease"]),"lease_expires_at":j["lease"]["expires_at"] if j["lease"] else None,
                     "publication_state":pub["state"],
                     "publication_upload_pin_will_change":decision=="eligible" and pub["upload_pin_current"]})
        # Sequence detects intervening claims/completions even when job
        # revision is unchanged. Readiness reports are not dispatch state.
        # The private grant identity is hashed, never returned as plan data.
        pins.append({"job_id":job_id,"revision":j["revision"],"sequence":j["sequence"],
                     "enabled":j["enabled"],"paused_reason":j["paused_reason"],
                     "lease_expires_at":j["lease"]["expires_at"] if j["lease"] else None,
                     "decision":decision,"reason":reason,
                     "publication_pin":None if not publication else {
                         "grant_id":publication[0]["grant_id"],"revision":publication[0]["revision"],
                         "published":publication[1],"expired":pub["state"]=="expired",
                         "manifest_sha256":publication[0]["manifest_sha256"]}})
        selected.append(j)
    plan={"format":FORMAT,"organization_id":org,"action":spec.action,
          "allow_active_lease_cancellation":spec.allow_active_lease_cancellation,"pins":pins}
    return selected,{"format":FORMAT,"organization_id":org,"action":spec.action,
                     "plan_sha256":canonical_digest(plan),"jobs":rows,
                     "eligible_count":sum(r["decision"]=="eligible" for r in rows),
                     "blocked_count":sum(r["decision"]=="blocked" for r in rows),
                     "active_leases_to_cancel":sum(r["decision"]=="eligible" and r["active_lease"] for r in rows),
                     "current_publication_upload_pins_to_invalidate":sum(r["publication_upload_pin_will_change"] for r in rows),
                     "provider_contacted":False,"dispatch_changed":False}


def preview(jobs,org,spec):
    _organization(org)
    with jobs._tx(org) as c:
        _,plan=_plan(jobs,c,org,spec,jobs.clock())
        return plan


def apply(jobs,org,spec,principal):
    _organization(org)
    if not spec.confirm_exact_dispatch_changes:
        raise JobError("explicit_bulk_dispatch_confirmation_required",403)
    with jobs._tx(org) as c:
        now=jobs.clock()
        selected,plan=_plan(jobs,c,org,spec,now)
        if plan["plan_sha256"]!=spec.expected_plan_sha256:
            raise JobError("connector_fleet_plan_changed")
        if plan["blocked_count"]:
            raise JobError("connector_fleet_selection_contains_blocked_jobs")
        if plan["active_leases_to_cancel"] and not spec.confirm_active_lease_uncertainty:
            raise JobError("explicit_active_lease_uncertainty_required",403)
        results=[]
        for j in selected:
            cancelled=bool(j["lease"])
            if spec.action=="resume":
                j.update(enabled=True,paused_reason=None,consecutive_failures=0,
                         next_due_at=max(now,j["next_due_at"]))
            else:
                j.update(enabled=False,paused_reason="cancelled_active_lease" if cancelled else "operator_paused",lease=None)
            j["revision"]+=1
            details={"revision":j["revision"],"reason":j["paused_reason"],"fleet_plan_sha256":plan["plan_sha256"]}
            jobs._history(c,j,"enabled" if spec.action=="resume" else "paused",details)
            jobs._put(c,j)
            jobs._audit(c,j,principal,"fleet_dispatch_changed",{"revision":j["revision"],
                "enabled":j["enabled"],"active_lease_invalidated":cancelled,"fleet_plan_sha256":plan["plan_sha256"]})
            results.append({"job_id":j["job_id"],"revision":j["revision"],"enabled":j["enabled"],
                            "pause_reason":j["paused_reason"],"next_due_at":j["next_due_at"]})
        return {"format":FORMAT,"organization_id":org,"plan_sha256":plan["plan_sha256"],
                "action":spec.action,"changed_count":len(results),"jobs":results,
                "active_leases_invalidated":plan["active_leases_to_cancel"],
                "provider_contacted":False,"already_dispatched_local_reads_recalled":False,
                "published_snapshots_deleted":False,"dispatch_changed":True}
