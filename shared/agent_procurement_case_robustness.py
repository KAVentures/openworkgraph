"""Exact finite-population stress tests of repeated paired procurement trials.

No new independent observations are inferred from repeated attempts. Missing
slots retain their original bounds. All deletion and category comparisons are
diagnostics of the full enrolled trial, not authorization or a new selection.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from .agent_procurement import CATEGORIES, Strict, _load, digest
from .agent_procurement_trials import Bps, review
from .process_shadow_review import Hash

FORMAT="openworkgraph.procurement-case-robustness.v1"
REPORT="openworkgraph.procurement-case-robustness-report.v1"
SignedBps=Annotated[StrictInt,Field(ge=-10000,le=10000)]


class Policy(Strict):
    maximum_case_deletions: Annotated[StrictInt,Field(ge=0,le=10)] = 1
    minimum_remaining_cases: Annotated[StrictInt,Field(ge=1,le=50)] = 2
    minimum_paired_match_change_basis_points: SignedBps = 0
    maximum_paired_regression_basis_points: Bps = 0
    maximum_new_critical_basis_points: Bps = 0
    require_all_categories: StrictBool = True
    minimum_category_cases: Annotated[StrictInt,Field(ge=1,le=50)] = 1


class Request(Strict):
    format: Literal["openworkgraph.procurement-case-robustness.v1"]
    expected_plan_sha256: Hash
    expected_ledger_sha256: Hash
    plan: dict
    captures: dict
    policy: Policy
    confirmed: StrictBool


def _interval(lower,upper,denominator):
    # All bounds use integer numerators and a positive explicit denominator.
    # No rounding may move a policy decision across its basis-point boundary.
    if not denominator:
        return None
    if lower>upper:
        raise ValueError("invalid robustness interval")
    return {"lower_numerator":lower,"upper_numerator":upper,"denominator":denominator}


def _gate(interval,bps,*,minimum):
    if interval is None:
        return "unknown"
    lo=interval["lower_numerator"]*10000
    hi=interval["upper_numerator"]*10000
    limit=bps*interval["denominator"]
    if minimum:
        return "pass" if lo>=limit else "fail" if hi<limit else "unknown"
    return "pass" if hi<=limit else "fail" if lo>limit else "unknown"


def _sum(rows,field):
    return _interval(sum(r[field+"_lower"] for r in rows),
                     sum(r[field+"_upper"] for r in rows),len(rows))


def _clusters(rows):
    groups=defaultdict(list)
    for row in rows:
        groups[row["case_id"]].append(row)
    result=[]
    for cid,group in sorted(groups.items()):
        fields={}
        for field in ("match_difference","regression","new_critical"):
            fields[field+"_lower"]=sum(r[field+"_lower"] for r in group)
            fields[field+"_upper"]=sum(r[field+"_upper"] for r in group)
        result.append({"case_id":cid,"repeat_count":len(group),**fields})
    return result


def _deletion_envelope(clusters,field,k):
    """Extrema across every subset deleting exactly k equal-size clusters.

    The lower envelope removes the k largest lower sums; the upper envelope
    removes the k smallest upper sums. Both witnesses are valid full clusters.
    Distinct extrema need not be achieved by the same remaining population.
    """
    if not clusters or not 0<=k<len(clusters):
        raise ValueError("invalid cluster deletion count")
    repeats=clusters[0]["repeat_count"]
    if repeats<=0 or any(c["repeat_count"]!=repeats for c in clusters):
        raise ValueError("balanced exact case/repeat clusters required")
    low_drop=sorted(clusters,key=lambda c:(-c[field+"_lower"],c["case_id"]))[:k]
    high_drop=sorted(clusters,key=lambda c:(c[field+"_upper"],c["case_id"]))[:k]
    lo=sum(c[field+"_lower"] for c in clusters)-sum(c[field+"_lower"] for c in low_drop)
    hi=sum(c[field+"_upper"] for c in clusters)-sum(c[field+"_upper"] for c in high_drop)
    return {**_interval(lo,hi,(len(clusters)-k)*repeats),
            "lower_witness_deleted_case_ids":[c["case_id"] for c in low_drop],
            "upper_witness_deleted_case_ids":[c["case_id"] for c in high_drop]}


def _checks(intervals,policy):
    return {
        "paired_match_change":_gate(intervals["match_difference"],
            policy.minimum_paired_match_change_basis_points,minimum=True),
        "paired_regression":_gate(intervals["regression"],
            policy.maximum_paired_regression_basis_points,minimum=False),
        "new_critical":_gate(intervals["new_critical"],
            policy.maximum_new_critical_basis_points,minimum=False)}


def _status(checks):
    checks=tuple(checks)
    return "fail" if "fail" in checks else "unknown" if "unknown" in checks else "pass"


def _slice(rows,policy):
    intervals={field:_sum(rows,field) for field in ("match_difference","regression","new_critical")}
    checks=_checks(intervals,policy)
    return {"distinct_cases":len({r["case_id"] for r in rows}),"paired_slots":len(rows),
            "intervals":intervals,"checks":checks,"status":_status(checks.values())}


def _all_repeat_match(rows):
    # Case-level all-repeat reliability, not a rate of independent attempts.
    by_case=defaultdict(list)
    for row in rows:
        by_case[row["case_id"]].append(row["match"])
    lo=sum(all(v==1 for v in group) for group in by_case.values())
    hi=sum(all(v!=0 for v in group) for group in by_case.values())
    return _interval(lo,hi,len(by_case))


def _comparison(paired,attempts,categories,repeats,policy):
    rows=paired["rows"]
    clusters=_clusters(rows)
    if {r["case_id"] for r in rows}!=set(categories) or any(
            c["repeat_count"]!=repeats for c in clusters):
        raise ValueError("paired trial does not cover the frozen case clusters")
    if len({(r["case_id"],r["repeat"]) for r in rows})!=len(rows):
        raise ValueError("duplicate paired slot")
    full=_slice(rows,policy)
    # The full trial is always retained; stress slices never replace it.
    per_category=[]
    for category in CATEGORIES:
        group=[r for r in rows if categories[r["case_id"]]==category]
        result=_slice(group,policy)
        sufficient=result["distinct_cases"]>=policy.minimum_category_cases
        if sufficient:
            coverage="pass"
        elif not group and not policy.require_all_categories:
            coverage="not_required"
        else:
            coverage="unknown"
        per_category.append({"category":category,"coverage":coverage,**result})
    per_repeat=[{"repeat":r,**_slice([x for x in rows if x["repeat"]==r],policy)}
                for r in range(1,repeats+1)]
    if not clusters:
        raise ValueError("empty trial")
    feasible=max(0,min(policy.maximum_case_deletions,len(clusters)-policy.minimum_remaining_cases))
    deletion=[]
    for k in range(feasible+1):
        intervals={field:_deletion_envelope(clusters,field,k)
                   for field in ("match_difference","regression","new_critical")}
        checks=_checks(intervals,policy)
        deletion.append({"deleted_cases":k,"remaining_cases":len(clusters)-k,
                         "intervals":intervals,"checks":checks,
                         "status":_status(checks.values())})
    coverage=("pass" if len(clusters)>=policy.minimum_remaining_cases and
              feasible==policy.maximum_case_deletions else "unknown")
    category_status=[]
    for item in per_category:
        if item["coverage"]=="not_required":
            continue
        category_status.append(_status([item["coverage"],item["status"]]))
    # A fail means a stress condition is definitively outside the supplied
    # thresholds, not that every deletion set or unknown completion fails.
    # The full envelope has extrema across different subsets; for universal
    # robustness, a single demonstrated adverse subset is sufficient.
    stress=[]
    for k in range(feasible+1):
        entry=deletion[k]
        for field,threshold,minimum in (
            ("match_difference",policy.minimum_paired_match_change_basis_points,True),
            ("regression",policy.maximum_paired_regression_basis_points,False),
            ("new_critical",policy.maximum_new_critical_basis_points,False)):
            interval=entry["intervals"][field]
            # Test the adverse witness with its own missing-data bounds.
            drop=set(interval["lower_witness_deleted_case_ids" if minimum else "upper_witness_deleted_case_ids"])
            retained=[c for c in clusters if c["case_id"] not in drop]
            witness=_interval(sum(c[field+"_lower"] for c in retained),
                              sum(c[field+"_upper"] for c in retained),
                              interval["denominator"])
            bound_status=entry["checks"][{"match_difference":"paired_match_change",
                "regression":"paired_regression","new_critical":"new_critical"}[field]]
            witness_status=_gate(witness,threshold,minimum=minimum)
            # Even if the first adverse witness is ambiguous, a different
            # subset may definitely fail: minimize upper (or maximize lower).
            opposite=sorted(clusters,key=lambda c:(
                -c[field+"_upper"] if minimum else c[field+"_lower"],c["case_id"]))[:k]
            excluded={c["case_id"] for c in opposite}
            other=[c for c in clusters if c["case_id"] not in excluded]
            extreme=_interval(sum(c[field+"_lower"] for c in other),
                              sum(c[field+"_upper"] for c in other),interval["denominator"])
            definite=_gate(extreme,threshold,minimum=minimum)
            status="pass" if bound_status=="pass" else "fail" if definite=="fail" else "unknown"
            stress.append(status)
            entry.setdefault("universal_checks",{})[field]={
                "status":status,"adverse_witness_interval":witness,
                "adverse_witness_status":witness_status,
                "definite_failure_witness_deleted_case_ids":sorted(excluded) if definite=="fail" else None}
        entry["universal_status"]=_status(v["status"] for v in entry["universal_checks"].values())
    return {"candidate_id":paired["candidate_id"],"full_population":full,
            "all_repeats_match_case_rate":_all_repeat_match(attempts),
            "per_category":per_category,"per_repeat":per_repeat,
            "case_deletion_sensitivity":deletion,"requested_deletions":policy.maximum_case_deletions,
            "evaluated_maximum_deletions":feasible,"deletion_coverage":coverage,
            "robustness_status":_status([full["status"],coverage,*category_status,
                                         *(x["status"] for x in per_repeat),*stress])}


def operation(packet,document):
    req=Request.model_validate(document)
    if not req.confirmed:
        raise ValueError("explicit exact trial sensitivity confirmation required")
    result=review(packet,req.plan,req.captures)
    if (result["plan_sha256"]!=req.expected_plan_sha256 or
            result["ledger_sha256"]!=req.expected_ledger_sha256):
        raise ValueError("exact trial plan and ledger pins required")
    categories={r["case_id"]:r["category"] for r in req.plan["basis"]["reference_report"]["cases"]}
    if any(v not in CATEGORIES for v in categories.values()):
        raise ValueError("invalid frozen category")
    by_candidate=defaultdict(list)
    for row in result["attempts"]:
        by_candidate[row["candidate_id"]].append(row)
    reports=[]
    for paired in sorted(result["paired_comparisons"],key=lambda p:p["candidate_id"]):
        reports.append(_comparison(paired,by_candidate[paired["candidate_id"]],categories,
                                   result["repeats"],req.policy))
    body={"format":REPORT,"plan_sha256":result["plan_sha256"],"ledger_sha256":result["ledger_sha256"],
          "process_reference":result["process_reference"],"case_population_sha256":result["case_population_sha256"],
          "suite_sha256":result["suite_sha256"],"baseline_candidate_id":result["baseline_candidate_id"],
          "distinct_cases":len(categories),"repeats":result["repeats"],"policy":req.policy.model_dump(),
          "baseline_all_repeats_match_case_rate":_all_repeat_match(by_candidate[result["baseline_candidate_id"]]),
          "comparisons":reports,"automatic_selection_performed":False,"execution_authorized":False,
          "confidence_interval_estimated":False,"representative_population_established":False,
          "independent_repeats_assumed":False,"result_persisted":False,
          "limitations":["Exact finite enrolled population diagnostics; not confidence intervals or extrapolation.",
                         "All repeat blocks and categories are descriptive stress slices, not new independent observations.",
                         "Missing attempts retain their original deterministic bounds.",
                         "Deletion stress is not permission to discard cases or replace the full trial.",
                         "Different extrema may be realized by different retained case subsets.",
                         "No source outcomes, identity, rollout or financial benefits are independently verified."]}
    return {**body,"robustness_sha256":digest(body)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet",type=Path)
    parser.add_argument("document",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args(argv)
    try:
        result=operation(_load(args.packet),_load(args.document))
        import json,os
        data=json.dumps(result,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")+b"\n"
        fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"wb") as f:
            f.write(data)
    except (ValueError,TypeError,OSError,RecursionError,OverflowError):
        parser.exit(2,"Robustness review refused: invalid evidence or file access.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
