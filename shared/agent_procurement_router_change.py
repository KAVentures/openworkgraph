"""Paired, version-pinned router change control. No actual deployment.

Both routers must use the same frozen portfolio, holdout and accounting
boundary. A shared path reuses exactly the same arm evidence, so its
uncertainty cancels in paired differences (but not in absolute quality).
"""
from __future__ import annotations

import argparse
from fractions import Fraction
from itertools import product
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from . import agent_procurement_router_rehearsal as router
from .agent_procurement import Alias, Strict, _load, digest
from .agent_procurement_operating_cost import operation as economics
from .agent_procurement_portfolio import _pair, _sum, _scale, _shift, _min_gate, _max_gate, _combine
from .agent_procurement_trials import Bps
from .process_shadow_review import Hash

FREEZE="openworkgraph.procurement-router-change-plan.v1"
REPORT="openworkgraph.procurement-router-change-report.v1"
RECEIPT="openworkgraph.procurement-router-change-receipt.v1"
ZERO=router.ZERO
AnnualCost=Annotated[StrictInt,Field(ge=0,le=10**18)]
SignedBps=Annotated[StrictInt,Field(ge=-10000,le=10000)]


class ChangePolicy(Strict):
    minimum_paired_success_change_basis_points: SignedBps=0
    maximum_new_outcome_regression_basis_points: Bps=0
    maximum_new_manual_leakage_basis_points: Bps=0
    maximum_new_shadow_critical_basis_points: Bps=0
    maximum_new_non_delivery_basis_points: Bps=0
    maximum_new_fallback_basis_points: Bps=0
    minimum_first_year_savings_microunits: AnnualCost=0
    require_nonnegative_first_year_savings: StrictBool=True
    require_distinct_reviewer_alias: StrictBool=True


class FreezeRequest(Strict):
    incumbent_configuration_sha256: Hash
    challenger_configuration_sha256: Hash
    portfolio_id: Alias
    router_policy: router.Policy
    comparison_policy: ChangePolicy
    nominator_alias: Alias
    incremental_annual_switch_cost_microunits: AnnualCost
    incremental_one_time_switch_cost_microunits: AnnualCost
    confirm_manual_fallback_on_known_router_failure: StrictBool
    confirm_non_overlapping_router_measurements: StrictBool
    confirm_non_overlapping_incremental_switch_cost: StrictBool
    confirmed: StrictBool


class Decision(Strict):
    disposition: Literal["refer_for_controlled_change_review","retain_incumbent","defer"]
    reviewer_alias: Alias
    expected_plan_sha256: Hash
    expected_report_sha256: Hash
    confirmed: StrictBool


def freeze(packet, discovery, holdout_plan, request):
    req=FreezeRequest.model_validate(request)
    if not req.confirmed or not req.confirm_non_overlapping_incremental_switch_cost:
        raise ValueError("explicit confirmation of change and switching cost required")
    if req.incumbent_configuration_sha256==req.challenger_configuration_sha256:
        raise ValueError("router versions must have distinct configuration hashes")
    common={"portfolio_id":req.portfolio_id,"policy":req.router_policy.model_dump(),
            "confirm_manual_fallback_on_known_router_failure":req.confirm_manual_fallback_on_known_router_failure,
            "confirm_non_overlapping_router_measurements":req.confirm_non_overlapping_router_measurements,
            "confirmed":True}
    arms={name:router.freeze(packet,discovery,holdout_plan,{**common,"router_configuration_sha256":cfg})
          for name,cfg in (("incumbent",req.incumbent_configuration_sha256),("challenger",req.challenger_configuration_sha256))}
    return router._seal({"format":FREEZE,"request":req.model_dump(),
        "incumbent_plan":arms["incumbent"],"challenger_plan":arms["challenger"],
        "holdout_trial_plan_sha256":arms["incumbent"]["holdout_trial_plan_sha256"],
        "paired_attempts":arms["incumbent"]["scheduled_attempts"],
        "execution_authorized":False,"independent_precommitment_proven":False},"change_plan_sha256")


def _destinations(row):
    return [*row["possible_candidate_ids"],*([None] if row["manual_path_possible"] else [])]


def _new(a,b):
    # Event absent in incumbent and present in challenger.
    return (int(a[1]==0 and b[0]==1),int(a[0]==0 and b[1]==1))


def _drop(a,b):
    return _new((1-a[1],1-a[0]),(1-b[1],1-b[0]))


def _counts(r,key):
    return (r[key]["lower"],r[key]["upper"])


def _pair_row(a,b,paths):
    if (a["case_id"],a["repeat"],a["true_category"],a["intended_candidate_id"])!=(
        b["case_id"],b["repeat"],b["true_category"],b["intended_candidate_id"]):
        raise ValueError("router comparisons must use exact paired evidence")
    expected=a["intended_candidate_id"]
    options=[]
    for left,right in product(_destinations(a),_destinations(b)):
        x,y=paths[left],paths[right]
        same=left==right
        # Compare challenger to incumbent using the same physical arm if
        # destination is identical; otherwise use conservative intervals.
        success=ZERO if same else router._difference(y["success"],x["success"])
        cost=ZERO if same else router._difference(x["cost"],y["cost"])
        capacity=ZERO if same else router._difference(y["capacity"],x["capacity"])
        options.append({"cost_savings":cost,"released_path_person_ms":capacity,
                        "success_change":success,
                        "new_outcome_regression":ZERO if same else _drop(x["success"],y["success"]),
                        "new_critical":ZERO if same else _new(x["critical"],y["critical"]),
                        "new_non_delivery":ZERO if same else _new(x["non_delivery"],y["non_delivery"]),
                        "new_manual_leakage":(int(expected is None and left is None and right is not None),)*2,
                        "changed_destination":(int(not same),)*2})
    values={k:router._union([p[k] for p in options]) for k in options[0]}
    values["new_fallback"]=_new(_counts(a,"fallback"),_counts(b,"fallback"))
    # Router versions have separately declared costs, even if they select
    # the same path. Do not cancel their unknown measurements.
    savings=_sum(values["cost_savings"],router._difference(_pair(a["router_cost"]),_pair(b["router_cost"])))
    capacity=_sum(values["released_path_person_ms"],router._difference(
        router._read_time(a["router_person_ms"]),router._read_time(b["router_person_ms"])))
    return {"case_id":a["case_id"],"repeat":a["repeat"],"category":a["true_category"],
        "incumbent_slot_id":a["router_slot_id"],"challenger_slot_id":b["router_slot_id"],
        "incumbent_status":a["status"],"challenger_status":b["status"],
        "incumbent_possible_candidate_ids":a["possible_candidate_ids"],
        "challenger_possible_candidate_ids":b["possible_candidate_ids"],
        "incumbent_manual_path_possible":a["manual_path_possible"],"challenger_manual_path_possible":b["manual_path_possible"],
        "paired_variable_cost_savings":router._money_bounds(savings),
        "paired_released_person_ms":router._rational(capacity),
        **{k:router._count(v) for k,v in values.items() if k not in ("cost_savings","released_path_person_ms")}}


METRICS=("success_change","new_outcome_regression","new_critical","new_non_delivery",
         "new_manual_leakage","changed_destination","new_fallback")


def _checks(metrics,policy,n):
    return {"paired_success_change":_min_gate(metrics["success_change"],policy.minimum_paired_success_change_basis_points,n),
        "new_outcome_regression":_max_gate(metrics["new_outcome_regression"],policy.maximum_new_outcome_regression_basis_points,n),
        "new_manual_leakage":_max_gate(metrics["new_manual_leakage"],policy.maximum_new_manual_leakage_basis_points,n),
        "new_shadow_critical":_max_gate(metrics["new_critical"],policy.maximum_new_shadow_critical_basis_points,n),
        "new_non_delivery":_max_gate(metrics["new_non_delivery"],policy.maximum_new_non_delivery_basis_points,n),
        "new_fallback":_max_gate(metrics["new_fallback"],policy.maximum_new_fallback_basis_points,n)}


def assess(packet, discovery, holdout, change_plan, captures):
    if not isinstance(captures,dict) or set(captures)!={"incumbent","challenger"}:
        raise ValueError("both exact version capture ledgers required")
    plan=freeze(packet,discovery,holdout["plan"],change_plan["request"])
    if digest(plan)!=digest(change_plan):
        raise ValueError("change plan or frozen discovery evidence changed")
    req=FreezeRequest.model_validate(plan["request"])
    reports={k:router.review(packet,discovery,holdout,plan[k+"_plan"],captures[k])
             for k in ("incumbent","challenger")}
    a,b=reports["incumbent"],reports["challenger"]
    if a["holdout_operating_report_sha256"]!=b["holdout_operating_report_sha256"]:
        raise ValueError("router reports must use shared holdout arm evidence")
    result=economics(packet,holdout)
    if result["report_sha256"]!=a["holdout_operating_report_sha256"]:
        raise ValueError("holdout evidence changed")
    evidence={c["candidate_id"]:{(r["case_id"],r["repeat"]):r for r in c["rows"]}
              for c in result["candidates"]}
    active=set(plan["incumbent_plan"]["assignments"].values())-{None}
    ref=next(iter(evidence.values()))
    left={(r["case_id"],r["repeat"]):r for r in a["rows"]}
    right={(r["case_id"],r["repeat"]):r for r in b["rows"]}
    if set(left)!=set(right) or len(left)!=plan["paired_attempts"]:
        raise ValueError("incomplete paired population")
    rows=[]
    for key in sorted(left):
        paths={c:router._path(evidence[c][key]) for c in active}
        paths[None]=router._path(ref[key],manual=True)
        rows.append(_pair_row(left[key],right[key],paths))
    volumes=plan["incumbent_plan"]["annual_category_volumes"];annual=sum(volumes.values())
    metrics={k:ZERO for k in METRICS};annual_savings=ZERO;capacity=ZERO;categories=[]
    for category,volume in sorted(volumes.items()):
        group=[r for r in rows if r["category"]==category]
        if not group:
            continue
        counts={k:router._sum_metric(group,k) for k in METRICS}
        checks=_checks(counts,req.comparison_policy,len(group))
        categories.append({"category":category,"annual_cases":volume,"attempts":len(group),
            "distinct_cases":len({r["case_id"] for r in group}),
            "counts":{k:router._count(v) for k,v in counts.items()},"checks":checks,"status":_combine(list(checks.values()))})
        if volume:
            for k in METRICS:
                metrics[k]=_sum(metrics[k],_scale(counts[k],len(group),volume))
            annual_savings=_sum(annual_savings,_scale(router._sum_pair(group,"paired_variable_cost_savings",_pair),len(group),volume))
            capacity=_sum(capacity,_scale(router._sum_pair(group,"paired_released_person_ms",router._read_time),len(group),volume))
    recurring=_shift(annual_savings,-req.incremental_annual_switch_cost_microunits)
    first=_shift(recurring,-req.incremental_one_time_switch_cost_microunits)
    checks=_checks(metrics,req.comparison_policy,annual)
    checks["all_observed_category_comparisons"]=_combine([c["status"] for c in categories])
    checks["challenger_absolute_router_gates"]=b["gate_status"]
    checks["both_router_capture_coverage"]=_combine([a["checks"]["router_capture_coverage"],b["checks"]["router_capture_coverage"]])
    if req.comparison_policy.require_nonnegative_first_year_savings:
        threshold=req.comparison_policy.minimum_first_year_savings_microunits
        checks["first_year_savings"]=("pass" if first[0] is not None and first[0]>=threshold else
             "fail" if first[1] is not None and first[1]<threshold else "unknown")
    else:
        checks["first_year_savings"]="not_required"
    ready=all(x in ("pass","not_required") for x in checks.values())
    return router._seal({"format":REPORT,"change_plan_sha256":plan["change_plan_sha256"],
        "holdout_operating_report_sha256":result["report_sha256"],
        "incumbent_report_sha256":a["report_sha256"],"challenger_report_sha256":b["report_sha256"],
        "incumbent_configuration_sha256":req.incumbent_configuration_sha256,
        "challenger_configuration_sha256":req.challenger_configuration_sha256,
        "currency":result["currency"],"portfolio_id":req.portfolio_id,
        "paired_attempts":len(rows),"annual_cases":annual,"checks":checks,
        "eligible_for_controlled_change_review":ready,
        "incumbent_absolute_gate_status":a["gate_status"],"challenger_absolute_gate_status":b["gate_status"],
        "incumbent_router_latency":a["router_latency"],"challenger_router_latency":b["router_latency"],
        "observed_counts":{k:router._count(router._sum_metric(rows,k)) for k in METRICS},
        "annual_conditional_counts":{k:router._rational(v) for k,v in metrics.items()},
        "category_reviews":categories,"rows":rows,
        "annual_paired_variable_savings":router._money_bounds(annual_savings),
        "annual_recurring_switch_savings":router._money_bounds(recurring),
        "first_year_switch_savings":router._money_bounds(first),
        "annual_paired_released_person_ms":router._rational(capacity),
        "incremental_annual_switch_cost_microunits":str(req.incremental_annual_switch_cost_microunits),
        "incremental_one_time_switch_cost_microunits":str(req.incremental_one_time_switch_cost_microunits),
        "independent_precommitment_proven":False,"actual_routed_execution_observed":False,
        "automatic_selection_performed":False,"execution_authorized":False,
        "limitations":["Same destination uses shared counterfactual arm evidence, not two observed routed executions.",
            "Both routers use the same configured agents and manual fallback; shared portfolio fixed costs cancel. Additional switching charges are operator assumptions.",
            "Per-category and volume-weighted gates are descriptive conditional comparisons, not statistical noninferiority or independent outcome verification.",
            "Router versions and chronology are declared, not independently authenticated. A referral is not rollout authorization."]},"report_sha256")


def decide(packet,discovery,holdout,change_plan,captures,request):
    req=Decision.model_validate(request)
    if not req.confirmed:
        raise ValueError("explicit review confirmation required")
    result=assess(packet,discovery,holdout,change_plan,captures)
    plan_req=FreezeRequest.model_validate(change_plan["request"])
    if req.expected_plan_sha256!=result["change_plan_sha256"] or req.expected_report_sha256!=result["report_sha256"]:
        raise ValueError("change evidence changed")
    if plan_req.comparison_policy.require_distinct_reviewer_alias and req.reviewer_alias.casefold()==plan_req.nominator_alias.casefold():
        raise ValueError("distinct declared reviewer alias required")
    if req.disposition=="refer_for_controlled_change_review" and not result["eligible_for_controlled_change_review"]:
        raise ValueError("change evidence does not meet the frozen checks")
    return router._seal({"format":RECEIPT,"request":req.model_dump(),
        "change_plan_sha256":result["change_plan_sha256"],"report_sha256":result["report_sha256"],
        "incumbent_configuration_sha256":result["incumbent_configuration_sha256"],
        "challenger_configuration_sha256":result["challenger_configuration_sha256"],
        "referred_configuration_sha256":result["challenger_configuration_sha256"] if req.disposition=="refer_for_controlled_change_review" else None,
        "reviewer_identity_verified":False,"deployment_changed":False,"execution_authorized":False},"receipt_sha256")


def operation(packet,action,doc):
    if not isinstance(doc,dict):
        raise ValueError("exact router change bundle required")
    if action=="router-change-freeze" and set(doc)=={"discovery","holdout_plan","request"}:
        return freeze(packet,doc["discovery"],doc["holdout_plan"],doc["request"])
    keys={"discovery","holdout","change_plan","captures"}
    if action=="router-change-assess" and set(doc)==keys:
        return assess(packet,doc["discovery"],doc["holdout"],doc["change_plan"],doc["captures"])
    if action=="router-change-decide" and set(doc)==keys|{"request"}:
        return decide(packet,doc["discovery"],doc["holdout"],doc["change_plan"],doc["captures"],doc["request"])
    raise ValueError("exact router change bundle required")


def main(argv=None):
    from .agent_procurement_replay import _output
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=["router-change-freeze","router-change-assess","router-change-decide"])
    parser.add_argument("--process-export",type=Path,required=True)
    parser.add_argument("--bundle",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        result=operation(_load(args.process_export),args.operation,_load(args.bundle))
        _output(args.out_dir,{"result.json":result})
    except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        parser.exit(2,"Invalid router change evidence or output unavailable. No deployment changed.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
