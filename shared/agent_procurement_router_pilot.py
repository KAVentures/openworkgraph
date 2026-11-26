"""Controlled pilot evidence review after a pinned router-change referral.

One declared business case belongs to one arm. No duplicate execution,
allocation dispatch, deployment, or rollback is performed by this module.
Imported evidence and release identities remain customer declarations.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from . import agent_procurement_router_change as change
from . import agent_procurement_router_rehearsal as router
from .agent_procurement import Alias, Cost, Duration, Strict, _load, digest
from .agent_procurement_operating_cost import Request, HOUR_MS
from .agent_procurement_portfolio import Category, _min_gate, _max_gate, _combine
from .agent_procurement_trials import Bps, _latency
from .process_shadow_review import Hash

PLAN="openworkgraph.procurement-router-pilot-plan.v1"
LEDGER="openworkgraph.procurement-router-pilot-ledger.v1"
REPORT="openworkgraph.procurement-router-pilot-report.v1"
RECEIPT="openworkgraph.procurement-router-pilot-receipt.v1"
Epoch=Annotated[StrictInt,Field(ge=0,le=253402300799000)]
Count=Annotated[StrictInt,Field(ge=1,le=250)]
SignedBps=Annotated[StrictInt,Field(ge=-10000,le=10000)]
Arm=Literal["incumbent","challenger"]


class PilotCase(Strict):
    # Customer-generated opaque unique business-case token, not raw input,
    # customer ID, or a hash of a low-entropy personal identifier.
    case_token_sha256: Hash
    reviewed_category: Category


class Policy(Strict):
    minimum_cases_per_arm_per_category: Count=5
    minimum_challenger_success_basis_points: Bps=9000
    minimum_success_change_basis_points: SignedBps=0
    maximum_challenger_fallback_basis_points: Bps=1000
    maximum_challenger_p95_elapsed_ms: Duration | None=None
    require_complete_costs: StrictBool=True
    require_nonnegative_variable_savings: StrictBool=True


class FreezeRequest(Strict):
    pilot_id: Alias
    coordinator_alias: Alias
    window_start_ms: Epoch
    window_end_exclusive_ms: Epoch
    allocation_seed_sha256: Hash
    cases: list[PilotCase]=Field(min_length=2,max_length=500)
    policy: Policy
    rollback_target_configuration_sha256: Hash
    external_controlled_pilot_approval_sha256: Hash
    confirm_unique_cases_no_duplicate_execution: StrictBool
    confirm_non_overlapping_cost_measurements: StrictBool
    confirmed: StrictBool

    @model_validator(mode="after")
    def valid(self):
        if self.window_end_exclusive_ms<=self.window_start_ms:
            raise ValueError("invalid pilot window")
        if len({c.case_token_sha256 for c in self.cases})!=len(self.cases):
            raise ValueError("duplicate business case")
        if not (self.confirmed and self.confirm_unique_cases_no_duplicate_execution and self.confirm_non_overlapping_cost_measurements):
            raise ValueError("explicit pilot and accounting confirmations required")
        return self


class Observation(Strict):
    slot_id: Hash
    case_token_sha256: Hash
    actual_router_configuration_sha256: Hash | None
    actual_destination: Literal["manual","agent"] | None
    actual_candidate_id: Alias | None
    actual_candidate_configuration_sha256: Hash | None
    observed_at_ms: Epoch
    status: Literal["completed","failed","withdrawn","unresolved"]
    business_outcome: Literal["success","failure","unresolved"] | None
    critical_incident: StrictBool | None
    forbidden_action: StrictBool | None
    used_fallback: StrictBool | None
    direct_cost_microunits: Cost | None
    human_person_ms: Duration | None
    elapsed_ms: Duration | None
    source_evidence_sha256: Hash

    @model_validator(mode="after")
    def valid(self):
        if self.actual_destination=="agent":
            if self.actual_candidate_id is None or self.actual_candidate_configuration_sha256 is None:
                raise ValueError("agent destination needs exact declared configuration")
        elif self.actual_candidate_id is not None or self.actual_candidate_configuration_sha256 is not None:
            raise ValueError("only agent destination may carry agent identity")
        if self.status!="completed" and self.business_outcome=="success":
            raise ValueError("uncompleted attempt cannot declare success")
        return self


class Captures(Strict):
    format: Literal["openworkgraph.procurement-router-pilot-ledger.v1"]
    pilot_plan_sha256: Hash
    as_of_ms: Epoch
    observations: list[Observation]=Field(max_length=500)

    @model_validator(mode="after")
    def unique(self):
        for key in ("slot_id","case_token_sha256","source_evidence_sha256"):
            if len({getattr(x,key) for x in self.observations})!=len(self.observations):
                raise ValueError("duplicate observation or evidence reference")
        return self


class Decision(Strict):
    disposition: Literal["refer_for_expanded_pilot_review","refer_for_rollback_review","hold"]
    reviewer_alias: Alias
    expected_pilot_plan_sha256: Hash
    expected_report_sha256: Hash
    confirmed: StrictBool


def _change_receipt(packet, upstream):
    keys={"discovery","holdout","change_plan","captures","request","receipt"}
    if not isinstance(upstream,dict) or set(upstream)!=keys:
        raise ValueError("exact upstream change decision and receipt required")
    result=change.decide(packet,upstream["discovery"],upstream["holdout"],
                         upstream["change_plan"],upstream["captures"],upstream["request"])
    if digest(result)!=digest(upstream["receipt"]) or result["request"]["disposition"]!="refer_for_controlled_change_review":
        raise ValueError("positive pinned change referral required")
    return result


def freeze(packet,upstream,request):
    req=FreezeRequest.model_validate(request)
    receipt=_change_receipt(packet,upstream)
    if req.rollback_target_configuration_sha256!=receipt["incumbent_configuration_sha256"]:
        raise ValueError("rollback target must pin the reviewed incumbent")
    cp=upstream["change_plan"]["incumbent_plan"]
    mapping=cp["assignments"]
    positive={k for k,v in cp["annual_category_volumes"].items() if v}
    if not positive.issubset({c.reviewed_category for c in req.cases}):
        raise ValueError("pilot must cover positive-volume categories")
    # Reject exact token overlap with declared input hashes where the same
    # representation is used; this does not prove semantic independence.
    prior={r["case_sha256"] for key in ("discovery","holdout")
           for r in upstream[key]["plan"]["basis"]["reference_report"]["cases"]}
    if prior & {c.case_token_sha256 for c in req.cases}:
        raise ValueError("pilot token overlaps prior evaluation")
    cfgs={x["candidate_id"]:x["configuration_sha256"] for x in upstream["holdout"]["plan"]["basis"]["candidates"]}
    slots=[]
    for category in sorted({c.reviewed_category for c in req.cases}):
        cases=[c for c in req.cases if c.reviewed_category==category]
        if len(cases)<2*req.policy.minimum_cases_per_arm_per_category:
            raise ValueError("insufficient stratified pilot coverage")
        ordered=sorted(cases,key=lambda c:(digest([req.allocation_seed_sha256,c.case_token_sha256]),c.case_token_sha256))
        offset=int(digest([req.allocation_seed_sha256,category]),16)%2
        for i,c in enumerate(ordered):
            arm="incumbent" if (i+offset)%2==0 else "challenger"
            cfg=receipt[arm+"_configuration_sha256"]
            slots.append({"slot_id":digest([receipt["receipt_sha256"],req.pilot_id,c.case_token_sha256,arm]),
                          "case_token_sha256":c.case_token_sha256,"category":category,"arm":arm,
                          "expected_router_configuration_sha256":cfg,"intended_candidate_id":mapping[category]})
    econ=Request.model_validate(upstream["holdout"]["request"])
    return router._seal({"format":PLAN,"request":req.model_dump(),
        "change_receipt_sha256":receipt["receipt_sha256"],"change_plan_sha256":receipt["change_plan_sha256"],
        "currency":econ.currency,"routine_hourly_microunits":str(econ.rates.routine_hourly_microunits),
        "configured_candidates":{c:cfgs[c] for c in sorted(set(mapping.values())-{None})},
        "slots":sorted(slots,key=lambda x:x["case_token_sha256"]),
        "allocation_executed":False,"external_approval_verified":False,"execution_authorized":False},"pilot_plan_sha256")


def _bool(v):
    return (0,1) if v is None else (int(v),int(v))


def _row(slot,obs,plan):
    actual=obs.actual_router_configuration_sha256 if obs else None
    expected=slot["expected_router_configuration_sha256"]
    wrong=actual is not None and actual!=expected
    destination=obs.actual_destination if obs else None
    agent=obs.actual_candidate_id if obs else None
    agent_cfg=obs.actual_candidate_configuration_sha256 if obs else None
    bad_agent=destination=="agent" and plan["configured_candidates"].get(agent)!=agent_cfg
    fallback=obs.used_fallback if obs else None
    # A fallback is manual by definition. Never excuse an AI assignment
    # away from a reviewed manual category, even if a fallback was claimed.
    inconsistent_fallback=fallback is True and destination=="agent"
    attribution=("fail" if wrong or bad_agent or inconsistent_fallback else
                 "pass" if actual==expected and destination is not None else "unknown")
    if obs and obs.status=="completed":
        outcome=obs.business_outcome
    elif obs and obs.status=="failed":
        outcome="failure"
    else:
        outcome=None
    success=router._outcome(outcome) if attribution=="pass" else (0,1)
    critical=_bool(obs.critical_incident if obs else None)
    forbidden=_bool(obs.forbidden_action if obs else None)
    leakage=_bool(destination=="agent" if destination is not None else None) if slot["intended_candidate_id"] is None else (0,0)
    # Any unexpected agent/manual path without explicitly documented manual
    # fallback is a route deviation. Wrong router version is a separate stop.
    intended=slot["intended_candidate_id"]
    deviation=(0,1) if destination is None else (
        _bool(False) if destination=="agent" and agent==intended or destination=="manual" and intended is None else
        _bool(None if fallback is None else not fallback) if destination=="manual" else (1,1))
    direct=obs.direct_cost_microunits if obs else None
    person=obs.human_person_ms if obs else None
    known=Fraction(direct or 0)+Fraction((person or 0)*int(plan["routine_hourly_microunits"]),HOUR_MS)
    complete=bool(obs and obs.status in ("completed","failed") and attribution=="pass" and direct is not None and person is not None)
    return {**slot,"status":obs.status if obs else "not_recorded",
        "source_evidence_sha256":obs.source_evidence_sha256 if obs else None,
        "observed_at_ms":obs.observed_at_ms if obs else None,"attribution":attribution,
        "actual_router_configuration_sha256":actual,"actual_destination":destination,"actual_candidate_id":agent,
        "actual_candidate_configuration_sha256":agent_cfg,"declared_business_outcome":outcome,
        "success":router._count(success),"critical":router._count(critical),"forbidden":router._count(forbidden),
        "manual_leakage":router._count(leakage),"route_deviation":router._count(deviation),
        "fallback":router._count(_bool(fallback)),
        "variable_cost":router._money_bounds((known,known if complete else None)),
        "person_ms":router._rational((person or 0,person if complete else None)),
        "elapsed_ms":obs.elapsed_ms if obs and attribution=="pass" and obs.status in ("completed","failed") else None}


METRICS=("success","critical","forbidden","manual_leakage","route_deviation","fallback")


def _summary(rows):
    n=len(rows)
    counts={k:router._sum_metric(rows,k) for k in METRICS}
    cost=router._sum_pair(rows,"variable_cost",router._pair)
    return {"cases":n,"recorded":sum(r["status"]!="not_recorded" for r in rows),
        "terminal":sum(r["status"] in ("completed","failed") for r in rows),
        "attribution_counts":{k:sum(r["attribution"]==k for r in rows) for k in ("pass","fail","unknown")},
        "counts":{k:router._count(v) for k,v in counts.items()},
        "variable_cost":router._money_bounds(cost),
        "mean_variable_cost":router._money_bounds(tuple(x/n if x is not None else None for x in cost)),
        "latency":_latency([r["elapsed_ms"] for r in rows])}


def _comparison(a,b,policy):
    n,m=a["cases"],b["cases"]
    # Independent disjoint arms, not per-case pairing. Exact difference of
    # observed proportion bounds, not statistical inference.
    x=a["counts"]["success"];y=b["counts"]["success"]
    diff=(Fraction(y["lower"],m)-Fraction(x["upper"],n),Fraction(y["upper"],m)-Fraction(x["lower"],n))
    savings=router._difference(router._pair(a["mean_variable_cost"]),router._pair(b["mean_variable_cost"]))
    checks={"challenger_success":_min_gate((y["lower"],y["upper"]),policy.minimum_challenger_success_basis_points,m),
            "success_rate_change":_min_gate(diff,policy.minimum_success_change_basis_points,1),
            "challenger_fallback":_max_gate(tuple(b["counts"]["fallback"][k] for k in ("lower","upper")),policy.maximum_challenger_fallback_basis_points,m)}
    if policy.require_complete_costs:
        checks["complete_costs"]="pass" if a["variable_cost"]["upper_microunits"] is not None and b["variable_cost"]["upper_microunits"] is not None else "unknown"
    if policy.require_nonnegative_variable_savings:
        checks["nonnegative_variable_savings"]="pass" if savings[0] is not None and savings[0]>=0 else "fail" if savings[1] is not None and savings[1]<0 else "unknown"
    if policy.maximum_challenger_p95_elapsed_ms is not None:
        p=b["latency"];limit=policy.maximum_challenger_p95_elapsed_ms
        checks["challenger_p95_latency"]="fail" if p["p95_lower_ms"]>limit else "pass" if p["p95_upper_ms"] is not None and p["p95_upper_ms"]<=limit else "unknown"
    return {"success_rate_change":router._rational(diff),"mean_variable_cost_savings":router._money_bounds(savings),
            "checks":checks,"status":_combine(list(checks.values()))}


def review(packet,upstream,pilot_plan,captures):
    plan=freeze(packet,upstream,pilot_plan["request"])
    if digest(plan)!=digest(pilot_plan):
        raise ValueError("pilot plan or upstream evidence changed")
    req=FreezeRequest.model_validate(plan["request"]);ledger=Captures.model_validate(captures)
    if ledger.pilot_plan_sha256!=plan["pilot_plan_sha256"] or ledger.as_of_ms<req.window_start_ms:
        raise ValueError("wrong pilot ledger or as-of time")
    slots={s["slot_id"]:s for s in plan["slots"]};obs={x.slot_id:x for x in ledger.observations}
    for key,x in obs.items():
        if key not in slots or x.case_token_sha256!=slots[key]["case_token_sha256"] or not (
            req.window_start_ms<=x.observed_at_ms<req.window_end_exclusive_ms and x.observed_at_ms<=ledger.as_of_ms):
            raise ValueError("unscheduled, mismatched or out-of-window observation")
    rows=[_row(s,obs.get(s["slot_id"]),plan) for s in plan["slots"]]
    groups={arm:_summary([r for r in rows if r["arm"]==arm]) for arm in ("incumbent","challenger")}
    categories=[]
    for cat in sorted({r["category"] for r in rows}):
        a,b=(_summary([r for r in rows if r["category"]==cat and r["arm"]==arm]) for arm in ("incumbent","challenger"))
        categories.append({"category":cat,"incumbent":a,"challenger":b,**_comparison(a,b,req.policy)})
    checks={"window_closed":"pass" if ledger.as_of_ms>=req.window_end_exclusive_ms else "unknown",
        "all_cases_terminal":"pass" if all(r["status"] in ("completed","failed") for r in rows) else "unknown",
        "all_version_attribution":_combine([r["attribution"] for r in rows]),
        "all_category_comparisons":_combine([x["status"] for x in categories])}
    immediate=[]
    for key in ("critical","forbidden","manual_leakage","route_deviation"):
        lo,hi=router._sum_metric(rows,key)
        checks["zero_"+key]="fail" if lo else "unknown" if hi else "pass"
        if lo:immediate.append(key)
    if checks["all_version_attribution"]=="fail":immediate.append("version_or_destination_attribution")
    # Do not label ordinary early underperformance as a rollback trigger
    # until all evidence has resolved sufficiently to establish failure.
    if any(x["status"]=="fail" for x in categories):immediate.append("category_policy_failure")
    ready=all(x=="pass" for x in checks.values())
    state="rollback_review" if immediate else "expanded_pilot_review" if ready else "hold"
    return router._seal({"format":REPORT,"pilot_plan_sha256":plan["pilot_plan_sha256"],
        "change_receipt_sha256":plan["change_receipt_sha256"],"captures_sha256":digest(ledger.model_dump()),
        "currency":plan["currency"],"as_of_ms":ledger.as_of_ms,"scheduled_cases":len(rows),
        "recorded_cases":len(obs),"arms":groups,"category_reviews":categories,"checks":checks,
        "recommended_review":state,"rollback_review_triggers":immediate,
        "eligible_for_expanded_pilot_review":ready,"rollback_target_configuration_sha256":req.rollback_target_configuration_sha256,
        "rows":rows,"actual_execution_independently_verified":False,"external_approval_verified":False,
        "allocation_execution_verified":False,"causal_effect_established":False,"deployment_changed":False,"execution_authorized":False,
        "limitations":["Distinct declared cases are allocated once, stratified by reviewed category; seed and chronology are not independently verified.",
            "Observed outcomes and version/source hashes are customer declarations, not authenticated production telemetry.",
            "Category comparisons use disjoint arm rate and cost bounds, not paired counterfactuals or statistical significance.",
            "Variable cost includes declared direct and human routine-rate labor only; no annual ROI or fixed cost claims.",
            "Known safety or attribution failures trigger rollback review; no rollback or deployment is performed."]},"report_sha256")


def decide(packet,upstream,pilot_plan,captures,request):
    req=Decision.model_validate(request)
    result=review(packet,upstream,pilot_plan,captures)
    if not req.confirmed or req.expected_pilot_plan_sha256!=result["pilot_plan_sha256"] or req.expected_report_sha256!=result["report_sha256"]:
        raise ValueError("review confirmation or evidence pin mismatch")
    if req.reviewer_alias.casefold()==pilot_plan["request"]["coordinator_alias"].casefold():
        raise ValueError("distinct declared reviewer alias required")
    if req.disposition=="refer_for_expanded_pilot_review" and not result["eligible_for_expanded_pilot_review"]:
        raise ValueError("pilot evidence blocks expansion referral")
    # Human reviewer may always escalate for rollback or hold, including
    # reasons not represented by the quantitative pilot checks.
    return router._seal({"format":RECEIPT,"request":req.model_dump(),"pilot_plan_sha256":result["pilot_plan_sha256"],
        "report_sha256":result["report_sha256"],"recommended_review":result["recommended_review"],
        "rollback_target_configuration_sha256":result["rollback_target_configuration_sha256"],
        "reviewer_identity_verified":False,"deployment_changed":False,"execution_authorized":False},"receipt_sha256")


def operation(packet,action,doc):
    if not isinstance(doc,dict):raise ValueError("exact pilot bundle required")
    if action=="router-pilot-freeze" and set(doc)=={"upstream","request"}:
        return freeze(packet,doc["upstream"],doc["request"])
    keys={"upstream","pilot_plan","captures"}
    if action=="router-pilot-review" and set(doc)==keys:
        return review(packet,doc["upstream"],doc["pilot_plan"],doc["captures"])
    if action=="router-pilot-decide" and set(doc)==keys|{"request"}:
        return decide(packet,doc["upstream"],doc["pilot_plan"],doc["captures"],doc["request"])
    raise ValueError("exact pilot bundle required")


def main(argv=None):
    from .agent_procurement_replay import _output
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation",choices=["router-pilot-freeze","router-pilot-review","router-pilot-decide"])
    p.add_argument("--process-export",type=Path,required=True)
    p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--out-dir",type=Path,required=True)
    a=p.parse_args(argv)
    try:_output(a.out_dir,{"result.json":operation(_load(a.process_export),a.operation,_load(a.bundle))})
    except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        p.exit(2,"Invalid pilot evidence or output unavailable. No deployment changed.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
