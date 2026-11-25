"""Cost-constrained review of a finite, already simulated capacity grid.

No new simulation, optimization over untested designs, production capacity
certification, financial savings claim or staffing recommendation. Every
decision metric is a conditional description of the declared experiment.
"""
from __future__ import annotations

from decimal import Decimal, Context
from fractions import Fraction
from itertools import combinations
import math
import re

from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.capacity-decision-review.v1"
MAX_COST_OVERRIDES = 120
MAX_THRESHOLDS = 8
# All accepted numeric costs originate from finite binary64 or bounded
# integers. 400 decimal digits cover the smallest subnormal (5e-324),
# the largest permitted staffed totals and resource-count multiplication.
# Use a private context per operation rather than the caller's precision.
def _cost_context():
    return Context(prec=400)

REQUIRED = {"currency", "default_resource_hour_cost", "maximum_staffed_hour_cost",
            "demand_weights", "service_share_thresholds"}
OPTIONAL = {"dedicated_hour_costs", "pool_hour_costs"}


def _number(value, low, high, label):
    if (type(value) not in (float,int) or not low <= value <= high
            or not math.isfinite(value)):
        raise EventLogWorkspaceError(f"{label} must be between {low:g} and {high:g}")
    return value


def _cost_map(value, limit, label):
    if not isinstance(value,dict) or len(value)>MAX_COST_OVERRIDES:
        raise EventLogWorkspaceError(f"{label} must be a bounded exact-name mapping")
    result={}
    for name,cost in value.items():
        if not isinstance(name,str) or not 1<=len(name)<=limit:
            raise EventLogWorkspaceError(f"{label} requires exact resource names")
        result[name]=_number(cost,0,10**9,label)
    return result


def normalize(value, rates, target_share):
    """Validate explicit assumptions before calibration or simulation."""
    if not isinstance(value,dict) or not REQUIRED<=set(value) or set(value)-REQUIRED-OPTIONAL:
        raise EventLogWorkspaceError("Provide currency, resource-hour cost, budget, exact demand weights and service thresholds")
    currency=value["currency"]
    if not isinstance(currency,str) or not re.fullmatch(r"[A-Z]{3}",currency):
        raise EventLogWorkspaceError("Use one three-letter currency code; no conversion")
    weights=value["demand_weights"]
    if not isinstance(weights,list) or len(weights)!=len(rates):
        raise EventLogWorkspaceError("Enter one explicit weight for each tested demand rate")
    by_rate={}
    for row in weights:
        if not isinstance(row,dict) or set(row)!={"arrival_rate_per_hour","weight"}:
            raise EventLogWorkspaceError("Each demand weight needs exact rate and positive weight")
        rate=float(_number(row["arrival_rate_per_hour"],.001,10000,"weighted demand rate"))
        weight=_number(row["weight"],.000001,10**9,"demand weight")
        if rate not in rates or rate in by_rate:
            raise EventLogWorkspaceError("Demand weights must cover each tested rate exactly once")
        by_rate[rate]=weight
    thresholds=value["service_share_thresholds"]
    if not isinstance(thresholds,list) or not 1<=len(thresholds)<=MAX_THRESHOLDS:
        raise EventLogWorkspaceError("Enter one to eight explicit service-share thresholds")
    thresholds=[_number(x,.01,1,"service threshold") for x in thresholds]
    if len(set(thresholds))!=len(thresholds):
        raise EventLogWorkspaceError("Service thresholds must be distinct")
    if target_share not in thresholds:
        raise EventLogWorkspaceError("Include the grid's declared service threshold in the sensitivity review")
    return {"currency":currency,
            "default_resource_hour_cost":_number(value["default_resource_hour_cost"],0,10**9,"resource-hour cost"),
            "maximum_staffed_hour_cost":_number(value["maximum_staffed_hour_cost"],0,10**15,"staffed-hour budget"),
            "dedicated_hour_costs":_cost_map(value.get("dedicated_hour_costs",{}),200,"dedicated cost overrides"),
            "pool_hour_costs":_cost_map(value.get("pool_hour_costs",{}),80,"pool cost overrides"),
            "demand_weights":[{"arrival_rate_per_hour":r,"weight":by_rate[r]} for r in rates],
            "service_share_thresholds":sorted(thresholds)}


def _d(value):
    return Decimal(str(value))


def _q(value):
    return Fraction(str(value))


def effective_basis(model):
    pool_of={activity:name for name,pool in model["pools"].items() for activity in pool["activities"]}
    return {"dedicated_activities":{a:cfg["resources"] for a,cfg in sorted(model["activities"].items()) if a not in pool_of},
            "shared_pools":{name:{"resources":p["resources"],"activities":sorted(p["activities"])}
                            for name,p in sorted(model["pools"].items())}}


def _cost(basis, spec):
    rows=[]
    ctx=_cost_context()
    for kind, entries, overrides in (
        ("dedicated_activity",basis["dedicated_activities"],spec["dedicated_hour_costs"]),
        ("shared_pool",{k:v["resources"] for k,v in basis["shared_pools"].items()},spec["pool_hour_costs"])):
        for name,count in sorted(entries.items()):
            if type(count) is not int or count<1:
                raise EventLogWorkspaceError("Invalid effective resource basis")
            rate=_d(overrides.get(name,spec["default_resource_hour_cost"]))
            rows.append({"kind":kind,"name":name,"resources":count,
                         "unit_hour_cost_decimal":str(rate),"staffed_hour_cost_decimal":str(ctx.multiply(rate,Decimal(count))),
                         "explicit_override":name in overrides})
    total=Decimal(0)
    for row in rows:
        total=ctx.add(total,_d(row["staffed_hour_cost_decimal"]))
    return {"staffed_resources":sum(r["resources"] for r in rows),
            "staffed_hour_cost_decimal":str(total),"cost_basis":rows,
            "within_budget":total<=_d(spec["maximum_staffed_hour_cost"])}


def preflight(planned, spec):
    """Check effective resource identity and costs before the first simulation."""
    bases={}
    dedicated,pools=set(),set()
    for label,_,model in planned:
        basis=effective_basis(model)
        if label in bases and bases[label]!=basis:
            raise EventLogWorkspaceError("Effective staffing differs across a design's demand levels")
        bases[label]=basis
        dedicated.update(basis["dedicated_activities"])
        pools.update(basis["shared_pools"])
    if set(spec["dedicated_hour_costs"])-dedicated or set(spec["pool_hour_costs"])-pools:
        raise EventLogWorkspaceError("A resource-hour cost override does not match an effective dedicated activity or shared pool")
    return {label:_cost(basis,spec) for label,basis in bases.items()}


def _ratio_bounds(run):
    n=run["post_warmup_arrivals"]
    on_time=run["completed_on_time"]
    unresolved=run["unresolved"]
    if type(n) is not int or n<=0 or any(type(x) is not int or x<0 for x in (on_time,unresolved)) or on_time+unresolved>n:
        raise EventLogWorkspaceError("Invalid target accounting in capacity decision review")
    return Fraction(on_time,n),Fraction(on_time+unresolved,n)


def _show(bounds):
    return {"lower":float(bounds[0]),"upper":float(bounds[1])}


def _status(runs, threshold):
    if any(row["overloaded"] for row in runs):
        return "overloaded"
    if any(row["bounds"][1]<threshold for row in runs):
        return "known_target_miss"
    if all(row["bounds"][0]>=threshold for row in runs):
        return "target_met_in_every_run"
    return "indeterminate"


def review(cells, costs, spec, target_share):
    """Use exact arrival counts; weights represent a declared demand mixture.

    Robust dominance requires both designs stable and the lower service
    bound of the putatively better design at least the other's upper bound
    at every matched rate and seed, with a strict cost or service advantage.
    """
    weights={r["arrival_rate_per_hour"]:_q(r["weight"]) for r in spec["demand_weights"]}
    weight_sum=sum(weights.values())
    weights={k:v/weight_sum for k,v in weights.items()}
    by_design={name:{} for name in costs}
    target=_q(target_share)
    for cell in cells:
        label,rate=cell["design"],cell["arrival_rate_per_hour"]
        if label not in by_design or rate not in weights or rate in by_design[label]:
            raise EventLogWorkspaceError("Capacity decision cells do not match reviewed design and demand")
        rows={}
        for run in cell["runs"]:
            seed=run["seed"]
            if seed in rows:
                raise EventLogWorkspaceError("Duplicate replication seed in capacity decision")
            rows[seed]={"bounds":_ratio_bounds(run),"overloaded":bool(run["overloaded"])}
        if not rows:
            raise EventLogWorkspaceError("Missing capacity decision replications")
        by_design[label][rate]=rows
    seed_sets=[set(rows) for rates in by_design.values() for rows in rates.values()]
    if (not seed_sets or any(s!=seed_sets[0] for s in seed_sets)
            or any(set(rates)!=set(weights) for rates in by_design.values())):
        raise EventLogWorkspaceError("Capacity decision needs the complete matched demand and seed grid")
    seeds=sorted(seed_sets[0])
    design_rows=[]
    exact={}
    for label,rates in by_design.items():
        all_runs=[row for rows in rates.values() for row in rows.values()]
        worst=(min(r["bounds"][0] for r in all_runs),min(r["bounds"][1] for r in all_runs))
        mixed=[]
        for seed in seeds:
            bounds=tuple(sum(weights[rate]*rates[rate][seed]["bounds"][i] for rate in weights)
                         for i in (0,1))
            mixed.append({"seed":seed,"bounds":bounds})
        mixed_worst=(min(r["bounds"][0] for r in mixed),min(r["bounds"][1] for r in mixed))
        status=_status(all_runs,target)
        rate_rows=[]
        for rate in weights:
            runs=list(rates[rate].values())
            rate_rows.append({"arrival_rate_per_hour":rate,"normalized_weight":float(weights[rate]),
                "service_status":_status(runs,target),"overloaded_replications":sum(r["overloaded"] for r in runs),
                "worst_seed_target_bounds":_show((min(r["bounds"][0] for r in runs),
                                                 min(r["bounds"][1] for r in runs)))})
        exact[label]={"runs":all_runs,"rates":rates,"cost":_d(costs[label]["staffed_hour_cost_decimal"]),
                      "worst":worst,"mixed_worst":mixed_worst}
        design_rows.append({"design":label,**costs[label],"service_status_all_tested_rates":status,
                            "budget_and_service_demonstrated":costs[label]["within_budget"] and status=="target_met_in_every_run",
                            "worst_rate_and_seed_target_bounds":_show(worst),
                            "worst_seed_demand_mix_target_bounds":_show(mixed_worst),
                            "demand_mix_by_seed":[{"seed":r["seed"],"target_bounds":_show(r["bounds"])} for r in mixed],
                            "demand_evidence":rate_rows})
    dominance=[]
    dominated=set()
    for a,b in combinations(exact,2):
        for left,right in ((a,b),(b,a)):
            l,r=exact[left],exact[right]
            if any(x["overloaded"] for x in l["runs"]+r["runs"]) or l["cost"]>r["cost"]:
                continue
            diffs=[l["rates"][rate][seed]["bounds"][0]-r["rates"][rate][seed]["bounds"][1]
                   for rate in weights for seed in seeds]
            if min(diffs)>=0 and (l["cost"]<r["cost"] or max(diffs)>0):
                dominated.add(right)
                dominance.append({"dominant_design":left,"dominated_design":right,
                                  "matched_rate_seed_cells":len(diffs),
                                  "minimum_service_advantage_lower_bound":float(min(diffs)),
                                  "maximum_service_advantage_lower_bound":float(max(diffs)),
                                  "staffed_hour_cost_advantage_decimal":str(_cost_context().subtract(r["cost"],l["cost"]))})
    sensitivity=[]
    for threshold in spec["service_share_thresholds"]:
        rows=[]
        for label,e in exact.items():
            status=_status(e["runs"],_q(threshold))
            rows.append({"design":label,"service_status":status,
                         "within_budget":costs[label]["within_budget"],
                         "budget_and_service_demonstrated":costs[label]["within_budget"] and status=="target_met_in_every_run"})
        qualified=[r["design"] for r in rows if r["budget_and_service_demonstrated"]]
        minimum=min((exact[l]["cost"] for l in qualified),default=None)
        sensitivity.append({"service_share_threshold":threshold,"designs":rows,
                            "demonstrated_designs":qualified,
                            "minimum_tested_qualifying_staffed_hour_cost_decimal":str(minimum) if minimum is not None else None,
                            "equal_minimum_cost_designs":[l for l in qualified if exact[l]["cost"]==minimum]})
    return {"format":FORMAT,"currency":spec["currency"],
            "maximum_staffed_hour_cost_decimal":str(_d(spec["maximum_staffed_hour_cost"])),
            "normalized_demand_weights":[{"arrival_rate_per_hour":r,"weight":float(w)} for r,w in weights.items()],
            "designs":design_rows,"robust_dominance_witnesses":dominance,
            "not_robustly_dominated_tested_designs":[l for l in exact if l not in dominated],
            "service_threshold_sensitivity":sensitivity,
            "all_tested_rates_required_for_service_gate":True,
            "demand_mix_is_declared_not_observed":True,"unresolved_outcomes_imputed":False,
            "production_capacity_certified":False,"financial_savings_verified":False,
            "staffing_recommendation_made":False,"execution_authorized":False,
            "interpretation":[
                "Staffed-hour cost charges each effective dedicated or shared-pool resource once for one staffed hour. It is not utilization, actual payroll, elapsed shift cost or verified savings.",
                "Service gates require every tested rate and seed to be stable and meet the lower target bound. A favorable weighted demand mixture cannot override a failed individual rate.",
                "Demand weights are declared scenario weights normalized to one, not observed demand probabilities. The mixture is calculated separately for each shared seed.",
                "Robust dominance requires both designs stable, no greater cost, and the lower service bound at least the other upper bound in every matched cell, with a strict advantage. Undominated is not recommended or proven optimal.",
                "Sensitivity reuses identical runs and changes only the service share threshold. Minimum cost applies to qualifying tested designs only; no untested design or demand is inferred."]}
