"""Frozen, repeated cross-vendor shadow trial accounting.

An offline extension of agent_procurement. Every scheduled case/candidate/repeat
is in the denominator. Imported minimized reports are claims, not independent
execution receipts. No agent or business system is contacted.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .agent_procurement import (Alias, CaseId, Cost, Duration, Strict,
    CATEGORIES, CRITICAL, _cohort, _decision, _load, digest)
from shared.process_shadow_review import Hash, ShadowEvaluation, inspect_shadow_report

DESIGN = "openworkgraph.procurement-trial-design.v1"
PLAN = "openworkgraph.procurement-trial-plan.v1"
CAPTURES = "openworkgraph.procurement-trial-captures.v1"
REVIEW = "openworkgraph.procurement-trial-review.v1"
MAX_REPEATS = 10
Bps = Annotated[StrictInt, Field(ge=0, le=10000)]
AttemptStatus = Literal["completed", "timeout", "transport_failed", "invalid_output", "withdrawn"]
STATES = ("completed", "timeout", "transport_failed", "invalid_output", "withdrawn", "not_recorded")
NO_RESULT = frozenset(("timeout", "transport_failed", "invalid_output"))
UNRESOLVED = frozenset(("withdrawn", "not_recorded"))


class Candidate(Strict):
    candidate_id: Alias
    provider: Alias
    model: Alias
    runtime: Alias
    configuration_sha256: Hash


class TrialGates(Strict):
    minimum_match_basis_points: Bps
    minimum_recorded_basis_points: Bps = 10000
    maximum_non_delivery_basis_points: Bps = 0
    maximum_critical_attempts: Annotated[StrictInt, Field(ge=0, le=500)] = 0
    maximum_total_cost_microunits: Annotated[StrictInt, Field(ge=0, le=500*10**12)] | None = None
    maximum_p95_elapsed_ms: Duration | None = None
    prohibit_new_critical: StrictBool = True
    require_all_categories: StrictBool = True


class Design(Strict):
    format: Literal["openworkgraph.procurement-trial-design.v1"]
    reference_report: ShadowEvaluation
    baseline_candidate_id: Alias
    candidates: list[Candidate] = Field(min_length=2, max_length=10)
    repeats: Annotated[StrictInt, Field(ge=1, le=MAX_REPEATS)]
    schedule_seed: Annotated[StrictInt, Field(ge=0, le=2**32-1)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    gates: TrialGates
    confirmed: StrictBool

    @model_validator(mode="after")
    def valid_design(self):
        ids = [c.candidate_id for c in self.candidates]
        if len(set(ids)) != len(ids) or self.baseline_candidate_id not in ids:
            raise ValueError("unique candidates and a declared baseline are required")
        if not self.confirmed:
            raise ValueError("explicitly confirm the selected population, candidates and gates")
        return self


class Attempt(Strict):
    case_id: CaseId
    status: AttemptStatus
    cost_microunits: Cost | None
    elapsed_ms: Duration | None


class Capture(Strict):
    candidate_id: Alias
    repeat: Annotated[StrictInt, Field(ge=1, le=MAX_REPEATS)]
    configuration_sha256: Hash
    report: ShadowEvaluation | None
    attempts: list[Attempt] = Field(max_length=50)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({a.case_id for a in self.attempts}) != len(self.attempts):
            raise ValueError("duplicate attempt within capture")
        if any(a.status == "completed" for a in self.attempts) != (self.report is not None):
            raise ValueError("a complete minimized shadow report is required exactly when plans were delivered")
        return self


class Ledger(Strict):
    format: Literal["openworkgraph.procurement-trial-captures.v1"]
    plan_sha256: Hash
    captures: list[Capture] = Field(max_length=100)


def _order(seed, case_id, ids):
    # Content-hash ordering, independent of Python random implementation and
    # input list order. Rotate a balanced order for successive repeats.
    return sorted(ids, key=lambda cid: (digest([seed, case_id, cid]), cid))


def freeze(packet, value):
    design = Design.model_validate(value)
    ref = design.reference_report
    inspect_shadow_report(packet, ref, latest_revision=packet["revision"])
    cohort = _cohort(ref)
    ids = [c.candidate_id for c in design.candidates]
    slots = []
    for case_id in sorted(cohort):
        order = _order(design.schedule_seed, case_id, ids)
        for repeat in range(1, design.repeats+1):
            shift = (repeat-1) % len(order)
            for position, cid in enumerate(order[shift:]+order[:shift], 1):
                slots.append({"case_id":case_id, "repeat":repeat, "candidate_id":cid,
                              "position":position, "slot_id":digest([case_id,repeat,cid])})
    basis = design.model_dump()
    result = {"format":PLAN, "basis":basis, "case_population_sha256":digest(cohort),
              "slots":slots, "scheduled_attempts":len(slots),
              "execution_authorized":False, "actual_execution_order_verified":False}
    return {**result, "plan_sha256":digest(result)}


def _validate_plan(packet, plan):
    if not isinstance(plan, dict) or plan.get("format") != PLAN or "basis" not in plan:
        raise ValueError("use a frozen trial plan")
    expected = freeze(packet, plan["basis"])
    if digest(plan) != digest(expected):
        raise ValueError("frozen plan or schedule changed")
    return Design.model_validate(expected["basis"]), expected


def _report_rows(packet, capture, design, cohort, report_hashes, result_hashes):
    report = capture.report
    if report is None:
        return {}
    inspect_shadow_report(packet, report, latest_revision=packet["revision"])
    if report.suite_sha256 != design.reference_report.suite_sha256 or _cohort(report) != cohort:
        raise ValueError("capture report does not cover the frozen exact suite")
    report_hash = digest(report.model_dump())
    if report_hash in report_hashes:
        raise ValueError("duplicate report envelope across captures")
    report_hashes.add(report_hash)
    result_hashes[report.results_sha256].append({"candidate_id":capture.candidate_id,"repeat":capture.repeat})
    return {r.case_id:r for r in report.cases}


def _status_row(slot, attempt, report_row):
    state = attempt.status if attempt is not None else "not_recorded"
    if state != "completed" and report_row is not None and report_row.reason != "invalid_or_stale_plan":
        raise ValueError("undelivered cases must use invalid-plan placeholders, not scored plans")
    reason = report_row.reason if state == "completed" and report_row is not None else None
    # Withdrawal and missing evidence are unresolved, not excluded. Operational
    # non-delivery is separately bounded; timeout/transport/invalid output is
    # known to yield no usable reviewed plan in this trial.
    success = (int(reason == "matches_reviewed_expectation") if state == "completed"
               else 0 if state in NO_RESULT else None)
    critical = (int(reason in CRITICAL) if state == "completed" else
                0 if state in NO_RESULT else None)
    non_delivery = 0 if state == "completed" else 1 if state in NO_RESULT else None
    return {**slot,"status":state,"reason":reason,"match":success,"critical":critical,
            "non_delivery":non_delivery,"cost_microunits":attempt.cost_microunits if attempt else None,
            "elapsed_ms":attempt.elapsed_ms if attempt else None}


def _bounds(values):
    values=list(values)
    lower=sum(v for v in values if v is not None)
    unknown=values.count(None)
    return {"lower":lower,"upper":lower+unknown,"unknown":unknown,"total":len(values)}


def _rate(bounds):
    n=bounds["total"]
    return {"lower":bounds["lower"]/n if n else None,"upper":bounds["upper"]/n if n else None}


def _minimum(bounds,bps):
    n=bounds["total"]
    return ("pass" if bounds["lower"]*10000>=bps*n else
            "fail" if bounds["upper"]*10000<bps*n else "unknown")


def _maximum(bounds,bps):
    n=bounds["total"]
    return ("pass" if bounds["upper"]*10000<=bps*n else
            "fail" if bounds["lower"]*10000>bps*n else "unknown")


def _count_max(bounds,maximum):
    return "pass" if bounds["upper"]<=maximum else "fail" if bounds["lower"]>maximum else "unknown"


def _latency(values):
    n=len(values)
    known=sorted(v for v in values if v is not None)
    missing=n-len(known)
    rank=(95*n+99)//100
    # Unknowns are nonnegative; place them all first/last to bound the order
    # statistic. No finite upper is assumed just because the input cap exists.
    low=0 if rank<=missing else known[rank-missing-1]
    high=known[rank-1] if rank<=len(known) else None
    return {"known_attempts":len(known),"unknown_attempts":missing,
            "p95_lower_ms":low,"p95_upper_ms":high,
            "p95_ms":low if not missing else None}


def _summary(candidate, rows, gates, cohort):
    n=len(rows)
    match=_bounds(r["match"] for r in rows)
    critical=_bounds(r["critical"] for r in rows)
    non_delivery=_bounds(r["non_delivery"] for r in rows)
    recorded=sum(r["status"]!="not_recorded" for r in rows)
    costs=[r["cost_microunits"] for r in rows if r["cost_microunits"] is not None]
    cost=sum(costs)
    latency=_latency([r["elapsed_ms"] for r in rows])
    categories={}
    for cat in CATEGORIES:
        group=[r for r in rows if cohort[r["case_id"]][1]==cat]
        bounds=_bounds(r["match"] for r in group)
        categories[cat]={"scheduled_attempts":len(group),"match_bounds":bounds,"match_rate_bounds":_rate(bounds)}
    checks={
        "match_rate":_minimum(match,gates.minimum_match_basis_points),
        "non_delivery_rate":_maximum(non_delivery,gates.maximum_non_delivery_basis_points),
        "critical_attempts":_count_max(critical,gates.maximum_critical_attempts),
        "recorded_coverage":"pass" if recorded*10000>=gates.minimum_recorded_basis_points*n else "fail",
        "category_coverage":"fail" if gates.require_all_categories and
                            any(not r["scheduled_attempts"] for r in categories.values()) else "pass",
    }
    if gates.maximum_total_cost_microunits is not None:
        checks["total_cost"]=("fail" if cost>gates.maximum_total_cost_microunits else
                              "pass" if len(costs)==n else "unknown")
    if gates.maximum_p95_elapsed_ms is not None:
        checks["p95_latency"]=("fail" if latency["p95_lower_ms"]>gates.maximum_p95_elapsed_ms else
                              "pass" if latency["p95_upper_ms"] is not None and
                              latency["p95_upper_ms"]<=gates.maximum_p95_elapsed_ms else "unknown")
    per_case=[]
    for case_id in sorted(cohort):
        group=[r for r in rows if r["case_id"]==case_id]
        b=_bounds(r["match"] for r in group)
        per_case.append({"case_id":case_id,"category":cohort[case_id][1],"match_bounds":b,
                         "all_repeats_matched":b["lower"]==b["total"],
                         "any_observed_match":b["lower"]>0,
                         "observed_instability":b["lower"]>0 and b["upper"]<b["total"]})
    return {**candidate.model_dump(),"scheduled_attempts":n,"recorded_attempts":recorded,
            "status_counts":{s:sum(r["status"]==s for r in rows) for s in STATES},
            "match_bounds":match,"match_rate_bounds":_rate(match),"critical_bounds":critical,
            "non_delivery_bounds":non_delivery,"categories":categories,"per_case":per_case,
            "cost":{"known_attempts":len(costs),"unknown_attempts":n-len(costs),
                    "lower_bound_microunits":cost,"total_microunits":cost if len(costs)==n else None},
            "latency":latency,"gates":checks}


def _range(v):
    return (0,1) if v is None else (v,v)


def _paired(base, candidate):
    left={(r["case_id"],r["repeat"]):r for r in base}
    right={(r["case_id"],r["repeat"]):r for r in candidate}
    rows=[]
    for key,a in sorted(left.items()):
        b=right[key]
        al,au=_range(a["match"]);bl,bu=_range(b["match"])
        acl,acu=_range(a["critical"]);bcl,bcu=_range(b["critical"])
        rows.append({"case_id":key[0],"repeat":key[1],"baseline_status":a["status"],
                     "candidate_status":b["status"],"baseline_reason":a["reason"],"candidate_reason":b["reason"],
                     "match_difference_lower":bl-au,"match_difference_upper":bu-al,
                     "regression_lower":int(al==1 and bu==0),"regression_upper":int(au==1 and bl==0),
                     "new_critical_lower":int(bcl==1 and acu==0),"new_critical_upper":int(bcu==1 and acl==0)})
    n=len(rows)
    def total(field):
        return {"lower":sum(r[field+"_lower"] for r in rows),
                "upper":sum(r[field+"_upper"] for r in rows)}
    change=total("match_difference")
    return {"candidate_id":candidate[0]["candidate_id"],"paired_slots":n,
            "match_rate_difference_bounds":{"lower":change["lower"]/n,"upper":change["upper"]/n},
            "regression_bounds":total("regression"),"new_critical_bounds":total("new_critical"),"rows":rows}


def _frontier(summaries):
    # No uncertain quality, cost or latency is silently assigned a point value.
    eligible=[s for s in summaries if s["gate_status"]=="pass" and
              s["match_bounds"]["unknown"]==s["critical_bounds"]["unknown"]==0 and
              s["cost"]["total_microunits"] is not None and s["latency"]["p95_ms"] is not None]
    def vector(s):
        return (-s["match_bounds"]["lower"],s["critical_bounds"]["lower"],
                s["cost"]["total_microunits"],s["latency"]["p95_ms"])
    def dominates(a,b):
        return all(x<=y for x,y in zip(a,b)) and any(x<y for x,y in zip(a,b))
    return {"candidate_ids":sorted(s["candidate_id"] for s in eligible
                if not any(dominates(vector(t),vector(s)) for t in eligible)),
            "eligible_candidate_ids":sorted(s["candidate_id"] for s in eligible),
            "excluded_candidate_ids":sorted(s["candidate_id"] for s in summaries if s not in eligible)}


def review(packet, plan, value):
    design, plan=_validate_plan(packet,plan)
    ledger=Ledger.model_validate(value)
    if ledger.plan_sha256!=plan["plan_sha256"]:
        raise ValueError("captures do not identify the frozen trial plan")
    cohort=_cohort(design.reference_report)
    candidates={c.candidate_id:c for c in design.candidates}
    captured={}
    report_hashes=set()
    result_hashes=defaultdict(list)
    for capture in ledger.captures:
        key=(capture.candidate_id,capture.repeat)
        if key in captured or capture.candidate_id not in candidates or capture.repeat>design.repeats:
            raise ValueError("duplicate or unscheduled candidate/repeat")
        if capture.configuration_sha256!=candidates[capture.candidate_id].configuration_sha256:
            raise ValueError("candidate configuration changed after enrollment")
        attempts={a.case_id:a for a in capture.attempts}
        if set(attempts)-set(cohort):
            raise ValueError("attempt does not identify a frozen case")
        reports=_report_rows(packet,capture,design,cohort,report_hashes,result_hashes)
        captured[key]=(attempts,reports)
    by_candidate=defaultdict(list)
    for slot in plan["slots"]:
        attempts,reports=captured.get((slot["candidate_id"],slot["repeat"]),({},{}))
        row=_status_row(slot,attempts.get(slot["case_id"]),reports.get(slot["case_id"]))
        by_candidate[slot["candidate_id"]].append(row)
    base=by_candidate[design.baseline_candidate_id]
    summaries=[];comparisons=[]
    for cid,candidate in candidates.items():
        rows=by_candidate[cid]
        summary=_summary(candidate,rows,design.gates,cohort)
        if cid!=design.baseline_candidate_id:
            paired=_paired(base,rows)
            comparisons.append(paired)
            if design.gates.prohibit_new_critical:
                summary["gates"]["no_new_critical"]=_count_max(paired["new_critical_bounds"],0)
        summary["gate_status"]=_decision(summary["gates"])
        summaries.append(summary)
    return {"format":REVIEW,"plan_sha256":plan["plan_sha256"],"ledger_sha256":digest(ledger.model_dump()),
            "process_reference":design.reference_report.process_reference.model_dump(),
            "spec_sha256":design.reference_report.spec_sha256,"suite_sha256":design.reference_report.suite_sha256,
            "case_population_sha256":plan["case_population_sha256"],"baseline_candidate_id":design.baseline_candidate_id,
            "currency":design.currency,"scheduled_attempts":len(plan["slots"]),"repeats":design.repeats,
            "candidates":summaries,"paired_comparisons":comparisons,"pareto":_frontier(summaries),
            "attempts":[r for cid in sorted(by_candidate) for r in by_candidate[cid]],
            "shared_result_hashes":[{"results_sha256":h,"captures":items} for h,items in sorted(result_hashes.items()) if len(items)>1],
            "candidate_identity_verified":False,"actual_execution_order_verified":False,
            "telemetry_independently_verified":False,"report_authenticity_verified":False,
            "independent_business_outcomes_verified":False,"execution_authorized":False,
            "automatic_selection_performed":False,"representative_population_established":False,
            "limitations":["A recorded capture is a customer declaration; schedule order is planned, not proven.",
                           "Timeout, transport failure and invalid output are known non-delivery; withdrawn and absent slots remain unresolved.",
                           "Bounds cover every scheduled attempt and are deterministic missing-data bounds, not confidence intervals.",
                           "Repeated attempts on one case are not independent business cases.",
                           "Placeholder invalid plans for undelivered slots are not scored as returned critical plans.",
                           "All attempt costs include failures when known; null is unknown, never zero.",
                           "No external agents, models or business tools are invoked."]}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="operation",required=True)
    for name in ("freeze","review"):
        p=sub.add_parser(name)
        p.add_argument("--process-export",type=Path,required=True)
        p.add_argument("--out",type=Path,required=True)
        if name=="freeze":p.add_argument("--design",type=Path,required=True)
        else:
            p.add_argument("--plan",type=Path,required=True)
            p.add_argument("--captures",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        packet=_load(args.process_export)
        result=(freeze(packet,_load(args.design)) if args.operation=="freeze" else
                review(packet,_load(args.plan),_load(args.captures)))
        with args.out.open("x",encoding="utf-8") as handle:
            json.dump(result,handle,indent=2,allow_nan=False)
            handle.write("\n")
    except (OSError,ValueError,KeyError,TypeError,RecursionError):
        parser.exit(2,"Invalid or incompatible trial input, or output unavailable. No execution or selection authorized.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
