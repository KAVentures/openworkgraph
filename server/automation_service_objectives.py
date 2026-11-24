"""Reported-run service objectives with explicit unresolved denominator bounds.

A retrospective cohort is reconstructed only from the currently retained
latest report of each run. It cannot reconstruct a historical reporting state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from . import automation_monitoring as monitoring

FORMAT = "openworkgraph.automation-service-objectives.v1"
MAX_RUNS = 200_000
MAX_BUCKETS = 90
STATES = ("met", "missed", "pending", "unknown")


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: str = Field(max_length=64)
    end: str = Field(max_length=64)
    as_of: str = Field(max_length=64)
    deadline_seconds: int = Field(ge=1, le=30*86400)
    target_basis_points: int = Field(ge=1, le=10000)
    require_reported_verified_outcome: StrictBool = False
    exact_spec_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    status: Literal["met", "missed", "pending", "unknown"] | None = None
    expected_population_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    offset: int = Field(default=0, ge=0, le=MAX_RUNS)
    limit: int = Field(default=30, ge=1, le=100)


def _parse(value):
    try:
        spec = Spec.model_validate(value)
    except ValidationError:
        raise monitoring.MonitoringError("invalid bounded service objective settings") from None
    start,end,as_of=(monitoring._time(getattr(spec,k),k) for k in ("start","end","as_of"))
    if not start<end<=as_of:
        raise monitoring.MonitoringError("require start < end <= observation cutoff")
    if end-start>timedelta(days=90):
        raise monitoring.MonitoringError("service objective cohort is limited to 90 days")
    if as_of>datetime.now(timezone.utc).replace(tzinfo=None):
        raise monitoring.MonitoringError("observation cutoff must not be in the future")
    if spec.offset and spec.expected_population_sha256 is None:
        raise monitoring.MonitoringError("restart evidence from page one")
    return spec,start,end,as_of


def _classify(row,cutoff,deadline,require_outcome,exact_spec):
    ident,start,finish,status,reported,source,spec_hash=row
    age=(cutoff-start).total_seconds()
    # A future finish is not available at this cutoff. Since the retained
    # record may itself have arrived later, this is not a historical replay.
    if exact_spec is not None and spec_hash!=exact_spec:
        return ("unknown","unattributed_spec" if spec_hash is None else "different_spec")
    if finish is not None and finish>cutoff:
        return ("missed","deadline_elapsed_before_recorded_finish") if age>deadline else ("pending","finish_after_cutoff")
    if status=="running":
        return ("missed","running_past_deadline") if age>deadline else ("pending","running_within_deadline")
    if finish is None:
        # Without an end timestamp we cannot determine whether a terminal
        # report belongs before the requested cutoff.
        return "unknown","terminal_timestamp_missing"
    elapsed=(finish-start).total_seconds()
    if elapsed>deadline:
        return "missed","reported_duration_exceeds_deadline"
    if status!="succeeded":
        return "missed","reported_"+status
    if require_outcome and reported is not True:
        return ("unknown","reported_outcome_missing") if reported is None else ("missed","reported_outcome_not_verified")
    return "met","reported_success_within_deadline"


def _summary(counts,target):
    n=sum(counts.values())
    met,missed=counts["met"],counts["missed"]
    unresolved=counts["pending"]+counts["unknown"]
    required=(n*target+9999)//10000
    # Integer counts provide exact comparisons, including non-integral
    # permitted error budgets. Unknown and pending runs are never dropped.
    permitted=n-required
    lower=met/n if n else None
    upper=(met+unresolved)/n if n else None
    verdict=("no_runs" if not n else "target_met_even_if_unresolved_fail" if met>=required else
             "target_missed_even_if_unresolved_succeed" if met+unresolved<required else "unresolved")
    return {"runs":n,"counts":dict(counts),"required_met_runs":required,
            "permitted_misses":permitted,"known_misses":missed,
            "remaining_error_budget_after_known_misses":permitted-missed,
            "remaining_error_budget_if_unresolved_miss":permitted-missed-unresolved,
            "lower_met_fraction":lower,"upper_met_fraction":upper,"verdict":verdict}


def _digest_header(spec,deployment):
    settings=spec.model_dump(exclude={"offset","limit","expected_population_sha256","status"})
    # Status filtering doesn't change the underlying cohort or objective.
    header={"deployment_id":deployment["deployment_id"],"settings":settings,
            "design_sha256":deployment["design_sha256"],"reported_spec_sha256":deployment["reported_spec_sha256"]}
    return sha256(json.dumps(header,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode())


def analyze(deployment_id,value):
    spec,start,end,cutoff=_parse(value)
    with monitoring._LOCK:
        conn=monitoring._connect()
        try:
            deployment=monitoring._check(conn,deployment_id)
            count=conn.execute("""SELECT count(*) FROM runs WHERE deployment_id=?
                AND started_at>=? AND started_at<?""",[deployment_id,start,end]).fetchone()[0]
            if count>MAX_RUNS:
                raise monitoring.MonitoringError("service objective cohort exceeds retained run bound; narrow dates")
            rows=conn.execute("""SELECT run_id,started_at,finished_at,status,outcome_reported_verified,
                    outcome_source,reported_spec_sha256 FROM runs WHERE deployment_id=?
                    AND started_at>=? AND started_at<? ORDER BY started_at,run_id""",
                    [deployment_id,start,end]).fetchall()
        finally:
            conn.close()
    digest=_digest_header(spec,deployment)
    counts=dict.fromkeys(STATES,0)
    reasons={}
    buckets={}
    page=[]
    total=0
    for row in rows:
        ident,began,finished,status,verified,source,spec_hash=row
        state,reason=_classify(row,cutoff,spec.deadline_seconds,
                               spec.require_reported_verified_outcome,spec.exact_spec_sha256)
        digest.update(json.dumps([ident,monitoring._iso(began),monitoring._iso(finished),
                     status,verified,source,spec_hash],ensure_ascii=True,separators=(",",":")).encode()+b"\n")
        counts[state]+=1
        reasons[reason]=reasons.get(reason,0)+1
        # Bucket from exact cohort start, not local time or arbitrary calendar
        # day boundaries; partial last bucket is marked in the report.
        index=(began-start)//timedelta(days=1)
        bucket=buckets.setdefault(index,dict.fromkeys(STATES,0))
        bucket[state]+=1
        if spec.status is None or state==spec.status:
            if spec.offset<=total<spec.offset+spec.limit:
                page.append({"run_id":ident,"started_at":monitoring._iso(began),
                    "finished_at":monitoring._iso(finished),"reported_status":status,
                    "reported_outcome_verified":verified,"reported_spec_sha256":spec_hash,
                    "objective_status":state,"reason":reason})
            total+=1
    population=digest.hexdigest()
    if spec.expected_population_sha256 is not None and population!=spec.expected_population_sha256:
        raise monitoring.MonitoringError("retained run cohort changed; restart service objective review")
    daily=[]
    for index in range((end-start+timedelta(days=1)-timedelta(microseconds=1))//timedelta(days=1)):
        if index>=MAX_BUCKETS:
            raise monitoring.MonitoringError("service objective bucket bound exceeded")
        lower=start+timedelta(days=index)
        upper=min(end,lower+timedelta(days=1))
        daily.append({"start":monitoring._iso(lower),"end":monitoring._iso(upper),
            **_summary(buckets.get(index,dict.fromkeys(STATES,0)),spec.target_basis_points)})
    return {"format":FORMAT,"deployment_id":deployment_id,"population_sha256":population,
            "settings":spec.model_dump(exclude={"offset","limit","expected_population_sha256"}),
            "summary":_summary(counts,spec.target_basis_points),"reason_counts":dict(sorted(reasons.items())),
            "daily_start_cohorts":daily,"matching_runs":total,"offset":spec.offset,
            "next_offset":spec.offset+len(page) if spec.offset+len(page)<total else None,
            "rows":page,"complete_for_retained_cohort":True,
            "historical_reporting_state_reconstructed":False,
            "report_arrival_times_known":False,"source_population_complete_verified":False,
            "independent_business_outcomes_verified":False,"execution_authorized":False,
            "interpretation":"Bounds cover retained run starts, not all production traffic. A run must report success by the deadline and optionally report a verified outcome. Unknown and pending runs stay in the denominator. Retrospective results use current retained reports, not historical knowledge."}
