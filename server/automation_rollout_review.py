"""R18: compare explicitly selected version-attributed rollout cohorts.

One local monitoring snapshot. Reported success is not verified business
success. Unhashed and other-version runs never become selected-version facts.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictFloat, ValidationError

from . import automation_monitoring as m
from . import automation_run_evidence as evidence
from .event_log_quality import _set_digest

FORMAT = "openworkgraph.version-rollout-review.v1"
MAX_RUNS = 50_000
MAX_STEPS = 2_000_000
ROLES = ("baseline", "candidate")
ATTRIBUTIONS = ("exact", "other", "unknown")


class Side(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    deployment_id: str = Field(pattern=evidence.DEP_PATTERN)
    spec_sha256: str = Field(pattern=evidence.HASH_PATTERN)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    baseline: Side
    candidate: Side
    started_from: str = Field(min_length=1, max_length=50)
    started_before: str = Field(min_length=1, max_length=50)
    minimum_attributed_runs: StrictInt = Field(ge=1, le=MAX_RUNS)
    minimum_candidate_success_share: StrictInt | StrictFloat = Field(ge=0, le=1)
    maximum_success_share_decrease: StrictInt | StrictFloat = Field(ge=0, le=1)
    maximum_unknown_attribution_share: StrictInt | StrictFloat = Field(ge=0, le=1)
    evidence_side: Literal["baseline", "candidate"] = "candidate"
    evidence_attribution: Literal["exact", "other", "unknown", "all"] = "exact"
    evidence_status: Literal["succeeded", "failed", "handed_to_human", "cancelled", "running"] | None = None
    expected_population_sha256: str | None = Field(default=None, pattern=evidence.HASH_PATTERN)
    offset: StrictInt = Field(default=0, ge=0, le=MAX_RUNS)
    limit: StrictInt = Field(default=30, ge=1, le=100)


def _q(x):
    return Fraction(str(x))


def _bounds(counts):
    total = sum(counts.values())
    if not total:
        return None
    success = counts["succeeded"]
    return Fraction(success,total), Fraction(success+counts["running"],total)


def _display(bounds):
    return None if bounds is None else {"lower":float(bounds[0]),"upper":float(bounds[1])}


def _attribution_sensitivity(exact, unknown):
    """Sharp possible share if any subset of unhashed runs belongs to version.

    Unknown successes need not belong to the version for a lower bound; unknown
    non-successes need not belong for an upper bound. Running outcomes are
    unrestricted. This is sensitivity only, not a reassignment of run evidence.
    """
    n = sum(exact.values())
    if not n:
        return None
    u = sum(unknown.values())
    s,r = exact["succeeded"], exact["running"]
    us,ur = unknown["succeeded"],unknown["running"]
    return (Fraction(s,n+u-us),
            Fraction(s+r+us+ur,n+us+ur))


def summarize(counts, settings):
    """Compute exact finite-population bounds and separate review gates."""
    rows={}
    exact={}
    for role in ROLES:
        by=counts[role]
        total=sum(sum(x.values()) for x in by.values())
        n=sum(by["exact"].values())
        unknown=sum(by["unknown"].values())
        bounds=_bounds(by["exact"])
        uncertain=_attribution_sensitivity(by["exact"],by["unknown"])
        exact[role]={"n":n,"bounds":bounds,"uncertain":uncertain,
                     "unknown_share":Fraction(unknown,total) if total else None}
        rows[role]={"all_retained_runs_in_window":total,"attributed_runs":n,
                    "other_version_runs":sum(by["other"].values()),"unhashed_runs":unknown,
                    "status_counts_by_attribution":by,
                    "unknown_attribution_share":float(exact[role]["unknown_share"]) if total else None,
                    "attributed_eventual_success_share_bounds":_display(bounds),
                    "possible_version_success_share_with_unhashed_bounds":_display(uncertain),
                    "reported_successes_with_reported_verification_not_independent":True}
    gates=[]
    def count_gate(role):
        n=exact[role]["n"]
        gates.append({"gate":role+"_minimum_attributed_runs",
                      "status":"met" if n>=settings.minimum_attributed_runs else "insufficient",
                      "observed":n,"threshold":settings.minimum_attributed_runs})
    def upper_gate(role):
        share=exact[role]["unknown_share"]
        cap=_q(settings.maximum_unknown_attribution_share)
        gates.append({"gate":role+"_maximum_unknown_attribution_share",
                      "status":"insufficient" if share is None else "met" if share<=cap else "not_met",
                      "observed":float(share) if share is not None else None,"threshold":float(cap)})
    for role in ROLES:
        count_gate(role);upper_gate(role)
    def lower_gate(name,bounds,target):
        gates.append({"gate":name,"status":"insufficient" if bounds is None else
                      "met" if bounds[0]>=target else "not_met" if bounds[1]<target else "unresolved",
                      "observed":_display(bounds),"threshold":float(target)})
    b,c=exact["baseline"],exact["candidate"]
    def delta(key):
        l,r=b[key],c[key]
        return None if l is None or r is None else (r[0]-l[1],r[1]-l[0])
    difference=delta("bounds")
    lower_gate("candidate_minimum_success_share",c["bounds"],_q(settings.minimum_candidate_success_share))
    lower_gate("candidate_minus_baseline_success_share",difference,-_q(settings.maximum_success_share_decrease))
    return {"cohorts":rows,"candidate_minus_baseline_attributed_success_share_bounds":_display(difference),
            "candidate_minus_baseline_with_unhashed_sensitivity_bounds":_display(delta("uncertain")),
            "review_gates":gates,"all_declared_gates_met":all(x["status"]=="met" for x in gates),
            "unhashed_sensitivity_not_used_to_pass_gates":True,
            "bounds_are_confidence_intervals":False,"causal_effect_verified":False}


def review(value):
    try:
        spec=Review.model_validate(value)
    except ValidationError:
        raise m.MonitoringError("invalid bounded rollout evidence request") from None
    if spec.baseline.deployment_id==spec.candidate.deployment_id:
        raise m.MonitoringError("select distinct baseline and candidate deployments")
    if spec.baseline.spec_sha256==spec.candidate.spec_sha256:
        raise m.MonitoringError("select two distinct reported workflow specification digests")
    if spec.offset and not spec.expected_population_sha256:
        raise m.MonitoringError("review page one before requesting later evidence")
    start=m._time(spec.started_from,"started_from")
    end=m._time(spec.started_before,"started_before")
    if start>=end or (end-start).days>366:
        raise m.MonitoringError("choose a positive common time window of at most 366 days")
    with m._LOCK:
        conn=m._connect()
        try:
            sides={role:m._check(conn,getattr(spec,role).deployment_id) for role in ROLES}
            # Select both complete deployment populations, including unknown
            # and other version claims; do not silently discard them.
            conn.execute("""CREATE TEMP TABLE rollout_runs AS
                SELECT r.*, CASE WHEN deployment_id=? THEN 'baseline' ELSE 'candidate' END AS side,
                       CASE WHEN reported_spec_sha256 IS NULL THEN 'unknown'
                            WHEN (deployment_id=? AND reported_spec_sha256=?)
                              OR (deployment_id=? AND reported_spec_sha256=?) THEN 'exact'
                            ELSE 'other' END AS attribution
                FROM runs r WHERE deployment_id IN (?,?) AND started_at>=? AND started_at<?""",
                [spec.baseline.deployment_id,spec.baseline.deployment_id,spec.baseline.spec_sha256,
                 spec.candidate.deployment_id,spec.candidate.spec_sha256,
                 spec.baseline.deployment_id,spec.candidate.deployment_id,start,end])
            total=conn.execute("SELECT count(*) FROM rollout_runs").fetchone()[0]
            if total>MAX_RUNS:
                raise m.MonitoringError("too many rollout runs; narrow the common time window")
            scope="""FROM steps s JOIN rollout_runs r ON
                     s.deployment_id=r.deployment_id AND s.run_id=r.run_id"""
            steps=conn.execute("SELECT count(*) "+scope).fetchone()[0]
            if steps>MAX_STEPS:
                raise m.MonitoringError("too many rollout steps; narrow the common time window")
            run_hash=_set_digest(conn,"""SELECT sha256(to_json(struct_pack(
                dep:=deployment_id,id:=run_id,start_time:=started_at,end_time:=finished_at,
                state:=status,verified:=outcome_reported_verified,source:=outcome_source,
                spec:=reported_spec_sha256))) AS h FROM rollout_runs""",[])
            step_hash=_set_digest(conn,"""SELECT sha256(to_json(struct_pack(
                dep:=s.deployment_id,id:=s.run_id,seq:=s.seq,step:=s.step_id,
                activity:=s.activity,state:=s.status,at_time:=s.observed_at))) AS h """+scope,[])
            basis=spec.model_dump(exclude={"offset","limit","expected_population_sha256",
                                          "evidence_side","evidence_attribution","evidence_status"})
            metadata={role:{k:info[k] for k in ("deployment_id","name","runtime",
                                                "reported_spec_sha256","design_sha256")}
                      for role,info in sides.items()}
            population=evidence._digest({"review":basis,"metadata":metadata,
                                         "runs_sha256":run_hash,"steps_sha256":step_hash})
            if spec.expected_population_sha256 and population!=spec.expected_population_sha256:
                raise m.MonitoringError("rollout evidence changed; inspect both cohorts again")
            counts={role:{a:dict.fromkeys(m.RUN_STATUSES,0) for a in ATTRIBUTIONS} for role in ROLES}
            for side,attr,status,n in conn.execute("""SELECT side,attribution,status,count(*)
                        FROM rollout_runs GROUP BY 1,2,3""").fetchall():
                if status not in m.RUN_STATUSES:
                    raise m.MonitoringError("unknown retained run status; review source data")
                counts[side][attr][status]=n
            result=summarize(counts,spec)
            for role in ROLES:
                result["cohorts"][role].update(metadata[role])
                result["cohorts"][role]["selected_spec_sha256"]=getattr(spec,role).spec_sha256
                result["cohorts"][role]["selected_spec_matches_current_declaration"]=(
                    metadata[role]["reported_spec_sha256"]==getattr(spec,role).spec_sha256)
            where,args=["side=?"],[spec.evidence_side]
            if spec.evidence_attribution!="all":
                where.append("attribution=?");args.append(spec.evidence_attribution)
            if spec.evidence_status:
                where.append("status=?");args.append(spec.evidence_status)
            predicate=" AND ".join(where)
            page_total=conn.execute("SELECT count(*) FROM rollout_runs WHERE "+predicate,args).fetchone()[0]
            page=conn.execute("""SELECT deployment_id,run_id,attribution FROM rollout_runs WHERE """+
                predicate+" ORDER BY started_at DESC,run_id LIMIT ? OFFSET ?",
                [*args,spec.limit,spec.offset]).fetchall()
            rows=[]
            for dep,rid,attr in page:
                item=evidence._detail(conn,dep,rid,sides[spec.evidence_side])
                run=item["run"]
                rows.append({"reference":item["reference"],"attribution":attr,
                             **{k:run[k] for k in ("run_id","status","started_at","finished_at",
                                                  "reported_spec_sha256","outcome_reported_verified")}})
            return {"format":FORMAT,"population_sha256":population,"review_basis":basis,
                    **result,"matching_runs":total,"matching_steps":steps,
                    "same_reported_runtime":sides["baseline"]["runtime"]==sides["candidate"]["runtime"],
                    "evidence_page":{"side":spec.evidence_side,"attribution":spec.evidence_attribution,
                                     "status":spec.evidence_status,"matching_runs":page_total,
                                     "offset":spec.offset,"next_offset":spec.offset+len(rows)
                                     if rows and spec.offset+len(rows)<page_total else None,"rows":rows},
                    "business_case_identity_verified":False,"release_approved":False,
                    "source_completeness_verified":False,"runtime_contacted":False,
                    "execution_authorized":False,"interpretation":[
                        "Common reported start window; each run belongs to its retained deployment. Matching run IDs across deployments are not joined.",
                        "Exact version claims, other claims and unhashed runs are separate. A missing version claim is not evidence of either selected version.",
                        "Succeeded is a reported terminal status; running reports are unresolved, failed, cancelled and handed-to-human reports are known non-successes for this metric.",
                        "Bounds are finite retained-population identification bounds, not confidence intervals or causal estimates. A different case mix or runtime can explain differences.",
                        "Passing entered gates does not approve a rollout or verify actual deployed code, provider state or business outcomes."]}
        finally:
            conn.close()
