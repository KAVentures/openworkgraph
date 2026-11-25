"""Evidence-pinned discovery/holdout procurement selection.

Local, portable, non-authorizing governance artifacts. A nomination fixes the
candidate and holdout plan before results are supplied to this tool. Hashes
detect changes relative to a retained pin; they are not independent timestamps
or proof that the customer did not inspect the holdout earlier.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from .agent_procurement import Alias, Strict, _cohort, _load, digest
from .agent_procurement_trials import _validate_plan, review, Bps
from .process_shadow_review import Hash

NOMINATION = "openworkgraph.procurement-nomination.v1"
ASSESSMENT = "openworkgraph.procurement-selection-assessment.v1"
RECEIPT = "openworkgraph.procurement-selection-receipt.v1"


class Policy(Strict):
    minimum_discovery_cases: Annotated[StrictInt, Field(ge=1, le=50)] = 5
    minimum_holdout_cases: Annotated[StrictInt, Field(ge=1, le=50)] = 5
    minimum_repeats: Annotated[StrictInt, Field(ge=1, le=10)] = 2
    minimum_candidate_match_basis_points: Bps = 9000
    minimum_paired_match_change_basis_points: Annotated[StrictInt, Field(ge=-10000,le=10000)] = 0
    maximum_regression_basis_points: Bps = 0
    maximum_candidate_cost_ratio_basis_points: Annotated[StrictInt,Field(ge=0,le=100000)] | None = None
    maximum_candidate_p95_ratio_basis_points: Annotated[StrictInt,Field(ge=0,le=100000)] | None = None
    require_complete_cost_latency: StrictBool = True
    require_all_categories: StrictBool = True
    require_distinct_reviewer_alias: StrictBool = True


class Nominate(Strict):
    candidate_id: Alias
    nominator_alias: Alias
    policy: Policy
    confirmed: StrictBool


class Decide(Strict):
    decision: Literal["select_for_substitution_rehearsal","retain_baseline","defer"]
    reviewer_alias: Alias
    expected_assessment_sha256: Hash
    expected_nomination_sha256: Hash
    confirmed: StrictBool


def _inputs(packet, discovery_plan, holdout_plan):
    d,dp=_validate_plan(packet,discovery_plan)
    h,hp=_validate_plan(packet,holdout_plan)
    if (d.reference_report.spec_sha256!=h.reference_report.spec_sha256 or
            d.reference_report.suite_sha256==h.reference_report.suite_sha256):
        raise ValueError("discovery and holdout must be distinct suites of the same specification")
    if (d.baseline_candidate_id!=h.baseline_candidate_id or d.currency!=h.currency or
            d.gates.model_dump()!=h.gates.model_dump() or
            {c.candidate_id:c.model_dump() for c in d.candidates}!=
            {c.candidate_id:c.model_dump() for c in h.candidates}):
        raise ValueError("candidate configurations, baseline, currency and trial gates must remain unchanged")
    dc,hc=_cohort(d.reference_report),_cohort(h.reference_report)
    dh={v[0] for v in dc.values()};hh={v[0] for v in hc.values()}
    # Opaque IDs are suite-local, so changing case labels cannot disguise
    # repeated input hashes. Within-suite duplicate input hashes do not count
    # as distinct cases; refuse them rather than let them inflate coverage.
    if len(dh)!=len(dc) or len(hh)!=len(hc) or dh & hh:
        raise ValueError("discovery and holdout require unique, disjoint case input hashes")
    return d,dp,h,hp


def _ratio(candidate,baseline,limit):
    if limit is None:
        return "not_required"
    if candidate is None or baseline is None:
        return "unknown"
    # Exact multiplication also handles a zero baseline without division.
    return "pass" if candidate*10000<=baseline*limit else "fail"


def _cohort_checks(result, policy, candidate_id, min_cases):
    by_id={s["candidate_id"]:s for s in result["candidates"]}
    if candidate_id not in by_id or candidate_id==result["baseline_candidate_id"]:
        raise ValueError("nominate a declared alternative to the baseline")
    base,candidate=by_id[result["baseline_candidate_id"]],by_id[candidate_id]
    paired=next(p for p in result["paired_comparisons"] if p["candidate_id"]==candidate_id)
    n=paired["paired_slots"]
    change_lower=sum(row["match_difference_lower"] for row in paired["rows"])
    change_upper=sum(row["match_difference_upper"] for row in paired["rows"])
    regressions=paired["regression_bounds"]
    cb=candidate["match_bounds"]
    def lower_gate(lo,hi,minimum):
        return ("pass" if lo*10000>=minimum*n else "fail" if hi*10000<minimum*n else "unknown")
    def upper_gate(lo,hi,maximum):
        return ("pass" if hi*10000<=maximum*n else "fail" if lo*10000>maximum*n else "unknown")
    coverage=all(s["recorded_attempts"]==s["scheduled_attempts"] and
                 s["match_bounds"]["unknown"]==0 and s["critical_bounds"]["unknown"]==0 and
                 s["non_delivery_bounds"]["unknown"]==0 for s in (base,candidate))
    telemetry=all(s["cost"]["total_microunits"] is not None and s["latency"]["p95_ms"] is not None
                  for s in (base,candidate))
    checks={
        "distinct_cases":"pass" if len(candidate["per_case"])>=min_cases else "fail",
        "repeats":"pass" if result["repeats"]>=policy.minimum_repeats else "fail",
        "all_categories":"pass" if not policy.require_all_categories or all(
            c["scheduled_attempts"]>0 for c in candidate["categories"].values()) else "fail",
        "candidate_trial_gates":candidate["gate_status"],
        "baseline_and_candidate_outcomes_complete":"pass" if coverage else "unknown",
        "candidate_match":lower_gate(cb["lower"],cb["upper"],policy.minimum_candidate_match_basis_points),
        "paired_match_change":lower_gate(change_lower,change_upper,policy.minimum_paired_match_change_basis_points),
        "paired_regressions":upper_gate(regressions["lower"],regressions["upper"],policy.maximum_regression_basis_points),
        "zero_candidate_critical_attempts":upper_gate(candidate["critical_bounds"]["lower"],candidate["critical_bounds"]["upper"],0),
        "zero_candidate_non_delivery":upper_gate(candidate["non_delivery_bounds"]["lower"],candidate["non_delivery_bounds"]["upper"],0),
        "complete_cost_latency":"pass" if telemetry or not policy.require_complete_cost_latency else "unknown",
        "cost_ratio":_ratio(candidate["cost"]["total_microunits"],base["cost"]["total_microunits"],
                             policy.maximum_candidate_cost_ratio_basis_points),
        "p95_ratio":_ratio(candidate["latency"]["p95_ms"],base["latency"]["p95_ms"],
                            policy.maximum_candidate_p95_ratio_basis_points),
    }
    return {
        "checks":checks,"all_required_checks_pass":all(v in ("pass","not_required") for v in checks.values()),
        "distinct_cases":len(candidate["per_case"]),"repeats":result["repeats"],"paired_attempts":n,
        "baseline_match_bounds":base["match_bounds"],"candidate_match_bounds":cb,
        "paired_match_count_change_bounds":{"lower":change_lower,"upper":change_upper},
        "paired_regression_bounds":regressions,"candidate_critical_bounds":candidate["critical_bounds"],
        "candidate_non_delivery_bounds":candidate["non_delivery_bounds"],
        "baseline_total_cost_microunits":base["cost"]["total_microunits"],
        "candidate_total_cost_microunits":candidate["cost"]["total_microunits"],
        "baseline_p95_ms":base["latency"]["p95_ms"],"candidate_p95_ms":candidate["latency"]["p95_ms"],
        "plan_sha256":result["plan_sha256"],"ledger_sha256":result["ledger_sha256"],
        "review_sha256":digest(result),
    }


def _sealed(value, key):
    return {**value,key:digest(value)}


def nominate(packet, discovery_plan, discovery_captures, holdout_plan, request):
    d,dp,h,hp=_inputs(packet,discovery_plan,holdout_plan)
    req=Nominate.model_validate(request)
    if not req.confirmed:
        raise ValueError("explicit nomination confirmation required")
    selected=next((c for c in d.candidates if c.candidate_id==req.candidate_id),None)
    if selected is None or selected.candidate_id==d.baseline_candidate_id:
        raise ValueError("nominate an alternative candidate")
    discovery=review(packet,dp,discovery_captures)
    checks=_cohort_checks(discovery,req.policy,req.candidate_id,req.policy.minimum_discovery_cases)
    if not checks["all_required_checks_pass"]:
        raise ValueError("discovery nomination does not meet all frozen selection checks")
    if len(_cohort(h.reference_report))<req.policy.minimum_holdout_cases or h.repeats<req.policy.minimum_repeats:
        raise ValueError("holdout plan is below the frozen coverage requirements")
    if req.policy.require_all_categories and len({v[1] for v in _cohort(h.reference_report).values()})!=3:
        raise ValueError("holdout plan is missing required case categories")
    return _sealed({
        "format":NOMINATION,"process_reference":d.reference_report.process_reference.model_dump(),
        "spec_sha256":d.reference_report.spec_sha256,"discovery_suite_sha256":d.reference_report.suite_sha256,
        "holdout_suite_sha256":h.reference_report.suite_sha256,"holdout_plan_sha256":hp["plan_sha256"],
        "discovery":checks,"request":req.model_dump(),"candidate":selected.model_dump(),
        "baseline_candidate_id":d.baseline_candidate_id,"currency":d.currency,
        "disjoint_declared_input_hashes":True,"independent_holdout_proven":False,
        "candidate_selected_for_deployment":False,"execution_authorized":False,
    },"nomination_sha256")


def assess(packet, discovery_plan, discovery_captures, holdout_plan, holdout_captures, nomination):
    if not isinstance(nomination,dict) or "request" not in nomination:
        raise ValueError("exact nomination required")
    recomputed=nominate(packet,discovery_plan,discovery_captures,holdout_plan,nomination["request"])
    if digest(recomputed)!=digest(nomination):
        raise ValueError("nomination or discovery evidence changed")
    req=Nominate.model_validate(recomputed["request"])
    h=review(packet,holdout_plan,holdout_captures)
    checks=_cohort_checks(h,req.policy,req.candidate_id,req.policy.minimum_holdout_cases)
    return _sealed({
        "format":ASSESSMENT,"nomination_sha256":recomputed["nomination_sha256"],
        "process_reference":recomputed["process_reference"],"spec_sha256":recomputed["spec_sha256"],
        "candidate":recomputed["candidate"],"baseline_candidate_id":recomputed["baseline_candidate_id"],
        "currency":recomputed["currency"],"policy":req.policy.model_dump(),"nominator_alias":req.nominator_alias,
        "discovery":recomputed["discovery"],"holdout":checks,
        "eligible_for_rehearsal_review":checks["all_required_checks_pass"],
        "independent_holdout_proven":False,"report_authenticity_verified":False,
        "provider_cost_verified":False,"business_outcomes_verified":False,"execution_authorized":False,
        "limitations":["Disjoint declared input hashes do not prove independent sampling or absence of semantic overlap.",
                       "The nomination pin does not prove it was retained before anyone saw holdout outcomes.",
                       "Customer-supplied scores and measurements are not independently authenticated.",
                       "Selection is a rehearsal decision only; it does not switch or deploy an agent."],
    },"assessment_sha256")


def decide(packet, discovery_plan, discovery_captures, holdout_plan, holdout_captures, nomination, request):
    req=Decide.model_validate(request)
    if not req.confirmed:
        raise ValueError("explicit selection review confirmation required")
    result=assess(packet,discovery_plan,discovery_captures,holdout_plan,holdout_captures,nomination)
    if (result["assessment_sha256"]!=req.expected_assessment_sha256 or
            result["nomination_sha256"]!=req.expected_nomination_sha256):
        raise ValueError("selection evidence changed; inspect again")
    if result["policy"]["require_distinct_reviewer_alias"] and req.reviewer_alias.casefold()==result["nominator_alias"].casefold():
        raise ValueError("a distinct declared reviewer alias is required")
    selected=req.decision=="select_for_substitution_rehearsal"
    if selected and not result["eligible_for_rehearsal_review"]:
        raise ValueError("holdout evidence does not meet all frozen selection checks")
    return _sealed({
        "format":RECEIPT,"nomination_sha256":result["nomination_sha256"],
        "assessment_sha256":result["assessment_sha256"],"process_reference":result["process_reference"],
        "spec_sha256":result["spec_sha256"],"candidate":result["candidate"],
        "baseline_candidate_id":result["baseline_candidate_id"],"request":req.model_dump(),
        "rehearsal_candidate_id":result["candidate"]["candidate_id"] if selected else None,
        "declared_reviewer_alias_distinct":req.reviewer_alias.casefold()!=result["nominator_alias"].casefold(),
        "reviewer_identity_verified":False,"independent_review_verified":False,
        "candidate_selected_for_deployment":False,"deployment_changed":False,"execution_authorized":False,
        "remaining_prerequisites":["verify runtime artifact and tool/schema compatibility",
                                   "controlled no-write rehearsal and independently checked source outcomes",
                                   "separate current authorization, rollout and rollback review"],
    },"receipt_sha256")


def operation(packet, action, bundle):
    """One strict portable bundle per operation, no caller-supplied scores."""
    base={"discovery_plan","discovery_captures","holdout_plan"}
    fields={"nominate":base|{"request"},"assess":base|{"holdout_captures","nomination"},
            "decide":base|{"holdout_captures","nomination","request"}}
    if action not in fields or not isinstance(bundle,dict) or set(bundle)!=fields[action]:
        raise ValueError("invalid selection operation bundle")
    return {"nominate":nominate,"assess":assess,"decide":decide}[action](packet,**bundle)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=("nominate","assess","decide"))
    parser.add_argument("--process-export",type=Path,required=True)
    parser.add_argument("--bundle",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        result=operation(_load(args.process_export),args.operation,_load(args.bundle))
        from .agent_procurement_replay import _private_json
        _private_json(args.out,result)
    except (OSError,ValueError,KeyError,TypeError,RecursionError,OverflowError):
        parser.exit(2,"Selection evidence incompatible, gate not met, or output unavailable. No deployment authorized.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
