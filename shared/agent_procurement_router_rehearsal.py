"""Frozen, blind-input router rehearsal on disjoint declared holdout cases.

Replays routing predictions against paired manual/agent evidence. Never
launches a router, agent, or business tool. Conditional counterfactual
outcomes are not actual routed executions or independent ground truth.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .agent_procurement import Alias, CaseId, Cost, Duration, Strict, _cohort, _load, digest
from .agent_procurement_trials import Bps, MAX_REPEATS, _latency
from .agent_procurement_selection import _inputs
from .agent_procurement_operating_cost import Request, operation as economics, _money
from .agent_procurement_portfolio import Category, _pair, _sum, _scale, _shift, _min_gate, _max_gate, _combine
from .process_shadow_review import Hash

FREEZE="openworkgraph.procurement-router-rehearsal-plan.v1"
CHALLENGE="openworkgraph.procurement-router-challenge.v1"
CAPTURES="openworkgraph.procurement-router-captures.v1"
REPORT="openworkgraph.procurement-router-rehearsal-report.v1"
ZERO=(Fraction(0),Fraction(0))
Status=Literal["classified","abstain","timeout","transport_failed","invalid_output","withdrawn"]


class Policy(Strict):
    minimum_distinct_cases_per_positive_category: Annotated[StrictInt,Field(ge=1,le=50)]=3
    minimum_repeats: Annotated[StrictInt,Field(ge=1,le=MAX_REPEATS)]=2
    minimum_category_accuracy_basis_points: Bps=9000
    minimum_destination_agreement_basis_points: Bps=9000
    maximum_manual_leakage_basis_points: Bps=0
    maximum_fallback_basis_points: Bps=1000
    minimum_declared_success_basis_points: Bps=9000
    maximum_declared_regression_basis_points: Bps=0
    require_complete_financial_evidence: StrictBool=True
    maximum_router_p95_elapsed_ms: Duration | None=None


class FreezeRequest(Strict):
    portfolio_id: Alias
    router_configuration_sha256: Hash
    policy: Policy
    confirm_manual_fallback_on_known_router_failure: StrictBool
    confirm_non_overlapping_router_measurements: StrictBool
    confirmed: StrictBool


class Prediction(Strict):
    case_id: CaseId
    repeat: Annotated[StrictInt,Field(ge=1,le=MAX_REPEATS)]
    router_slot_id: Hash
    status: Status
    predicted_category: Category | None
    direct_cost_microunits: Cost | None
    human_person_ms: Duration | None
    elapsed_ms: Duration | None

    @model_validator(mode="after")
    def category_only_for_classified(self):
        if (self.status=="classified") != (self.predicted_category is not None):
            raise ValueError("classification required exactly for classified status")
        return self


class Ledger(Strict):
    format: Literal["openworkgraph.procurement-router-captures.v1"]
    router_plan_sha256: Hash
    router_configuration_sha256: Hash
    predictions: list[Prediction]=Field(max_length=500)

    @model_validator(mode="after")
    def unique(self):
        if len({(x.case_id,x.repeat) for x in self.predictions})!=len(self.predictions):
            raise ValueError("duplicate router prediction")
        return self


def _seal(value,key):
    return {**value,key:digest(value)}


def freeze(packet, discovery, holdout_plan, request):
    req=FreezeRequest.model_validate(request)
    if not (req.confirmed and req.confirm_manual_fallback_on_known_router_failure and
            req.confirm_non_overlapping_router_measurements):
        raise ValueError("explicit routing and accounting confirmation required")
    # Existing selection preflight enforces exact process, candidates, gates,
    # currency and unique disjoint declared discovery/holdout input hashes.
    if not isinstance(discovery,dict) or set(discovery)!={"plan","captures","request"}:
        raise ValueError("exact discovery operating bundle required")
    _,dp,h,hp=_inputs(packet,discovery["plan"],holdout_plan)
    review=economics(packet,discovery)
    econ=Request.model_validate(discovery["request"])
    portfolio=econ.scenario.portfolio
    if portfolio is None:
        raise ValueError("discovery must include an explicit portfolio")
    chosen=next((x for x in portfolio.portfolios if x.portfolio_id==req.portfolio_id),None)
    if chosen is None:
        raise ValueError("unknown portfolio")
    routes={x.category:x.candidate_id for x in chosen.assignments}
    if not any(cid is not None for cid in routes.values()):
        raise ValueError("router rehearsal requires at least one AI assignment")
    volumes={x.category:x.annual_cases for x in portfolio.category_volumes}
    cohort=_cohort(h.reference_report)
    counts={c:sum(v[1]==c for v in cohort.values()) for c in volumes}
    if h.repeats<req.policy.minimum_repeats or any(
        v and counts[c]<req.policy.minimum_distinct_cases_per_positive_category for c,v in volumes.items()):
        raise ValueError("holdout below frozen distinct-case or repeat coverage")
    slots=[{"case_id":case,"repeat":repeat,
            "router_slot_id":digest([hp["plan_sha256"],req.router_configuration_sha256,case,repeat])}
           for case in sorted(cohort) for repeat in range(1,h.repeats+1)]
    return _seal({"format":FREEZE,"process_reference":h.reference_report.process_reference.model_dump(),
        "spec_sha256":h.reference_report.spec_sha256,"holdout_trial_plan_sha256":hp["plan_sha256"],
        "holdout_suite_sha256":h.reference_report.suite_sha256,
        "discovery_operating_report_sha256":review["report_sha256"],
        "discovery_trial_plan_sha256":dp["plan_sha256"],"request":req.model_dump(),
        "assignments":routes,"annual_category_volumes":volumes,"scheduled_attempts":len(slots),
        "slots":slots,"unique_disjoint_declared_input_hashes":True,
        "independent_holdout_proven":False,"execution_authorized":False},"router_plan_sha256")


def challenge(packet, discovery, holdout_plan, router_plan, suite, *, confirm_export_inputs=False):
    if confirm_export_inputs is not True:
        raise ValueError("explicit input export confirmation required")
    verified=freeze(packet,discovery,holdout_plan,router_plan["request"])
    if digest(verified)!=digest(router_plan):
        raise ValueError("router plan changed")
    # Validate raw reviewed suite using the same installed offline evaluator
    # as blind agent replay. Do not copy categories or expected answers.
    from .agent_procurement_replay import _evaluate
    from .process_agent_project import build_runtime_spec
    ref,_=_evaluate(packet,suite,[])
    _,_,h,_=_inputs(packet,discovery["plan"],holdout_plan)
    if (ref.spec_sha256!=h.reference_report.spec_sha256 or ref.suite_sha256!=h.reference_report.suite_sha256
            or _cohort(ref)!=_cohort(h.reference_report)):
        raise ValueError("raw suite differs from exact holdout")
    hashes={x.case_id:x.case_sha256 for x in ref.cases}
    return {"format":CHALLENGE,"router_plan_sha256":verified["router_plan_sha256"],
        "router_configuration_sha256":verified["request"]["router_configuration_sha256"],
        "process_spec":build_runtime_spec(packet),
        "cases":[{"case_id":x["id"],"input":x["input"],"input_sha256":hashes[x["id"]]} for x in suite["cases"]],
        "slots":verified["slots"],"allowed_predictions":["normal","exception","missing_facts"],
        "capture_format":CAPTURES,"expected_case_answers_included":False,
        "execution_authorized":False,"business_tools_authorized":False}


def _binary(v):
    return (0,1) if v is None else (int(v),int(v))


def _outcome(v):
    return _binary(True if v=="success" else False if v=="failure" else None)


def _union(values):
    """Enclose all feasible alternative bounds, None is unbounded."""
    return (min(v[0] for v in values) if all(v[0] is not None for v in values) else None,
            max(v[1] for v in values) if all(v[1] is not None for v in values) else None)


def _money_bounds(v):
    return {"lower_microunits":_money(v[0]),"upper_microunits":_money(v[1])}


def _rational(v):
    return {"lower":_money(v[0]),"upper":_money(v[1])}


def _count(v):
    return {"lower":int(v[0]),"upper":int(v[1])}


def _difference(a,b):
    return (a[0]-b[1] if a[0] is not None and b[1] is not None else None,
            a[1]-b[0] if a[1] is not None and b[0] is not None else None)


def _path(row, manual=False):
    if manual:
        return {"cost":_pair(row["manual_cost"]),"saving":ZERO,"capacity":ZERO,
                "success":_outcome(row["manual_declared_outcome"]),"regression":(0,0),
                "critical":(0,0),"non_delivery":(0,0)}
    q=row["declared_outcome_comparison"]["regression"]
    return {"cost":_pair(row["candidate_cost"]),"saving":_pair(row["paired_savings"]),
            "capacity":_difference((row["manual_person_ms"]["lower"],row["manual_person_ms"]["upper"]),
                                   (row["candidate_person_ms"]["lower"],row["candidate_person_ms"]["upper"])),
            "success":_outcome(row["candidate_declared_outcome"]),"regression":(q["lower"],q["upper"]),
            "critical":_binary(row["shadow_critical"]),"non_delivery":_binary(row["shadow_non_delivery"])}


METRICS=("accuracy","agreement","manual_leakage","fallback","success","regression","critical","non_delivery","ai_assigned")


def _slot(slot,category,pred,assignments,paths,labor_rate):
    status=pred.status if pred is not None else "not_recorded"
    unresolved=status in ("not_recorded","withdrawn")
    expected=assignments[category]
    if unresolved:
        # A missing capture is not evidence that the fallback ran. Bound all
        # possible destinations under the fixed policy, including manual.
        destinations={None,*assignments.values()}
        accuracy=(0,1);fallback=(0,1)
    elif status=="classified":
        destinations={assignments[pred.predicted_category]}
        accuracy=_binary(pred.predicted_category==category);fallback=(0,0)
    else:
        destinations={None}
        accuracy=(0,0);fallback=(1,1)
    options=[paths[c] for c in destinations]
    metrics={k:_union([p[k] for p in options]) for k in ("success","regression","critical","non_delivery")}
    metrics.update(accuracy=accuracy,fallback=fallback,
        agreement=_union([_binary(c==expected) for c in destinations]),
        manual_leakage=_union([_binary(expected is None and c is not None) for c in destinations]),
        ai_assigned=_union([_binary(c is not None) for c in destinations]))
    direct=pred.direct_cost_microunits if pred is not None else None
    person=pred.human_person_ms if pred is not None else None
    known=Fraction(direct or 0)+Fraction((person or 0)*labor_rate,3_600_000)
    overhead=(known,known if direct is not None and person is not None and not unresolved else None)
    time=(person or 0,person if person is not None and not unresolved else None)
    cost=_sum(_union([p["cost"] for p in options]),overhead)
    saving=_difference(_union([p["saving"] for p in options]),overhead)
    capacity=_difference(_union([p["capacity"] for p in options]),time)
    # Compare against the identical intended arm. Its unknown cost cancels
    # when the actual destination is the intended destination.
    route_delta=_union([ZERO if c==expected else _difference(paths[c]["cost"],paths[expected]["cost"])
                        for c in destinations])
    total_routing_delta=_sum(route_delta,overhead)
    return {**slot,"true_category":category,"status":status,
            "predicted_category":pred.predicted_category if pred else None,
            "intended_candidate_id":expected,"possible_candidate_ids":sorted(c for c in destinations if c is not None),
            "manual_path_possible":None in destinations,"route_unresolved":unresolved,
            "router_elapsed_ms":pred.elapsed_ms if pred else None,
            "router_cost":_money_bounds(overhead),"router_person_ms":_rational(time),"routed_cost":_money_bounds(cost),
            "paired_savings":_money_bounds(saving),"released_person_ms":_rational(capacity),
            "cost_difference_from_ideal_path":_money_bounds(route_delta),
            "cost_difference_from_ideal_including_router":_money_bounds(total_routing_delta),
            **{k:_count(v) for k,v in metrics.items()}}


def _sum_metric(rows,k):
    return tuple(sum(r[k][end] for r in rows) for end in ("lower","upper"))


def _sum_pair(rows,k,reader):
    result=ZERO
    for r in rows:
        result=_sum(result,reader(r[k]))
    return result


def _read_time(v):
    return tuple(None if v[k] is None else Fraction(int(v[k]["numerator"]),int(v[k]["denominator"]))
                 for k in ("lower","upper"))


def _gates(metrics,policy,n):
    return {
        "category_accuracy":_min_gate(metrics["accuracy"],policy.minimum_category_accuracy_basis_points,n),
        "destination_agreement":_min_gate(metrics["agreement"],policy.minimum_destination_agreement_basis_points,n),
        "manual_leakage":_max_gate(metrics["manual_leakage"],policy.maximum_manual_leakage_basis_points,n),
        "router_fallback":_max_gate(metrics["fallback"],policy.maximum_fallback_basis_points,n),
        "declared_success":_min_gate(metrics["success"],policy.minimum_declared_success_basis_points,n),
        "declared_regression":_max_gate(metrics["regression"],policy.maximum_declared_regression_basis_points,n),
        "shadow_critical":_max_gate(metrics["critical"],0,n),
        "shadow_non_delivery":_max_gate(metrics["non_delivery"],0,n)}


def review(packet, discovery, holdout, router_plan, captures):
    if not isinstance(holdout,dict) or set(holdout)!={"plan","captures","request"}:
        raise ValueError("exact holdout operating bundle required")
    verified=freeze(packet,discovery,holdout["plan"],router_plan["request"])
    if digest(verified)!=digest(router_plan):
        raise ValueError("router plan or discovery evidence changed")
    req=FreezeRequest.model_validate(verified["request"])
    d=Request.model_validate(discovery["request"]);h=Request.model_validate(holdout["request"])
    if d.rates.model_dump()!=h.rates.model_dump() or d.scenario.model_dump()!=h.scenario.model_dump():
        raise ValueError("holdout scenario and accounting rates must match frozen discovery")
    ledger=Ledger.model_validate(captures)
    if (ledger.router_plan_sha256!=verified["router_plan_sha256"] or
            ledger.router_configuration_sha256!=req.router_configuration_sha256):
        raise ValueError("router captures do not identify frozen plan/configuration")
    slots={(s["case_id"],s["repeat"]):s for s in verified["slots"]}
    preds={(p.case_id,p.repeat):p for p in ledger.predictions}
    if any(key not in slots or slots[key]["router_slot_id"]!=p.router_slot_id for key,p in preds.items()):
        raise ValueError("unscheduled or mismatched router slot")
    result=economics(packet,holdout)
    candidates={c["candidate_id"]:c for c in result["candidates"]}
    evidence={cid:{(r["case_id"],r["repeat"]):r for r in c["rows"]} for cid,c in candidates.items()}
    mapping=verified["assignments"];active=set(mapping.values())-{None}
    ref=next(iter(evidence.values()))
    rows=[]
    for key,slot in slots.items():
        paths={cid:_path(evidence[cid][key]) for cid in active}
        paths[None]=_path(ref[key],manual=True)
        rows.append(_slot(slot,ref[key]["case_category"],preds.get(key),mapping,paths,h.rates.routine_hourly_microunits))
    volumes=verified["annual_category_volumes"];annual=sum(volumes.values())
    categories=[];metrics={k:ZERO for k in METRICS}
    annual_cost=ZERO;annual_savings=ZERO;annual_capacity=ZERO
    annual_route_delta=ZERO;annual_router_delta=ZERO
    for category,volume in sorted(volumes.items()):
        group=[r for r in rows if r["true_category"]==category]
        if not group:
            continue
        counts={k:_sum_metric(group,k) for k in METRICS}
        checks=_gates(counts,req.policy,len(group))
        categories.append({"category":category,"annual_cases":volume,"attempts":len(group),
            "distinct_cases":len({r["case_id"] for r in group}),"counts":{k:_count(v) for k,v in counts.items()},
            "checks":checks,"status":_combine(checks.values())})
        if not volume:
            continue
        for k in METRICS:
            metrics[k]=_sum(metrics[k],_scale(counts[k],len(group),volume))
        annual_cost=_sum(annual_cost,_scale(_sum_pair(group,"routed_cost",_pair),len(group),volume))
        annual_savings=_sum(annual_savings,_scale(_sum_pair(group,"paired_savings",_pair),len(group),volume))
        annual_capacity=_sum(annual_capacity,_scale(_sum_pair(group,"released_person_ms",_read_time),len(group),volume))
        annual_route_delta=_sum(annual_route_delta,_scale(_sum_pair(group,"cost_difference_from_ideal_path",_pair),len(group),volume))
        annual_router_delta=_sum(annual_router_delta,_scale(_sum_pair(group,"cost_difference_from_ideal_including_router",_pair),len(group),volume))
    selected=next(p for p in h.scenario.portfolio.portfolios if p.portfolio_id==req.portfolio_id)
    fixed={x.candidate_id:x for x in h.scenario.candidate_fixed_costs}
    # Charge all configured agents, even if a small holdout happened not to
    # route to them. Keep manual fixed costs because fallback is retained.
    annual_fixed=sum(fixed[c].annual_microunits for c in active)+selected.routing_annual_microunits
    one_time=sum(fixed[c].one_time_microunits for c in active)+selected.routing_one_time_microunits
    annual_cost=_shift(annual_cost,h.scenario.manual_annual_microunits+annual_fixed)
    annual_savings=_shift(annual_savings,-annual_fixed)
    first=_shift(annual_savings,-one_time)
    complete=annual_cost[1] is not None and all(x is not None for x in first)
    checks=_gates(metrics,req.policy,annual)
    checks["all_observed_category_checks"]=_combine([c["status"] for c in categories])
    checks["configured_candidate_trial_gates"]=_combine([candidates[c]["trial_gate_status"] for c in active])
    checks["complete_financial_evidence"]="pass" if complete or not req.policy.require_complete_financial_evidence else "unknown"
    checks["router_capture_coverage"]="unknown" if any(r["route_unresolved"] for r in rows) else "pass"
    latency=_latency([r["router_elapsed_ms"] if not r["route_unresolved"] else None for r in rows])
    if req.policy.maximum_router_p95_elapsed_ms is not None:
        limit=req.policy.maximum_router_p95_elapsed_ms
        checks["router_p95_latency"]=("fail" if latency["p95_lower_ms"]>limit else
            "pass" if latency["p95_upper_ms"] is not None and latency["p95_upper_ms"]<=limit else "unknown")
    labels=("normal","exception","missing_facts","abstain","timeout","transport_failed","invalid_output","withdrawn","not_recorded")
    confusion={cat:{label:sum(r["true_category"]==cat and
        (r["predicted_category"] if r["status"]=="classified" else r["status"])==label for r in rows)
        for label in labels} for cat in ("normal","exception","missing_facts")}
    return _seal({"format":REPORT,"router_plan_sha256":verified["router_plan_sha256"],
        "router_configuration_sha256":req.router_configuration_sha256,
        "holdout_operating_report_sha256":result["report_sha256"],"captures_sha256":digest(ledger.model_dump()),
        "currency":result["currency"],"portfolio_id":req.portfolio_id,"policy":req.policy.model_dump(),
        "scheduled_attempts":len(rows),"recorded_attempts":len(preds),"annual_cases":annual,
        "status_counts":{s:sum(r["status"]==s for r in rows) for s in
            ("classified","abstain","timeout","transport_failed","invalid_output","withdrawn","not_recorded")},
        "observed_counts":{k:_count(_sum_metric(rows,k)) for k in METRICS},
        "confusion_counts":confusion,"router_latency":latency,
        "annual_cost_difference_from_ideal_path":_money_bounds(annual_route_delta),
        "annual_cost_difference_from_ideal_including_router":_money_bounds(annual_router_delta),
        "annual_conditional_counts":{k:_rational(v) for k,v in metrics.items()},
        "category_reviews":categories,"rows":rows,"checks":checks,"gate_status":_combine(checks.values()),
        "annual_cost":_money_bounds(annual_cost),"first_year_cost":_money_bounds(_shift(annual_cost,one_time)),
        "recurring_savings":_money_bounds(annual_savings),"first_year_savings":_money_bounds(first),
        "annual_released_person_ms":_rational(annual_capacity),"financial_evidence_complete":complete,
        "manual_fixed_cost_retained":True,"configured_candidate_ids":sorted(active),
        "independent_holdout_proven":False,"pre_execution_predictions_verified":False,
        "actual_routed_execution_observed":False,"independent_outcomes_verified":False,
        "execution_authorized":False,"automatic_selection_performed":False,
        "limitations":["Predictions are customer-supplied; the tool does not verify when or how they were generated.",
            "Disjoint declared hashes do not prove independent sampling, chronology or absence of semantic overlap.",
            "Known router failures use the declared manual fallback counterfactual; absent/withdrawn predictions are unresolved over every configured path.",
            "Outcome and cost evidence come from paired independent-in-design arms, not actual routed execution; router measurements replace scenario per-case routing charges and include all attempts.",
            "Annual counts are rational conditional projections at frozen category volumes, not predictions of actual integer outcomes.",
            "Ideal-path differences are signed paired variable-cost comparisons; they do not include fixed charges or imply the ideal path is economically optimal. A negative difference may coexist with a quality regression.",
            "Manual fixed costs remain because fallback is retained; every configured agent incurs fixed costs. No execution or selection is authorized."]},"report_sha256")


def operation(packet, action, doc):
    if not isinstance(doc,dict):
        raise ValueError("exact router rehearsal bundle required")
    if action=="router-freeze" and set(doc)=={"discovery","holdout_plan","request"}:
        return freeze(packet,doc["discovery"],doc["holdout_plan"],doc["request"])
    if action=="router-review" and set(doc)=={"discovery","holdout","router_plan","captures"}:
        return review(packet,doc["discovery"],doc["holdout"],doc["router_plan"],doc["captures"])
    raise ValueError("exact router rehearsal bundle required")


def main(argv=None):
    from .agent_procurement_replay import _output
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=["router-freeze","router-review","challenge"])
    parser.add_argument("--process-export",type=Path,required=True)
    parser.add_argument("--bundle",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    parser.add_argument("--confirm-export-inputs",action="store_true")
    args=parser.parse_args(argv)
    try:
        packet=_load(args.process_export);doc=_load(args.bundle)
        if args.operation=="challenge":
            if set(doc)!={"discovery","holdout_plan","router_plan","suite"}:
                raise ValueError("exact challenge bundle required")
            value=challenge(packet,doc["discovery"],doc["holdout_plan"],doc["router_plan"],doc["suite"],
                            confirm_export_inputs=args.confirm_export_inputs)
            name="router-challenge.private.json"
        else:
            value=operation(packet,args.operation,doc)
            name="router-plan.json" if args.operation=="router-freeze" else "router-review.json"
        _output(args.out_dir,{name:value})
    except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        parser.exit(2,"Invalid router rehearsal evidence or output unavailable. No execution authorized.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
