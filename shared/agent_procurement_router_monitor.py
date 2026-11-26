"""Finite, multi-window monitoring of declared router-pilot evidence.

The retained initial expansion referral and exact same configurations are
required. Cases are disjoint across windows and the initial pilot. The
checkpoint protocol compares cumulative snapshots against a retained prior
pin; it is not an authenticated append-only store or live traffic monitor.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from . import agent_procurement_router_pilot as pilot
from . import agent_procurement_router_rehearsal as router
from .agent_procurement import Alias, Strict, _load, digest
from .agent_procurement_portfolio import _combine
from .process_shadow_review import Hash

PLAN="openworkgraph.procurement-router-monitor-plan.v1"
SNAPSHOT="openworkgraph.procurement-router-monitor-snapshot.v1"
REPORT="openworkgraph.procurement-router-monitor-report.v1"
RECEIPT="openworkgraph.procurement-router-monitor-receipt.v1"
MAX_CASES=1500
MAX_WINDOWS=12


class Window(Strict):
    window_id: Alias
    start_ms: pilot.Epoch
    end_exclusive_ms: pilot.Epoch
    allocation_seed_sha256: Hash
    cases: list[pilot.PilotCase]=Field(min_length=2,max_length=500)


class FreezeRequest(Strict):
    monitor_id: Alias
    coordinator_alias: Alias
    windows: list[Window]=Field(min_length=1,max_length=MAX_WINDOWS)
    maximum_reporting_delay_ms: Annotated[StrictInt,Field(ge=0,le=30*24*3600000)]
    external_monitoring_approval_sha256: Hash
    confirmed: StrictBool

    @model_validator(mode="after")
    def valid(self):
        if not self.confirmed:
            raise ValueError("explicit monitoring confirmation required")
        if len({w.window_id for w in self.windows})!=len(self.windows):
            raise ValueError("duplicate monitoring window")
        if sum(len(w.cases) for w in self.windows)>MAX_CASES:
            raise ValueError("monitoring cohort too large")
        last=None;seen=set()
        for w in self.windows:
            if w.end_exclusive_ms<=w.start_ms or (last is not None and w.start_ms!=last):
                raise ValueError("monitoring windows must be ordered and contiguous")
            last=w.end_exclusive_ms
            for c in w.cases:
                if c.case_token_sha256 in seen:
                    raise ValueError("case repeated across monitoring windows")
                seen.add(c.case_token_sha256)
        return self


class WindowCapture(Strict):
    window_id: Alias
    observations: list[pilot.Observation]=Field(max_length=500)


class Snapshot(Strict):
    format: Literal["openworkgraph.procurement-router-monitor-snapshot.v1"]
    monitor_plan_sha256: Hash
    as_of_ms: pilot.Epoch
    windows: list[WindowCapture]=Field(max_length=MAX_WINDOWS)

    @model_validator(mode="after")
    def unique(self):
        if len({w.window_id for w in self.windows})!=len(self.windows):
            raise ValueError("duplicate window capture")
        obs=[o for w in self.windows for o in w.observations]
        if len(obs)>MAX_CASES:
            raise ValueError("monitoring evidence too large")
        for key in ("slot_id","case_token_sha256","source_evidence_sha256"):
            if len({getattr(o,key) for o in obs})!=len(obs):
                raise ValueError("duplicate case, slot or source evidence reference")
        return self


class Decision(Strict):
    disposition: Literal["refer_for_continued_monitoring_review","refer_for_rollback_review","hold"]
    reviewer_alias: Alias
    expected_monitor_plan_sha256: Hash
    expected_report_sha256: Hash
    confirmed: StrictBool


def _initial(packet,initial):
    keys={"upstream","pilot_plan","captures","request","receipt"}
    if not isinstance(initial,dict) or set(initial)!=keys:
        raise ValueError("exact initial pilot and expansion receipt required")
    upstream=initial["upstream"]
    upstream_receipt=pilot._change_receipt(packet,upstream)
    req=pilot.FreezeRequest.model_validate(initial["pilot_plan"]["request"])
    plan=pilot._freeze_verified(upstream,req,upstream_receipt)
    if digest(plan)!=digest(initial["pilot_plan"]):
        raise ValueError("initial pilot plan changed")
    result=pilot._review_verified(plan,initial["captures"])
    receipt=pilot._decide_verified(plan,result,pilot.Decision.model_validate(initial["request"]))
    if digest(receipt)!=digest(initial["receipt"]) or receipt["request"]["disposition"]!="refer_for_expanded_pilot_review":
        raise ValueError("positive initial pilot expansion referral required")
    return upstream,upstream_receipt,req,plan,result,receipt


def freeze(packet,initial,request):
    req=FreezeRequest.model_validate(request)
    upstream,upstream_receipt,prior,plan,report,receipt=_initial(packet,initial)
    if req.windows[0].start_ms<max(prior.window_end_exclusive_ms,report["as_of_ms"]):
        raise ValueError("monitoring must follow the reviewed initial pilot snapshot")
    old={x.case_token_sha256 for x in prior.cases}
    windows=[]
    for w in req.windows:
        if old & {c.case_token_sha256 for c in w.cases}:
            raise ValueError("monitor case overlaps initial pilot")
        child=pilot.FreezeRequest.model_validate({**prior.model_dump(),
            "pilot_id":digest([req.monitor_id,w.window_id]),"coordinator_alias":req.coordinator_alias,
            "window_start_ms":w.start_ms,"window_end_exclusive_ms":w.end_exclusive_ms,
            "allocation_seed_sha256":w.allocation_seed_sha256,
            "cases":[c.model_dump() for c in w.cases],
            "external_controlled_pilot_approval_sha256":req.external_monitoring_approval_sha256})
        windows.append({"window_id":w.window_id,"plan":pilot._freeze_verified(upstream,child,upstream_receipt)})
    return router._seal({"format":PLAN,"request":req.model_dump(),
        "initial_pilot_plan_sha256":plan["pilot_plan_sha256"],"initial_report_sha256":report["report_sha256"],
        "initial_receipt_sha256":receipt["receipt_sha256"],
        "rollback_target_configuration_sha256":prior.rollback_target_configuration_sha256,
        "currency":plan["currency"],"windows":windows,"scheduled_cases":sum(len(w.cases) for w in req.windows),
        "external_approval_verified":False,"execution_authorized":False},"monitor_plan_sha256")


def _normalize(raw):
    s=Snapshot.model_validate(raw)
    # Canonical ordering avoids false changes caused only by input order.
    return {**s.model_dump(),"windows":[{"window_id":w.window_id,
        "observations":[o.model_dump() for o in sorted(w.observations,key=lambda o:o.slot_id)]}
        for w in sorted(s.windows,key=lambda w:w.window_id)]}


def _index(snapshot):
    return {(w["window_id"],o["slot_id"]):o for w in snapshot["windows"] for o in w["observations"]}


def _check_extension(previous,current):
    if previous["monitor_plan_sha256"]!=current["monitor_plan_sha256"] or current["as_of_ms"]<=previous["as_of_ms"]:
        raise ValueError("checkpoint must advance the same monitoring plan")
    old,new=_index(previous),_index(current)
    for k,o in old.items():
        # Immutable source evidence: no silent reclassification, deletion,
        # cost reduction or adverse event erasure. Corrections need separate
        # external reconciliation, not an overwritten checkpoint.
        if k not in new or digest(o)!=digest(new[k]):
            raise ValueError("prior monitoring observation removed or changed")


def _pooled_categories(rows,policy):
    result=[]
    for cat in sorted({r["category"] for r in rows}):
        a,b=(pilot._summary([r for r in rows if r["arm"]==arm and r["category"]==cat]) for arm in ("incumbent","challenger"))
        result.append({"category":cat,"incumbent":a,"challenger":b,**pilot._comparison(a,b,policy)})
    return result


def _snapshot_review(plan,raw,initial_sources):
    snapshot=_normalize(raw);cut=snapshot["as_of_ms"]
    if snapshot["monitor_plan_sha256"]!=plan["monitor_plan_sha256"]:
        raise ValueError("snapshot does not identify monitoring plan")
    req=FreezeRequest.model_validate(plan["request"])
    if cut<req.windows[0].start_ms:
        raise ValueError("snapshot predates monitoring schedule")
    captures={w["window_id"]:w["observations"] for w in snapshot["windows"]}
    if set(captures)-{w["window_id"] for w in plan["windows"]}:
        raise ValueError("unknown monitoring window")
    if initial_sources & {o["source_evidence_sha256"] for rows in captures.values() for o in rows}:
        raise ValueError("source reference reused from initial pilot")
    rows=[];windows=[];triggers=[];prefix_checks=[]
    policy=pilot.Policy.model_validate(plan["windows"][0]["plan"]["request"]["policy"])
    for item in plan["windows"]:
        wid,p=item["window_id"],item["plan"]
        start,end=p["request"]["window_start_ms"],p["request"]["window_end_exclusive_ms"]
        observations=captures.get(wid,[])
        if cut<start:
            if observations:raise ValueError("observations in future monitoring window")
            windows.append({"window_id":wid,"state":"future","start_ms":start,"end_exclusive_ms":end,
                            "scheduled_cases":len(p["slots"]),"recorded_cases":0})
            continue
        result=pilot._review_verified(p,{"format":pilot.LEDGER,"pilot_plan_sha256":p["pilot_plan_sha256"],
                                          "as_of_ms":cut,"observations":observations})
        rows.extend(result["rows"])
        overdue=cut>=end+req.maximum_reporting_delay_ms and any(v=="unknown" for v in result["checks"].values())
        state="rollback_review" if result["rollback_review_triggers"] else "overdue" if overdue else (
            "complete_pass" if result["eligible_for_expanded_pilot_review"] else "hold" if cut>=end else "open")
        for reason in result["rollback_review_triggers"]:triggers.append({"window_id":wid,"reason":reason})
        windows.append({"window_id":wid,"state":state,"start_ms":start,"end_exclusive_ms":end,
            "scheduled_cases":result["scheduled_cases"],"recorded_cases":result["recorded_cases"],
            "checks":result["checks"],"arms":result["arms"],"category_reviews":result["category_reviews"],
            "pilot_report_sha256":result["report_sha256"]})
        if cut>=end:
            prefix=_pooled_categories(rows,policy)
            status=_combine([c["status"] for c in prefix])
            prefix_checks.append({"through_window_id":wid,"status":status,
                                  "category_status":{c["category"]:c["status"] for c in prefix}})
            if status=="fail":triggers.append({"window_id":wid,"reason":"closed_prefix_category_policy_failure"})
    pooled=[];groups={}
    if rows:
        groups={arm:pilot._summary([r for r in rows if r["arm"]==arm]) for arm in ("incumbent","challenger")}
        pooled=_pooled_categories(rows,policy)
        if any(c["status"]=="fail" for c in pooled):
            triggers.append({"window_id":None,"reason":"cumulative_category_policy_failure"})
    completed=sum(w["state"]=="complete_pass" for w in windows)
    # Future windows do not count as missing yet, but an unfinished horizon
    # never yields the final continuation referral. Any window failure sticks
    # even when pooled averages recover.
    ready=completed==len(windows) and bool(pooled) and all(c["status"]=="pass" for c in pooled) and not triggers
    state="rollback_review" if triggers else "continued_monitoring_review" if ready else "hold"
    return {"snapshot_sha256":digest(snapshot),"as_of_ms":cut,"windows":windows,"arms":groups,
            "cumulative_category_reviews":pooled,"closed_prefix_checks":prefix_checks,"scheduled_cases":plan["scheduled_cases"],
            "started_window_cases":len(rows),"recorded_cases":sum(r["status"]!="not_recorded" for r in rows),
            "completed_passing_windows":completed,"rollback_review_triggers":triggers,
            "recommended_review":state,"eligible_for_continued_monitoring_review":ready}


def review(packet,initial,monitor_plan,snapshot,previous_checkpoint):
    plan=freeze(packet,initial,monitor_plan["request"])
    if digest(plan)!=digest(monitor_plan):
        raise ValueError("monitoring plan or initial evidence changed")
    initial_sources={o["source_evidence_sha256"] for o in initial["captures"]["observations"]}
    return _review_verified(plan,snapshot,initial_sources,previous_checkpoint)


def _review_verified(plan,snapshot,initial_sources,previous_checkpoint):
    """Internal: use only an already recomputed and retained plan."""
    latest=_snapshot_review(plan,snapshot,initial_sources)
    previous_pin=None;prior_overdue=[];prior_triggers=[]
    if previous_checkpoint is not None:
        if not isinstance(previous_checkpoint,dict) or set(previous_checkpoint)!={"snapshot","expected_snapshot_sha256"}:
            raise ValueError("exact retained previous checkpoint required")
        previous=_normalize(previous_checkpoint["snapshot"])
        if digest(previous)!=previous_checkpoint["expected_snapshot_sha256"]:
            raise ValueError("prior snapshot pin mismatch")
        prior_review=_snapshot_review(plan,previous,initial_sources)
        prior_overdue=[w["window_id"] for w in prior_review["windows"] if w["state"]=="overdue"]
        prior_triggers=prior_review["rollback_review_triggers"]
        _check_extension(previous,_normalize(snapshot))
        previous_pin=previous_checkpoint["expected_snapshot_sha256"]
    if prior_triggers:
        latest["eligible_for_continued_monitoring_review"]=False
        latest["recommended_review"]="rollback_review"
    return router._seal({"format":REPORT,"monitor_plan_sha256":plan["monitor_plan_sha256"],
        "initial_receipt_sha256":plan["initial_receipt_sha256"],"currency":plan["currency"],
        "rollback_target_configuration_sha256":plan["rollback_target_configuration_sha256"],
        "previous_snapshot_sha256":previous_pin,"prior_checkpoint_extension_verified":previous_pin is not None,
        "overdue_windows_at_previous_checkpoint":prior_overdue,"rollback_triggers_at_previous_checkpoint":prior_triggers,
        **latest,"continuous_history_independently_verified":False,"live_telemetry_verified":False,
        "deployment_changed":False,"execution_authorized":False,
        "limitations":["Finite declared monitoring schedule; no live ingestion, background polling or deployment actions.",
            "Previous snapshot validation protects only the retained pin provided by the caller; no authoritative persisted checkpoint or independent chronology.",
            "Existing observations cannot be modified or removed; late new observations may fill previously missing slots if the event timestamp was in the frozen window.",
            "Future windows are excluded from cumulative rates. Every started window includes its entire enrolled cohort; no survivor-only comparisons.",
            "Window-specific failures cannot be hidden by pooled performance. Cost and outcome data remain declarations, not causal effects or realized ROI."]},"report_sha256")


def decide(packet,initial,monitor_plan,snapshot,previous_checkpoint,request):
    req=Decision.model_validate(request)
    report=review(packet,initial,monitor_plan,snapshot,previous_checkpoint)
    if not req.confirmed or req.expected_monitor_plan_sha256!=report["monitor_plan_sha256"] or req.expected_report_sha256!=report["report_sha256"]:
        raise ValueError("decision confirmation or evidence pin mismatch")
    if req.reviewer_alias.casefold()==monitor_plan["request"]["coordinator_alias"].casefold():
        raise ValueError("distinct declared reviewer alias required")
    if req.disposition=="refer_for_continued_monitoring_review" and not report["eligible_for_continued_monitoring_review"]:
        raise ValueError("monitor evidence blocks positive referral")
    return router._seal({"format":RECEIPT,"request":req.model_dump(),
        "monitor_plan_sha256":report["monitor_plan_sha256"],"report_sha256":report["report_sha256"],
        "snapshot_sha256":report["snapshot_sha256"],"previous_snapshot_sha256":report["previous_snapshot_sha256"],
        "recommended_review":report["recommended_review"],
        "rollback_target_configuration_sha256":report["rollback_target_configuration_sha256"],
        "reviewer_identity_verified":False,"deployment_changed":False,"execution_authorized":False},"receipt_sha256")


def operation(packet,action,doc):
    if not isinstance(doc,dict):raise ValueError("exact monitor bundle required")
    if action=="router-monitor-freeze" and set(doc)=={"initial","request"}:
        return freeze(packet,doc["initial"],doc["request"])
    keys={"initial","monitor_plan","snapshot","previous_checkpoint"}
    if action=="router-monitor-review" and set(doc)==keys:
        return review(packet,doc["initial"],doc["monitor_plan"],doc["snapshot"],doc["previous_checkpoint"])
    if action=="router-monitor-decide" and set(doc)==keys|{"request"}:
        return decide(packet,doc["initial"],doc["monitor_plan"],doc["snapshot"],doc["previous_checkpoint"],doc["request"])
    raise ValueError("exact monitor bundle required")


def main(argv=None):
    from .agent_procurement_replay import _output
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation",choices=["router-monitor-freeze","router-monitor-review","router-monitor-decide"])
    p.add_argument("--process-export",type=Path,required=True)
    p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--out-dir",type=Path,required=True)
    a=p.parse_args(argv)
    try:_output(a.out_dir,{"result.json":operation(_load(a.process_export),a.operation,_load(a.bundle))})
    except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        p.exit(2,"Invalid monitoring evidence or output unavailable. No deployment changed.\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
