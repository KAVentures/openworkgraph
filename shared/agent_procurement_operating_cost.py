"""All-attempt human-in-the-loop economics for frozen procurement trials.

Costs are declared measurements and scenario assumptions, not invoices,
verified productivity, comparable business outcomes, or rollout authority.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .agent_procurement import Alias, CaseId, Cost, Duration, Strict, _load, digest
from .agent_procurement_trials import review as review_trial
from .agent_procurement_adoption import adoption, paired_outcomes, released_time
from .process_shadow_review import Hash

FORMAT = "openworkgraph.procurement-operating-cost.v1"
REPORT = "openworkgraph.procurement-operating-cost-report.v1"
HOUR_MS = 3_600_000
Volume = Annotated[StrictInt, Field(ge=1, le=1_000_000_000)]
Repeat = Annotated[StrictInt, Field(ge=1, le=10)]
AnnualCost = Annotated[StrictInt, Field(ge=0, le=10**18)]


class HumanTime(Strict):
    # Person-time, not wall-clock elapsed time. Disjoint categories.
    routine_ms: Duration | None
    review_ms: Duration | None
    rework_ms: Duration | None
    fallback_ms: Duration | None
    escalation_ms: Duration | None
    other_direct_cost_microunits: Cost | None


class HumanBaseline(HumanTime):
    case_id: CaseId
    repeat: Repeat
    observed_business_outcome: Literal["success", "failure", "unresolved"] | None


class AgentIntervention(HumanTime):
    slot_id: Hash
    observed_business_outcome: Literal["success", "failure", "unresolved"] | None


class Rates(Strict):
    routine_hourly_microunits: Cost
    review_hourly_microunits: Cost
    rework_hourly_microunits: Cost
    fallback_hourly_microunits: Cost
    escalation_hourly_microunits: Cost


class FixedCost(Strict):
    candidate_id: Alias
    annual_microunits: AnnualCost
    one_time_microunits: AnnualCost


class AdoptionPolicy(Strict):
    # No fixed manual cost is assumed avoidable unless explicitly declared.
    avoidable_manual_annual_microunits: AnnualCost = 0
    adoption_case_counts: list[Annotated[StrictInt, Field(ge=0, le=1_000_000_000)]] = Field(min_length=1,max_length=21)
    minimum_declared_success_basis_points: Annotated[StrictInt,Field(ge=0,le=10000)] = 9000
    maximum_declared_regression_basis_points: Annotated[StrictInt,Field(ge=0,le=10000)] = 0


class Scenario(Strict):
    annual_cases: Volume
    manual_annual_microunits: AnnualCost
    candidate_fixed_costs: list[FixedCost] = Field(min_length=2, max_length=10)
    adoption: AdoptionPolicy | None = None

    @model_validator(mode="after")
    def valid_adoption(self):
        if self.adoption is not None:
            a=self.adoption
            if a.avoidable_manual_annual_microunits>self.manual_annual_microunits:
                raise ValueError("avoidable manual fixed cost exceeds total manual fixed cost")
            if len(set(a.adoption_case_counts))!=len(a.adoption_case_counts) or max(a.adoption_case_counts)>self.annual_cases:
                raise ValueError("adoption volumes must be unique and within annual volume")
        return self


class Request(Strict):
    format: Literal["openworkgraph.procurement-operating-cost.v1"]
    plan_sha256: Hash
    ledger_sha256: Hash
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    rates: Rates
    manual: list[HumanBaseline] = Field(max_length=500)
    interventions: list[AgentIntervention] = Field(max_length=5000)
    scenario: Scenario
    # Operator must explicitly declare that trial cost is provider/runtime
    # spend only, and the same additional cost/person-time boundaries apply.
    confirm_non_overlapping_cost_boundary: StrictBool

    @model_validator(mode="after")
    def unique(self):
        if not self.confirm_non_overlapping_cost_boundary:
            raise ValueError("confirm non-overlapping cost accounting")
        if len({(x.case_id,x.repeat) for x in self.manual}) != len(self.manual):
            raise ValueError("duplicate manual baseline observation")
        if len({x.slot_id for x in self.interventions}) != len(self.interventions):
            raise ValueError("duplicate intervention observation")
        if len({x.candidate_id for x in self.scenario.candidate_fixed_costs}) != len(self.scenario.candidate_fixed_costs):
            raise ValueError("duplicate fixed-cost candidate")
        return self


CATEGORIES = ("routine", "review", "rework", "fallback", "escalation")


def _money(x):
    """Exact rational microunits; no intermediate per-attempt rounding."""
    if x is None:
        return None
    return {"numerator": str(x.numerator), "denominator": str(x.denominator)}


def _cost_parts(observation, rates, direct, *, provider_complete=True):
    values = {k: getattr(observation, k+"_ms") if observation is not None else None
              for k in CATEGORIES}
    extra = observation.other_direct_cost_microunits if observation is not None else None
    outcome = observation.observed_business_outcome if observation is not None else None
    labor = sum((Fraction(value * getattr(rates, name+"_hourly_microunits"), HOUR_MS)
                 for name, value in values.items() if value is not None), Fraction(0))
    missing = [k+"_ms" for k,v in values.items() if v is None]
    if extra is None:
        missing.append("other_direct_cost_microunits")
    if direct is None or not provider_complete:
        missing.append("provider_runtime_cost_microunits")
    known = labor + (extra if extra is not None else 0) + (direct if direct is not None else 0)
    # A missing duration remains missing even if the assumed wage is zero:
    # this preserves human effort coverage independently of cost.
    time_known = sum(v for v in values.values() if v is not None)
    return {"known": known, "complete": not missing, "missing": missing,
            "times": values, "time_known":time_known, "time_complete":all(v is not None for v in values.values()),
            "provider":direct, "extra":extra, "labor":labor, "outcome":outcome}


def _sum_bounds(parts):
    low = sum((x["known"] for x in parts), Fraction(0))
    complete = sum(x["complete"] for x in parts)
    return low, low if complete == len(parts) else None, complete


def _bounds(lo, hi):
    return {"lower_microunits": _money(lo), "upper_microunits": _money(hi)}


def _delta(left, right):
    # Savings = manual minus alternative. Unknown positive costs on either
    # side can make the respective bound unbounded. Never assume zero.
    a,b,_ = _sum_bounds(left)
    c,d,_ = _sum_bounds(right)
    return (a-d if d is not None else None, b-c if b is not None else None)


def _outcomes(parts):
    n=len(parts)
    success=sum(x["outcome"]=="success" for x in parts)
    failure=sum(x["outcome"]=="failure" for x in parts)
    unresolved=n-success-failure
    return {"declared_success":success,"declared_failure":failure,
            "unresolved_or_missing":unresolved,"total":n,
            "success_count_lower":success,"success_count_upper":success+unresolved}


def _summary(parts):
    lo,hi,complete=_sum_bounds(parts)
    n=len(parts)
    time_known=sum(x["time_known"] for x in parts)
    time_complete=sum(x["time_complete"] for x in parts)
    touched=sum(any(v is not None and v>0 for v in x["times"].values()) for x in parts)
    touch_unknown=sum(not any(v is not None and v>0 for v in x["times"].values())
                      and not x["time_complete"] for x in parts)
    categories={}
    for k in CATEGORIES:
        vals=[x["times"][k] for x in parts]
        total=sum(v for v in vals if v is not None)
        categories[k]={"lower_person_ms":total,"upper_person_ms":total if None not in vals else None,
                       "known_attempts":sum(v is not None for v in vals)}
    return {"attempts":n,"complete_cost_attempts":complete,"total_cost":_bounds(lo,hi),
            "cost_per_enrolled_attempt":_bounds(lo/n,hi/n if hi is not None else None),
            "human_person_ms":{"lower":time_known,"upper":time_known if time_complete==n else None},
            "complete_person_time_attempts":time_complete,"human_touched_attempts":{"lower":touched,"upper":touched+touch_unknown},
            "human_time_by_category":categories,
            "provider_runtime_cost_lower_microunits":sum(x["provider"] for x in parts if x["provider"] is not None),
            "other_direct_cost_lower_microunits":sum(x["extra"] for x in parts if x["extra"] is not None),
            "labor_cost_lower_microunits":_money(sum((x["labor"] for x in parts), Fraction(0))),
            "declared_business_outcomes":_outcomes(parts)}


def _scaled(bounds, annual_cases, n, fixed):
    return tuple(value*annual_cases/n + fixed if value is not None else None for value in bounds)


def _row(slot, manual, alternative):
    lo,hi=_delta([manual],[alternative])
    return {"slot_id":slot["slot_id"],"case_id":slot["case_id"],"repeat":slot["repeat"],
            "trial_status":slot["status"],"shadow_match":slot["match"],
            "manual_cost":_bounds(manual["known"],manual["known"] if manual["complete"] else None),
            "candidate_cost":_bounds(alternative["known"],alternative["known"] if alternative["complete"] else None),
            "manual_missing":manual["missing"],"candidate_missing":alternative["missing"],
            "manual_declared_outcome":manual["outcome"],"candidate_declared_outcome":alternative["outcome"],
            "paired_savings":_bounds(lo,hi)}


def assess(packet, plan, captures, request):
    req=Request.model_validate(request)
    trial=review_trial(packet,plan,captures)
    if (req.plan_sha256!=trial["plan_sha256"] or req.ledger_sha256!=trial["ledger_sha256"]
            or req.currency!=trial["currency"]):
        raise ValueError("operating evidence must pin the recomputed trial and currency")
    slots={x["slot_id"]:x for x in trial["attempts"]}
    keys={(x["case_id"],x["repeat"]) for x in slots.values()}
    manual={(x.case_id,x.repeat):x for x in req.manual}
    interventions={x.slot_id:x for x in req.interventions}
    fixed={x.candidate_id:x for x in req.scenario.candidate_fixed_costs}
    if set(manual)-keys or set(interventions)-set(slots):
        raise ValueError("unknown case/repeat or trial slot")
    candidates={x["candidate_id"]:x for x in trial["candidates"]}
    if set(fixed)!=set(candidates):
        raise ValueError("fixed cost assumptions must cover every candidate")
    manual_parts={key:_cost_parts(manual.get(key),req.rates,0) for key in sorted(keys)}
    by_candidate=defaultdict(list)
    for slot in slots.values():
        # Preserve any known incurred cost on withdrawal as a lower bound;
        # unresolved attempts cannot establish complete provider spend.
        direct=slot["cost_microunits"]
        alt=_cost_parts(interventions.get(slot["slot_id"]),req.rates,direct,
                        provider_complete=slot["status"] not in ("not_recorded","withdrawn"))
        by_candidate[slot["candidate_id"]].append((slot,manual_parts[(slot["case_id"],slot["repeat"])],alt))
    n=len(keys)
    summaries=[]
    for cid in sorted(candidates):
        entries=sorted(by_candidate[cid],key=lambda t:(t[0]["case_id"],t[0]["repeat"]))
        left=[x[1] for x in entries];right=[x[2] for x in entries]
        delta=_delta(left,right)
        recurring_fixed=req.scenario.manual_annual_microunits-fixed[cid].annual_microunits
        annual=_scaled(delta,req.scenario.annual_cases,n,recurring_fixed)
        first_year=tuple(x-fixed[cid].one_time_microunits if x is not None else None for x in annual)
        gates=candidates[cid]["gate_status"]
        policy=req.scenario.adoption
        if policy is None:
            # Backward-compatible full-replacement figures above are retained.
            # The new mixed-operation analysis is conservative by default.
            policy=AdoptionPolicy(adoption_case_counts=sorted({0,req.scenario.annual_cases//4,
                req.scenario.annual_cases//2,req.scenario.annual_cases*3//4,req.scenario.annual_cases}))
        volumes=sorted(set(policy.adoption_case_counts)|{0,req.scenario.annual_cases})
        quality=paired_outcomes([x["outcome"] for x in left],[x["outcome"] for x in right],
                                policy.minimum_declared_success_basis_points,policy.maximum_declared_regression_basis_points)
        quality_rows=quality.pop("rows")
        capacity=released_time(left,right)
        rollout=adoption(delta,capacity,n,req.scenario.annual_cases,volumes,
                         policy.avoidable_manual_annual_microunits,fixed[cid].annual_microunits,
                         fixed[cid].one_time_microunits,_money)
        paired_rows=[{**_row(*x),"declared_outcome_comparison":q} for x,q in zip(entries,quality_rows)]
        summaries.append({"candidate_id":cid,"configuration_sha256":candidates[cid]["configuration_sha256"],
                          "trial_gate_status":gates,"candidate":_summary(right),
                          "paired_trial_savings":_bounds(*delta),
                          "annual_recurring_savings_scenario":_bounds(*annual),
                          "first_year_savings_scenario":_bounds(*first_year),
                          "positive_first_year_lower_bound":first_year[0]>0 if first_year[0] is not None else None,
                          "declared_outcome_review":quality,"conditional_adoption":rollout,
                          "paired_released_person_ms":{"lower":_money(capacity[0]),"upper":_money(capacity[1])},
                          "rows":paired_rows})
    result={"format":REPORT,"process_reference":trial["process_reference"],"spec_sha256":trial["spec_sha256"],
            "plan_sha256":trial["plan_sha256"],"ledger_sha256":trial["ledger_sha256"],
            "trial_review_sha256":digest(trial),"request_sha256":digest(req.model_dump()),"currency":req.currency,
            "manual_baseline":_summary(list(manual_parts.values())),
            "scenario":{"annual_cases":req.scenario.annual_cases,
                        "adoption_policy":None if req.scenario.adoption is None else {
                            **req.scenario.adoption.model_dump(),
                            "avoidable_manual_annual_microunits":str(req.scenario.adoption.avoidable_manual_annual_microunits)},
                        "manual_annual_microunits":str(req.scenario.manual_annual_microunits),
                        "candidate_fixed_costs":[{"candidate_id":x.candidate_id,
                            "annual_microunits":str(x.annual_microunits),"one_time_microunits":str(x.one_time_microunits)}
                            for x in req.scenario.candidate_fixed_costs]},
            "rates":req.rates.model_dump(),"candidates":summaries,
            "independent_outcomes_verified":False,"cost_boundary_verified":False,
            "representative_population_established":False,"realized_savings_verified":False,
            "execution_authorized":False,"automatic_selection_performed":False,
            "limitations":["One manual observation is paired to each case/repeat and reused across alternatives, never counted as additional manual evidence.",
                           "Costs include all enrolled attempts, with provider spend and disjoint human person-time categories; unknowns are not zero.",
                           "Repeated shadow cases are not independent business cases. A shadow match is not a business outcome.",
                           "Business outcomes and cost measurements are declarations; no invoices or source-system outcomes were authenticated.",
                           "Annual figures are conditional scenarios assuming the observed enrolled-case mix and per-attempt costs transfer to the declared annual volume.",
                           "Fixed cost assumptions must cover incremental infrastructure, licensing and implementation costs without overlapping observed variable costs.",
                           "Full-replacement figures assume all declared manual fixed cost is eliminated; the separate mixed-adoption scenarios retain it until an explicitly declared avoidable amount at full adoption.",
                           "Released person-time is conditional capacity, not cash savings or verified staff reduction. Outcome gates refer only to declared observations.",
                           "Savings do not establish deployment eligibility; no provider call, business write, or selection is performed."]}
    return {**result,"report_sha256":digest(result)}


def operation(packet, document):
    if not isinstance(document,dict) or set(document)!={"plan","captures","request"}:
        raise ValueError("exact operating cost bundle required")
    return assess(packet,document["plan"],document["captures"],document["request"])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process-export",type=Path,required=True)
    parser.add_argument("--bundle",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        result=operation(_load(args.process_export),_load(args.bundle))
        with args.out.open("x",encoding="utf-8") as handle:
            json.dump(result,handle,indent=2,allow_nan=False)
            handle.write("\n")
    except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        parser.exit(2,"Invalid or incompatible operating cost evidence, or output unavailable.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
