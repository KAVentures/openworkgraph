"""Conditional, category-specific multi-agent procurement portfolios.

This is not a router. Categories are retrospective reviewed-suite labels,
not proven information available before execution. Every forecast is explicit
and descriptive; no automatic selection or execution authorization.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .agent_procurement import Alias, Cost, Duration, Strict, CATEGORIES

Count = Annotated[StrictInt, Field(ge=0, le=1_000_000_000)]
AnnualCost = Annotated[StrictInt, Field(ge=0, le=10**18)]
Bps = Annotated[StrictInt, Field(ge=0,le=10000)]
Category = Literal["normal","exception","missing_facts"]


class CategoryVolume(Strict):
    category: Category
    annual_cases: Count


class Assignment(Strict):
    category: Category
    candidate_id: Alias | None  # null means keep the existing manual path


class Portfolio(Strict):
    portfolio_id: Alias
    assignments: list[Assignment] = Field(min_length=3,max_length=3)
    routing_annual_microunits: AnnualCost
    routing_one_time_microunits: AnnualCost
    routing_cost_per_ai_case_microunits: Cost
    routing_human_ms_per_ai_case: Duration

    @model_validator(mode="after")
    def exact_assignment(self):
        if {x.category for x in self.assignments} != set(CATEGORIES):
            raise ValueError("assign each category exactly once")
        return self


class PortfolioPolicy(Strict):
    category_volumes: list[CategoryVolume] = Field(min_length=3,max_length=3)
    portfolios: list[Portfolio] = Field(min_length=1,max_length=20)
    avoidable_manual_annual_microunits: AnnualCost = 0
    minimum_declared_success_basis_points: Bps = 9000
    maximum_declared_regression_basis_points: Bps = 0
    confirm_conditional_category_assignment: StrictBool
    confirm_non_overlapping_routing_costs: StrictBool

    @model_validator(mode="after")
    def exact_policy(self):
        if {x.category for x in self.category_volumes} != set(CATEGORIES):
            raise ValueError("supply each category volume exactly once")
        if len({x.portfolio_id for x in self.portfolios})!=len(self.portfolios):
            raise ValueError("duplicate portfolio id")
        if not self.confirm_conditional_category_assignment or not self.confirm_non_overlapping_routing_costs:
            raise ValueError("explicitly confirm conditional routing and non-overlapping costs")
        return self


def _read(x):
    return None if x is None else Fraction(int(x["numerator"]),int(x["denominator"]))


def _pair(value):
    return tuple(_read(value[key]) for key in ("lower_microunits","upper_microunits"))


def _sum(a,b):
    return tuple(x+y if x is not None and y is not None else None for x,y in zip(a,b))


def _scale(value,n,volume):
    if volume==0:
        return (Fraction(0),Fraction(0))
    if n<=0:
        raise ValueError("positive volume needs observed category")
    return tuple(Fraction(v)*volume/n if v is not None else None for v in value)


def _cost_minus(a,b):
    return (a[0]-b[1] if a[0] is not None and b[1] is not None else None,
            a[1]-b[0] if a[1] is not None and b[0] is not None else None)


def _shift(a,value):
    return tuple(x+value if x is not None else None for x in a)


def _quality(q,field):
    v=q[field]
    return (v["lower"],v["upper"])


def _min_gate(v,limit,n):
    return "pass" if v[0]*10000>=limit*n else "fail" if v[1]*10000<limit*n else "unknown"


def _max_gate(v,limit,n):
    return "pass" if v[1]*10000<=limit*n else "fail" if v[0]*10000>limit*n else "unknown"


def _combine(states):
    return "fail" if "fail" in states else "unknown" if "unknown" in states else "pass"


def evaluate(policy, annual_cases, manual_fixed, fixed_costs, summaries, money, labor_hourly):
    """Use only the server-recomputed operating review, never imported scores.

    The same manual baseline is used for each scenario. All alternative fixed
    costs are charged once per distinct active configuration alias; no vendor
    discounts or shared infrastructure savings are inferred.
    """
    volumes={x.category:x.annual_cases for x in policy.category_volumes}
    if sum(volumes.values())!=annual_cases:
        raise ValueError("category volumes must reconcile with annual cases")
    if policy.avoidable_manual_annual_microunits>manual_fixed:
        raise ValueError("avoidable manual fixed cost exceeds total")
    candidates={x["candidate_id"]:x for x in summaries}
    groups={cid:{g["category"]:g for g in x["category_reviews"]} for cid,x in candidates.items()}
    reference=next(iter(groups.values()))
    if any(v and k not in reference for k,v in volumes.items()):
        raise ValueError("positive annual volume for an unobserved category")
    if any(set(g)!=set(reference) for g in groups.values()):
        raise ValueError("category populations must agree")
    zero=(Fraction(0),Fraction(0))
    def amount(v):
        return {"lower_microunits":money(v[0]),"upper_microunits":money(v[1])}
    def rational(v):
        return {"lower":money(v[0]),"upper":money(v[1])}
    # A dedicated all-manual comparison shares its own observations with itself;
    # uncertainty must not manufacture a regression against the identical arm.
    manual_variable=zero
    for cat,v in volumes.items():
        if v:
            data=reference[cat]["manual"]
            manual_variable=_sum(manual_variable,_scale(_pair(data["total_cost"]),data["attempts"],v))
    manual_total=_shift(manual_variable,manual_fixed)
    results=[]
    for p in policy.portfolios:
        assignments={x.category:x.candidate_id for x in p.assignments}
        if any(cid is not None and cid not in candidates for cid in assignments.values()):
            raise ValueError("portfolio identifies an unknown candidate")
        if any(not volumes[cat] and cid is not None for cat,cid in assignments.items()):
            raise ValueError("zero-volume categories must be assigned manual")
        active={cid for cat,cid in assignments.items() if volumes[cat] and cid is not None}
        ai_cases=sum(volumes[cat] for cat,cid in assignments.items() if cid is not None)
        if not ai_cases and any((p.routing_annual_microunits,p.routing_one_time_microunits,
                                 p.routing_cost_per_ai_case_microunits,p.routing_human_ms_per_ai_case)):
            raise ValueError("manual-only portfolio cannot declare AI routing charges")
        # Routing direct cost excludes its human labor, which is valued at the
        # declared routine hourly rate and counted in released capacity.
        routing_variable=ai_cases*(p.routing_cost_per_ai_case_microunits+
            Fraction(p.routing_human_ms_per_ai_case*labor_hourly,3_600_000))
        routing_time=ai_cases*p.routing_human_ms_per_ai_case
        recurring_fixed=sum(fixed_costs[cid].annual_microunits for cid in active)+p.routing_annual_microunits
        one_time=sum(fixed_costs[cid].one_time_microunits for cid in active)+p.routing_one_time_microunits
        avoidable=policy.avoidable_manual_annual_microunits if ai_cases==annual_cases else 0
        variable=zero;savings=zero;capacity=zero
        success=zero;regressions=zero;change=zero;routes=[];category_status=[]
        for cat,v in sorted(volumes.items()):
            cid=assignments[cat]
            if not v:
                routes.append({"category":cat,"annual_cases":0,"candidate_id":None,"status":"not_applicable"})
                continue
            g=reference[cat] if cid is None else groups[cid][cat]
            n=g["manual"]["attempts"]
            manual=g["manual"]
            arm=manual if cid is None else g["candidate"]
            if arm["attempts"]!=n:
                raise ValueError("incompatible category attempt population")
            variable=_sum(variable,_scale(_pair(arm["total_cost"]),n,v))
            out=arm["declared_business_outcomes"]
            s=_scale((out["success_count_lower"],out["success_count_upper"]),n,v)
            if cid is None:
                saved=zero;released=zero;reg=zero;diff=zero
                trial_status="not_applicable"
            else:
                saved=_scale(_pair(g["paired_savings"]),n,v)
                a=manual["human_person_ms"];b=arm["human_person_ms"]
                released=_scale(_cost_minus((a["lower"],a["upper"]),(b["lower"],b["upper"])),n,v)
                reg=_scale(_quality(g["declared_outcome_review"],"regression_count"),n,v)
                diff=_scale(_quality(g["declared_outcome_review"],"success_count_change"),n,v)
                trial_status=candidates[cid]["trial_gate_status"]
            savings=_sum(savings,saved);capacity=_sum(capacity,released)
            success=_sum(success,s);regressions=_sum(regressions,reg);change=_sum(change,diff)
            checks={"success":_min_gate(s,policy.minimum_declared_success_basis_points,v),
                    "regression":_max_gate(reg,policy.maximum_declared_regression_basis_points,v)}
            if cid is not None:
                checks["whole_trial_gates"]=trial_status
            status=_combine(checks.values());category_status.append(status)
            routes.append({"category":cat,"annual_cases":v,"candidate_id":cid,
                           "observed_distinct_cases":g["distinct_cases"],"observed_attempts":n,
                           "annual_variable_cost":amount(_scale(_pair(arm["total_cost"]),n,v)),
                           "annual_paired_variable_savings":amount(saved),
                           "annual_declared_success_count":rational(s),
                           "annual_declared_regression_count":rational(reg),
                           "checks":checks,"status":status})
        recurring=_shift(savings,avoidable-recurring_fixed-routing_variable)
        first=_shift(recurring,-one_time)
        annual_cost=_shift(variable,manual_fixed-avoidable+recurring_fixed+routing_variable)
        checks={"weighted_success":_min_gate(success,policy.minimum_declared_success_basis_points,annual_cases),
                "weighted_regression":_max_gate(regressions,policy.maximum_declared_regression_basis_points,annual_cases),
                "all_positive_volume_category_checks":_combine(category_status)}
        results.append({"portfolio_id":p.portfolio_id,"active_candidate_ids":sorted(active),
                        "ai_cases":ai_cases,"manual_cases":annual_cases-ai_cases,
                        "annual_cost":amount(annual_cost),"first_year_cost":amount(_shift(annual_cost,one_time)),
                        "recurring_savings":amount(recurring),"first_year_savings":amount(first),
                        "annual_released_person_ms":rational(_shift(capacity,-routing_time)),
                        "annual_declared_success_count":rational(success),
                        "annual_declared_regression_count":rational(regressions),
                        "annual_declared_success_change":rational(change),
                        "candidate_and_routing_annual_fixed_microunits":str(recurring_fixed),
                        "candidate_and_routing_one_time_microunits":str(one_time),
                        "routing_variable_cost":money(routing_variable),
                        "avoidable_manual_fixed_microunits":str(avoidable),
                        "checks":checks,"declared_evidence_status":_combine(checks.values()),
                        "positive_first_year_lower_bound":None if first[0] is None else first[0]>0,
                        "routes":routes})
    return {"annual_cases":annual_cases,"category_volumes":dict(sorted(volumes.items())),
            "all_manual_annual_cost":amount(manual_total),
            "minimum_declared_success_basis_points":policy.minimum_declared_success_basis_points,
            "maximum_declared_regression_basis_points":policy.maximum_declared_regression_basis_points,
            "portfolios":results,"category_available_before_execution_verified":False,
            "routing_accuracy_verified":False,"automatic_selection_performed":False,"execution_authorized":False,
            "limitations":["Reviewed shadow-suite labels are not demonstrated pre-execution signals; the routing assumption may be impossible in production.",
                           "Annual category volumes, routing cost and routing person-time are declared scenarios, not measured population or router performance.",
                           "Every positive-volume category requires observed evidence; zero-volume categories cannot activate candidates.",
                           "All alternative fixed costs are charged once per active candidate; manual fixed costs remain unless all cases are assigned to AI and an avoidable amount was declared.",
                           "Outcome and regression gates apply per category and to the volume-weighted mixture; whole-trial gates remain required for active AI candidates.",
                           "No portfolio is automatically ranked, selected or deployed."]}

