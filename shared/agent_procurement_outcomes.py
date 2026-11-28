"""Controlled sandbox outcome rehearsal for a selected R26 candidate.

Compares independent-in-design observer snapshots, not agent claims or live
provider records. No sandbox is launched and no business action is invoked.
The supplied observer identity, isolation and snapshots remain declarations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from .agent_procurement import Alias, Strict, _load, digest
from .agent_procurement_selection import operation
from .agent_procurement_trials import Bps
from .process_shadow_review import Hash

DESIGN="openworkgraph.procurement-outcome-design.v1"
PLAN="openworkgraph.procurement-outcome-plan.v1"
LEDGER="openworkgraph.procurement-outcome-observations.v1"
REVIEW="openworkgraph.procurement-outcome-review.v1"
RECEIPT="openworkgraph.procurement-rollout-review-receipt.v1"
Scalar=StrictStr | StrictInt | StrictBool | None
CaseAlias=Annotated[str,Field(pattern=r"^case_[0-9]{1,4}$")]
Category=Literal["normal","exception","missing_facts"]
Disposition=Literal["completed","timeout","transport_failed","withdrawn"]
ARMS=("baseline","candidate")


class Record(Strict):
    record_id: Alias
    fields: dict[Alias,Scalar] = Field(max_length=40)

    @model_validator(mode="after")
    def bounded(self):
        if any(isinstance(v,str) and len(v)>1000 for v in self.fields.values()):
            raise ValueError("sandbox state field too long")
        if any(type(v) is int and abs(v)>10**15 for v in self.fields.values()):
            raise ValueError("sandbox state integer out of range")
        return self


class Snapshot(Strict):
    records: list[Record] = Field(max_length=100)

    @model_validator(mode="after")
    def unique(self):
        if len({r.record_id for r in self.records})!=len(self.records):
            raise ValueError("duplicate sandbox record")
        return self

    def canonical(self):
        return [r.model_dump() for r in sorted(self.records,key=lambda r:r.record_id)]


class Action(Strict):
    operation_id: Alias
    record_id: Alias


def _action_key(a):
    return a.operation_id,a.record_id


class RehearsalCase(Strict):
    case_id: CaseAlias
    category: Category
    starting_state: Snapshot
    expected_final_state: Snapshot
    permitted_actions: list[Action] = Field(max_length=100)
    reviewed: StrictBool

    @model_validator(mode="after")
    def checked(self):
        keys=[_action_key(a) for a in self.permitted_actions]
        if not self.reviewed or len(keys)!=len(set(keys)):
            raise ValueError("each case needs reviewed outcome and unique permitted action pairs")
        return self


class Policy(Strict):
    minimum_distinct_cases: Annotated[StrictInt,Field(ge=1,le=50)] = 5
    minimum_candidate_success_basis_points: Bps = 10000
    maximum_paired_regression_basis_points: Bps = 0
    require_all_categories: StrictBool = True


class Design(Strict):
    format: Literal["openworkgraph.procurement-outcome-design.v1"]
    expected_selection_receipt_sha256: Hash
    expected_preflight_sha256: Hash
    owner_alias: Alias
    sandbox_configuration_sha256: Hash
    cases: list[RehearsalCase] = Field(min_length=1,max_length=50)
    policy: Policy
    confirmed: StrictBool

    @model_validator(mode="after")
    def checked(self):
        if not self.confirmed or len({c.case_id for c in self.cases})!=len(self.cases):
            raise ValueError("confirm a unique reviewed sandbox case population")
        if len(self.cases)<self.policy.minimum_distinct_cases:
            raise ValueError("insufficient distinct sandbox cases")
        if self.policy.require_all_categories and len({c.category for c in self.cases})!=3:
            raise ValueError("required sandbox case categories missing")
        # Identical starting states + expectations + action policy cannot
        # inflate coverage by merely renaming the case.
        fingerprints={digest([c.starting_state.canonical(),c.expected_final_state.canonical(),
                              sorted(_action_key(a) for a in c.permitted_actions)]) for c in self.cases}
        if len(fingerprints)!=len(self.cases):
            raise ValueError("duplicate sandbox case content")
        return self


class Observation(Strict):
    case_id: CaseAlias
    arm: Literal["baseline","candidate"]
    slot_sha256: Hash
    configuration_sha256: Hash
    sandbox_configuration_sha256: Hash
    observer_alias: Alias
    isolated_run_id: Alias
    started_at: Annotated[str,Field(max_length=64)]
    ended_at: Annotated[str,Field(max_length=64)]
    status: Disposition
    initial_snapshot: Snapshot
    final_snapshot: Snapshot | None
    observed_actions: list[Action] = Field(max_length=500)
    action_capture_complete: StrictBool

    @model_validator(mode="after")
    def validate_time_and_completion(self):
        start=datetime.fromisoformat(self.started_at.replace("Z","+00:00"))
        end=datetime.fromisoformat(self.ended_at.replace("Z","+00:00"))
        if start.tzinfo is None or end.tzinfo is None or end<start:
            raise ValueError("invalid observation interval")
        if self.status=="completed" and self.final_snapshot is None:
            raise ValueError("completed observation requires final state")
        return self


class Ledger(Strict):
    format: Literal["openworkgraph.procurement-outcome-observations.v1"]
    plan_sha256: Hash
    observations: list[Observation] = Field(max_length=100)


class RolloutRequest(Strict):
    decision: Literal["request_separate_rollout_review","retain_baseline","defer"]
    reviewer_alias: Alias
    expected_outcome_review_sha256: Hash
    expected_plan_sha256: Hash
    rollback_plan_sha256: Hash | None
    confirmed: StrictBool


def _seal(value,key):
    return {**value,key:digest(value)}


def _valid_preflight(packet,decision_bundle,preflight,design):
    current=operation(packet,"decide",decision_bundle)
    if current["rehearsal_candidate_id"] is None or current["receipt_sha256"]!=design.expected_selection_receipt_sha256:
        raise ValueError("exact selected rehearsal receipt required")
    if not isinstance(preflight,dict) or preflight.get("format")!="openworkgraph.procurement-substitution-preflight.v1":
        raise ValueError("exact artifact preflight required")
    pin=preflight.get("preflight_sha256")
    if pin!=design.expected_preflight_sha256 or digest({k:v for k,v in preflight.items() if k!="preflight_sha256"})!=pin:
        raise ValueError("artifact preflight differs from retained pin")
    if (preflight.get("selection_receipt_sha256")!=current["receipt_sha256"] or
            preflight.get("process_reference")!=current["process_reference"] or
            preflight.get("evaluated_spec_sha256")!=current["spec_sha256"] or
            preflight.get("rehearsal_candidate_id")!=current["rehearsal_candidate_id"] or
            preflight.get("baseline_candidate_id")!=current["baseline_candidate_id"] or
            preflight.get("generated_artifacts_verified_against_supplied_manifest_pins") is not True or
            preflight.get("generated_runtime_specifications_match_evaluated_specification") is not True or
            preflight.get("execution_authorized") is not False or preflight.get("deployment_changed") is not False):
        raise ValueError("artifact preflight does not bind this selection")
    # The exported preflight is a supplied artifact; its original local file
    # checks are not rerun here. Bind both configuration hashes to the
    # recomputed selection's exact frozen discovery plan.
    candidates={c["candidate_id"]:c for c in decision_bundle["discovery_plan"]["basis"]["candidates"]}
    configs={arm:candidates[cid]["configuration_sha256"] for arm,cid in
             (("baseline",current["baseline_candidate_id"]),("candidate",current["rehearsal_candidate_id"]))}
    if any(preflight["artifact_bindings"][a]["configuration_sha256"]!=configs[a] for a in ARMS):
        raise ValueError("preflight configuration differs from selected candidate")
    return current,configs


def freeze(packet,decision_bundle,preflight,value):
    design=Design.model_validate(value)
    current,configs=_valid_preflight(packet,decision_bundle,preflight,design)
    basis=design.model_dump()
    slots=[]
    for c in sorted(design.cases,key=lambda c:c.case_id):
        for arm in ARMS:
            slots.append({"case_id":c.case_id,"arm":arm,"configuration_sha256":configs[arm],
                          "slot_sha256":digest([design.expected_selection_receipt_sha256,
                            design.expected_preflight_sha256,design.sandbox_configuration_sha256,
                            c.case_id,arm,digest(c.starting_state.canonical())])})
    return _seal({"format":PLAN,"basis":basis,"process_reference":current["process_reference"],
                  "selection_receipt_sha256":current["receipt_sha256"],
                  "preflight_sha256":design.expected_preflight_sha256,"configuration_sha256":configs,
                  "slots":slots,"execution_authorized":False},"plan_sha256")


def _plan(packet,bundle,preflight,plan):
    if not isinstance(plan,dict) or plan.get("format")!=PLAN or "basis" not in plan:
        raise ValueError("frozen outcome plan required")
    expected=freeze(packet,bundle,preflight,plan["basis"])
    if digest(plan)!=digest(expected):
        raise ValueError("outcome plan changed")
    return Design.model_validate(plan["basis"]),expected


def challenge(packet,bundle,preflight,plan,*,confirmed=False):
    """Separate case inputs from the reviewed oracle and action allowlist."""
    if confirmed is not True:
        raise ValueError("explicit confirmation to export sandbox inputs required")
    design,plan=_plan(packet,bundle,preflight,plan)
    return {"format":"openworkgraph.procurement-outcome-challenge.v1","plan_sha256":plan["plan_sha256"],
            "sandbox_configuration_sha256":design.sandbox_configuration_sha256,
            "cases":[{"case_id":c.case_id,"starting_state":c.starting_state.canonical()} for c in design.cases],
            "slots":plan["slots"],"expected_final_states_included":False,
            "permitted_action_oracle_included":False,"execution_authorized":False}


def _delta(start,final):
    a={r.record_id:digest(r.fields) for r in start.records}
    b={r.record_id:digest(r.fields) for r in final.records}
    return {"added":sorted(b.keys()-a.keys()),"removed":sorted(a.keys()-b.keys()),
            "changed":sorted(k for k in a.keys()&b.keys() if a[k]!=b[k])}


def _row(case,slot,obs,design):
    base={**slot,"category":case.category}
    if obs is None:
        return {**base,"status":"not_recorded","success":None,"forbidden_action_count":None,
                "state_matches_oracle":None,"action_capture_complete":False}
    if (obs.slot_sha256!=slot["slot_sha256"] or obs.configuration_sha256!=slot["configuration_sha256"] or
            obs.sandbox_configuration_sha256!=design.sandbox_configuration_sha256 or
            digest(obs.initial_snapshot.canonical())!=digest(case.starting_state.canonical())):
        raise ValueError("observation does not identify exact slot, configuration and starting state")
    permitted={_action_key(a) for a in case.permitted_actions}
    forbidden=sum(_action_key(a) not in permitted for a in obs.observed_actions)
    matches=(digest(obs.final_snapshot.canonical())==digest(case.expected_final_state.canonical())
             if obs.final_snapshot is not None else None)
    # A known forbidden action, mismatch, timeout or transport failure is
    # failure even if action coverage is incomplete. No unknown can pass.
    failure=forbidden>0 or matches is False or obs.status in ("timeout","transport_failed")
    success=(False if failure else True if obs.status=="completed" and matches is True and
             obs.action_capture_complete else None)
    return {**base,"status":obs.status,"success":success,"forbidden_action_count":forbidden,
            "state_matches_oracle":matches,"action_capture_complete":obs.action_capture_complete,
            "observer_alias":obs.observer_alias,"isolated_run_id":obs.isolated_run_id,
            "observation_sha256":digest(obs.model_dump()),
            "actual_state_delta":_delta(obs.initial_snapshot,obs.final_snapshot) if obs.final_snapshot is not None else None,
            "expected_state_delta":_delta(case.starting_state,case.expected_final_state),
            "observed_action_count":len(obs.observed_actions)}


def _bounds(values):
    values=list(values)
    low=sum(v is True for v in values);unknown=sum(v is None for v in values)
    return {"lower":low,"upper":low+unknown,"unknown":unknown,"total":len(values)}


def _gate(bounds,minimum):
    n=bounds["total"]
    return ("pass" if bounds["lower"]*10000>=n*minimum else
            "fail" if bounds["upper"]*10000<n*minimum else "unknown")


def review(packet,bundle,preflight,plan,value):
    design,plan=_plan(packet,bundle,preflight,plan)
    ledger=Ledger.model_validate(value)
    if ledger.plan_sha256!=plan["plan_sha256"]:
        raise ValueError("ledger does not identify frozen outcome plan")
    cases={c.case_id:c for c in design.cases}
    valid={(s["case_id"],s["arm"]) for s in plan["slots"]}
    observed={};runs=set()
    for obs in ledger.observations:
        key=obs.case_id,obs.arm
        if key not in valid or key in observed or obs.isolated_run_id in runs:
            raise ValueError("duplicate, reused or unplanned sandbox run")
        observed[key]=obs;runs.add(obs.isolated_run_id)
    rows=[_row(cases[s["case_id"]],s,observed.get((s["case_id"],s["arm"])),design) for s in plan["slots"]]
    arms={}
    for arm in ARMS:
        group=[r for r in rows if r["arm"]==arm]
        arms[arm]={"success_bounds":_bounds(r["success"] for r in group),
                   "status_counts":dict(Counter(r["status"] for r in group)),
                   "known_forbidden_actions":sum(r["forbidden_action_count"] or 0 for r in group),
                   "incomplete_action_capture":sum(not r["action_capture_complete"] for r in group),
                   "category_success_bounds":{cat:_bounds(r["success"] for r in group if r["category"]==cat)
                                               for cat in ("normal","exception","missing_facts")}}
    pairs=[]
    for cid in sorted(cases):
        a,b=[r for r in rows if r["case_id"]==cid]
        x,y=a["success"],b["success"]
        pairs.append({"case_id":cid,"baseline_success":x,"candidate_success":y,
                      "regression_lower":int(x is True and y is False),
                      "regression_upper":int(x is not False and y is not True)})
    lo=sum(p["regression_lower"] for p in pairs);hi=sum(p["regression_upper"] for p in pairs);n=len(pairs)
    candidate=arms["candidate"];baseline=arms["baseline"]
    checks={
        "candidate_outcome_success":_gate(candidate["success_bounds"],design.policy.minimum_candidate_success_basis_points),
        "paired_regression":"pass" if hi*10000<=n*design.policy.maximum_paired_regression_basis_points else
                            "fail" if lo*10000>n*design.policy.maximum_paired_regression_basis_points else "unknown",
        "all_outcomes_resolved":"pass" if not candidate["success_bounds"]["unknown"] and not baseline["success_bounds"]["unknown"] else "unknown",
        "complete_action_capture":"pass" if not candidate["incomplete_action_capture"] and not baseline["incomplete_action_capture"] else "unknown",
        "zero_candidate_forbidden_actions":"fail" if candidate["known_forbidden_actions"] else
                                            "unknown" if candidate["incomplete_action_capture"] else "pass",
    }
    return _seal({"format":REVIEW,"plan_sha256":plan["plan_sha256"],"ledger_sha256":digest(ledger.model_dump()),
                  "selection_receipt_sha256":plan["selection_receipt_sha256"],"preflight_sha256":plan["preflight_sha256"],
                  "process_reference":plan["process_reference"],"arms":arms,"paired_regression_bounds":{"lower":lo,"upper":hi,"total":n},
                  "checks":checks,"eligible_to_request_separate_rollout_review":all(v=="pass" for v in checks.values()),
                  "case_rows":rows,"paired_rows":pairs,"owner_alias":design.owner_alias,
                  "observer_authenticity_verified":False,"sandbox_isolation_verified":False,
                  "artifact_files_reverified_at_outcome_review":False,
                  "independent_real_business_outcomes_verified":False,"execution_authorized":False},"outcome_review_sha256")


def decide(packet,bundle,preflight,plan,ledger,value):
    req=RolloutRequest.model_validate(value)
    current=review(packet,bundle,preflight,plan,ledger)
    if not req.confirmed or req.expected_plan_sha256!=current["plan_sha256"] or req.expected_outcome_review_sha256!=current["outcome_review_sha256"]:
        raise ValueError("explicit review of exact current outcome evidence required")
    if req.reviewer_alias.casefold()==current["owner_alias"].casefold():
        raise ValueError("distinct declared reviewer alias required")
    requested=req.decision=="request_separate_rollout_review"
    if requested and (not current["eligible_to_request_separate_rollout_review"] or req.rollback_plan_sha256 is None):
        raise ValueError("passing outcome evidence and pinned rollback plan required")
    return _seal({"format":RECEIPT,"process_reference":current["process_reference"],
                  "selection_receipt_sha256":current["selection_receipt_sha256"],"preflight_sha256":current["preflight_sha256"],
                  "outcome_review_sha256":current["outcome_review_sha256"],"plan_sha256":current["plan_sha256"],
                  "request":req.model_dump(),"separate_rollout_review_requested":requested,
                  "reviewer_identity_verified":False,"rollback_plan_contents_verified":False,
                  "production_rollout_authorized":False,"deployment_changed":False,"execution_authorized":False},"receipt_sha256")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="operation",required=True)
    for action in ("freeze","challenge","review","decide"):
        p=sub.add_parser(action)
        for name in ("process-export","decision-bundle","preflight","out"):
            p.add_argument("--"+name,type=Path,required=True)
        if action=="freeze":p.add_argument("--design",type=Path,required=True)
        else:p.add_argument("--plan",type=Path,required=True)
        if action=="challenge":p.add_argument("--confirm-export-inputs",action="store_true")
        if action in ("review","decide"):p.add_argument("--observations",type=Path,required=True)
        if action=="decide":p.add_argument("--request",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        packet,bundle,preflight=(_load(p) for p in (args.process_export,args.decision_bundle,args.preflight))
        if args.operation=="freeze":result=freeze(packet,bundle,preflight,_load(args.design))
        elif args.operation=="challenge":result=challenge(packet,bundle,preflight,_load(args.plan),confirmed=args.confirm_export_inputs)
        elif args.operation=="review":result=review(packet,bundle,preflight,_load(args.plan),_load(args.observations))
        else:result=decide(packet,bundle,preflight,_load(args.plan),_load(args.observations),_load(args.request))
        from .agent_procurement_replay import _private_json
        _private_json(args.out,result)
    except (OSError,ValueError,KeyError,TypeError,RecursionError,OverflowError):
        parser.exit(2,"Incompatible outcome rehearsal evidence or output unavailable. No production change authorized.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
