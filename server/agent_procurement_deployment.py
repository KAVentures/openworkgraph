"""Read-only R26 bridge to exact retained R18 release and monitoring evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from shared.agent_procurement import Alias, Strict, digest
from shared.agent_procurement_outcomes import decide as outcome_decide
from shared.agent_procurement_trials import Bps
from shared.process_shadow_review import Hash
from . import automation_monitoring as monitoring
from . import automation_release_inventory as releases

DeploymentId=Annotated[str,Field(pattern=r"^dep_[0-9a-f]{16}$")]
Timestamp=Annotated[str,Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$",max_length=40)]
MAX_RUNS=50000
MAX_STEPS=2000000


def _time(value):
    return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _iso(value):
    return value.isoformat()+"Z" if value is not None else None


class Policy(Strict):
    minimum_baseline_runs: Annotated[StrictInt,Field(ge=1,le=MAX_RUNS)] = 20
    minimum_candidate_runs: Annotated[StrictInt,Field(ge=1,le=MAX_RUNS)] = 20
    minimum_candidate_success_basis_points: Bps = 9500
    maximum_candidate_failure_basis_points: Bps = 0
    maximum_candidate_handoff_basis_points: Bps = 500
    maximum_success_drop_basis_points: Bps = 0
    maximum_candidate_p95_ratio_basis_points: Annotated[StrictInt,Field(ge=0,le=100000)] | None = None
    require_all_terminal_durations: StrictBool = True


class DeploymentPin(Strict):
    deployment_id: DeploymentId
    release_revision: Annotated[StrictInt,Field(ge=1,le=releases.MAX_REVISIONS)]
    manifest_sha256: Hash


class RollbackPlan(Strict):
    format: Literal["openworkgraph.procurement-rollback-plan.v1"]
    baseline: DeploymentPin
    candidate: DeploymentPin
    baseline_configuration_sha256: Hash
    candidate_configuration_sha256: Hash
    owner_alias: Alias
    stop_candidate_before_restore: StrictBool
    reconcile_inflight_work_before_restore: StrictBool
    preserve_evidence: StrictBool
    require_separate_execution_authorization: StrictBool

    @model_validator(mode="after")
    def valid(self):
        if self.baseline.deployment_id==self.candidate.deployment_id or not all((
            self.stop_candidate_before_restore,self.reconcile_inflight_work_before_restore,
            self.preserve_evidence,self.require_separate_execution_authorization)):
            raise ValueError("rollback targets and safety prerequisites required")
        return self


class Request(Strict):
    expected_rollout_receipt_sha256: Hash
    baseline: DeploymentPin
    candidate: DeploymentPin
    window_start: Timestamp
    window_end: Timestamp
    policy: Policy

    @model_validator(mode="after")
    def valid(self):
        if self.baseline.deployment_id==self.candidate.deployment_id:
            raise ValueError("select separate deployments")
        start,end=_time(self.window_start),_time(self.window_end)
        if not timedelta(0)<end-start<=timedelta(days=90):
            raise ValueError("select a common window of at most 90 days")
        return self


class Decision(Strict):
    decision: Literal["refer_to_release_owner","retain_baseline","defer"]
    reviewer_alias: Alias
    expected_deployment_review_sha256: Hash
    confirmed: StrictBool


def _seal(value,key):
    return {**value,key:digest(value)}


def _verify_rollout(packet,bundle,req,rollback):
    if not isinstance(bundle,dict) or set(bundle)!={"decision_bundle","preflight","outcome_plan","observations","request"}:
        raise ValueError("exact original rollout bundle required")
    result=outcome_decide(packet,bundle["decision_bundle"],bundle["preflight"],bundle["outcome_plan"],bundle["observations"],bundle["request"])
    if result["receipt_sha256"]!=req.expected_rollout_receipt_sha256 or not result["separate_rollout_review_requested"]:
        raise ValueError("exact positive rollout-review receipt required")
    if digest(rollback.model_dump())!=result["request"]["rollback_plan_sha256"]:
        raise ValueError("rollback document differs from prior pinned hash")
    pf=bundle["preflight"]
    for arm in ("baseline","candidate"):
        pin=getattr(req,arm)
        binding=pf["artifact_bindings"][arm]
        if (pin!=getattr(rollback,arm) or pin.manifest_sha256!=binding["manifest_sha256"] or
                getattr(rollback,arm+"_configuration_sha256")!=binding["configuration_sha256"]):
            raise ValueError("rollback/deployment targets differ from artifact and configuration pins")
    return result,pf


def _hash_add(h,row):
    data=json.dumps(row,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()
    h.update(len(data).to_bytes(8,"big"));h.update(data)


def _snapshot(conn,pin,arm,spec,reference,start,end):
    info=monitoring._check(conn,pin.deployment_id)
    inv=releases._view(conn,pin.deployment_id)
    current=inv["current"]
    if not current or inv["revision"]!=pin.release_revision or current["artifact_sha256"]!=pin.manifest_sha256:
        raise ValueError("release revision or declared artifact changed")
    stages=("pilot_reported","production_reported") if arm=="baseline" else ("shadow_reported","pilot_reported")
    if current["stage"] not in stages:
        raise ValueError("invalid reported stage for selected arm")
    if (info["reported_spec_sha256"]!=spec or info["design_sha256"]!=spec or
        current["reported_spec_sha256"]!=spec or current["design_sha256"]!=spec or
        not isinstance(info["design_graph"],dict) or info["design_graph"].get("process_reference")!=reference):
        raise ValueError("deployment does not bind exact evaluated specification")
    rows=conn.execute("""SELECT run_id,started_at,finished_at,status,outcome_reported_verified,
        outcome_source,reported_spec_sha256 FROM runs WHERE deployment_id=? AND started_at>=? AND started_at<?
        ORDER BY run_id LIMIT ?""",[pin.deployment_id,start,end,MAX_RUNS+1]).fetchall()
    if len(rows)>MAX_RUNS:raise ValueError("monitoring window exceeds run capacity")
    cursor=conn.execute("""SELECT s.run_id,s.seq,s.step_id,s.activity,s.status,s.observed_at
        FROM steps s JOIN runs r ON r.deployment_id=s.deployment_id AND r.run_id=s.run_id
        WHERE s.deployment_id=? AND r.started_at>=? AND r.started_at<?
        ORDER BY s.run_id,s.seq,s.step_id,s.activity,s.status,s.observed_at""",[pin.deployment_id,start,end])
    steps_hash=hashlib.sha256();steps=0
    while chunk:=cursor.fetchmany(1000):
        for rid,seq,step,activity,status,at in chunk:
            steps+=1
            if steps>MAX_STEPS:raise ValueError("monitoring window exceeds step capacity")
            _hash_add(steps_hash,[rid,seq,step,activity,status,_iso(at)])
    counts={s:0 for s in monitoring.RUN_STATUSES}
    run_hash=hashlib.sha256()
    for rid,started,finished,status,verified,source,attribution in rows:
        counts[status]+=1
        _hash_add(run_hash,[rid,_iso(started),_iso(finished),status,verified,source,attribution])
    attributed=sum(row[6]==spec for row in rows)
    durations=sorted(int((r[2]-r[1]).total_seconds()*1000) for r in rows if r[3]!="running" and r[2] is not None and r[6]==spec)
    terminal=len(rows)-counts["running"]
    p95=durations[(95*len(durations)+99)//100-1] if durations and len(durations)==terminal else None
    return {"deployment_id":pin.deployment_id,"release_revision":inv["revision"],"reported_stage":current["stage"],
            "manifest_sha256_declared_in_inventory":current["artifact_sha256"],"release_record_sha256":digest(current),
            "runs":len(rows),"unattributed_runs":len(rows)-attributed,"status_counts":counts,
            "attributed_succeeded":sum(r[3]=="succeeded" and r[6]==spec for r in rows),
            "attributed_failed":sum(r[3]=="failed" and r[6]==spec for r in rows),
            "attributed_handed_to_human":sum(r[3]=="handed_to_human" and r[6]==spec for r in rows),
            "terminal_runs":terminal,"duration_observations":len(durations),"p95_elapsed_ms":p95,
            "run_population_sha256":run_hash.hexdigest(),"step_population_sha256":steps_hash.hexdigest(),"step_count":steps}


def inspect(packet,rollout_bundle,request,rollback_document):
    req=Request.model_validate(request);rollback=RollbackPlan.model_validate(rollback_document)
    receipt,pf=_verify_rollout(packet,rollout_bundle,req,rollback)
    start,end=_time(req.window_start),_time(req.window_end)
    if end>datetime.now(timezone.utc).replace(tzinfo=None):raise ValueError("window must end in past")
    spec=pf["evaluated_spec_sha256"];ref=receipt["process_reference"]
    with monitoring._LOCK:
        conn=monitoring._connect()
        try:
            b=_snapshot(conn,req.baseline,"baseline",spec,ref,start,end)
            c=_snapshot(conn,req.candidate,"candidate",spec,ref,start,end)
        finally:conn.close()
    p=req.policy
    def lower(count,n,limit):return "unknown" if not n else "pass" if count*10000>=n*limit else "fail"
    def upper(count,n,limit):return "unknown" if not n else "pass" if count*10000<=n*limit else "fail"
    def ratio():
        if p.maximum_candidate_p95_ratio_basis_points is None:return "not_required"
        if b["p95_elapsed_ms"] is None or c["p95_elapsed_ms"] is None:return "unknown"
        return "pass" if c["p95_elapsed_ms"]*10000<=b["p95_elapsed_ms"]*p.maximum_candidate_p95_ratio_basis_points else "fail"
    checks={
        "baseline_run_count":"pass" if b["runs"]>=p.minimum_baseline_runs else "fail",
        "candidate_run_count":"pass" if c["runs"]>=p.minimum_candidate_runs else "fail",
        "complete_exact_spec_attribution":"pass" if not b["unattributed_runs"] and not c["unattributed_runs"] else "unknown",
        "no_unfinished_runs":"pass" if not b["status_counts"]["running"] and not c["status_counts"]["running"] else "unknown",
        "candidate_reported_success":lower(c["attributed_succeeded"],c["runs"],p.minimum_candidate_success_basis_points),
        "candidate_reported_failure":upper(c["attributed_failed"],c["runs"],p.maximum_candidate_failure_basis_points),
        "candidate_reported_handoff":upper(c["attributed_handed_to_human"],c["runs"],p.maximum_candidate_handoff_basis_points),
        "success_drop":"unknown" if not b["runs"] or not c["runs"] else "pass" if
            (b["attributed_succeeded"]*c["runs"]-c["attributed_succeeded"]*b["runs"])*10000<=p.maximum_success_drop_basis_points*b["runs"]*c["runs"] else "fail",
        "complete_terminal_durations":"pass" if not p.require_all_terminal_durations or all(
            s["terminal_runs"] and s["duration_observations"]==s["terminal_runs"] for s in (b,c)) else "unknown",
        "p95_ratio":ratio(),"candidate_pilot_reported":"pass" if c["reported_stage"]=="pilot_reported" else "fail",
    }
    return _seal({"format":"openworkgraph.procurement-deployment-review.v1","process_reference":ref,"spec_sha256":spec,
           "rollout_receipt_sha256":receipt["receipt_sha256"],"rollback_plan_sha256":digest(rollback.model_dump()),
           "rollback_owner_alias":rollback.owner_alias,"prior_reviewer_alias":receipt["request"]["reviewer_alias"],
           "window":{"from":_iso(start),"until_exclusive":_iso(end)},"policy":p.model_dump(),
           "baseline":b,"candidate":c,"checks":checks,
           "eligible_for_release_owner_referral":all(v in ("pass","not_required") for v in checks.values()),
           "rollback_targets_match_retained_release_inventory":True,"rollback_procedure_tested":False,
           "runtime_configuration_independently_verified":False,"reported_runs_independently_authenticated":False,
           "pilot_populations_comparable":False,"business_outcomes_verified":False,
           "deployment_changed":False,"execution_authorized":False,"production_rollout_authorized":False},
           "deployment_review_sha256")


def decide(packet,rollout_bundle,request,rollback_document,decision):
    req=Decision.model_validate(decision)
    value=inspect(packet,rollout_bundle,request,rollback_document)
    if not req.confirmed or req.expected_deployment_review_sha256!=value["deployment_review_sha256"]:
        raise ValueError("monitoring or release evidence changed; inspect again")
    if req.reviewer_alias.casefold() in (value["rollback_owner_alias"].casefold(),value["prior_reviewer_alias"].casefold()):
        raise ValueError("distinct declared reviewer required")
    referral=req.decision=="refer_to_release_owner"
    if referral and not value["eligible_for_release_owner_referral"]:
        raise ValueError("monitoring evidence does not pass policy")
    return _seal({"format":"openworkgraph.procurement-deployment-review-receipt.v1","process_reference":value["process_reference"],
                  "rollout_receipt_sha256":value["rollout_receipt_sha256"],"deployment_review_sha256":value["deployment_review_sha256"],
                  "rollback_plan_sha256":value["rollback_plan_sha256"],"baseline_deployment_id":value["baseline"]["deployment_id"],
                  "candidate_deployment_id":value["candidate"]["deployment_id"],
                  "baseline_release_revision":value["baseline"]["release_revision"],"candidate_release_revision":value["candidate"]["release_revision"],
                  "request":req.model_dump(),"release_owner_referral_requested":referral,"reviewer_identity_verified":False,
                  "release_inventory_mutated":False,"deployment_changed":False,"production_rollout_authorized":False,
                  "execution_authorized":False},"receipt_sha256")


def operation(packet,action,bundle):
    keys={"rollout_bundle","request","rollback_document"}
    if action=="deployment-decide":keys.add("decision")
    if action not in ("deployment-review","deployment-decide") or not isinstance(bundle,dict) or set(bundle)!=keys:
        raise ValueError("exact deployment readiness bundle required")
    return (inspect if action=="deployment-review" else decide)(packet,**bundle)
