"""Frozen prospective procurement trial accounting with minimized reports."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from evals.agent_procurement import digest
from evals.agent_procurement_trials import freeze,review,_latency
from test_agent_procurement import study


@pytest.fixture
def trial(study):
    packet,req=study
    design={"format":"openworkgraph.procurement-trial-design.v1",
            "reference_report":req["candidates"][0]["report"],
            "baseline_candidate_id":"baseline","currency":"USD","repeats":2,"schedule_seed":17,
            "confirmed":True,"gates":{"minimum_match_basis_points":5000,"require_all_categories":True},
            "candidates":[{**{k:c[k] for k in ("candidate_id","provider","model","runtime")},
                           "configuration_sha256":digest(c["candidate_id"])} for c in req["candidates"][:2]]}
    plan=freeze(packet,design)
    return packet,req,design,plan


def capture(req,cid,repeat,states=None,measurements=None):
    c=next(c for c in req["candidates"] if c["candidate_id"]==cid)
    report=deepcopy(c["report"])
    report["results_sha256"]=digest([cid,repeat])
    attempts=[]
    for row,measurement in zip(report["cases"],c["measurements"]):
        case=row["case_id"];state=(states or {}).get(case,"completed")
        if state!="completed":
            row.update(reason="invalid_or_stale_plan",matches_reviewed_expectation=False,review_required=True)
        if state!="not_recorded":
            costs=(measurements or {}).get(case,(measurement["cost_microunits"],measurement["elapsed_ms"]))
            attempts.append({"case_id":case,"status":state,"cost_microunits":costs[0],"elapsed_ms":costs[1]})
    report["matching_case_count"]=sum(c["matches_reviewed_expectation"] for c in report["cases"])
    report["review_required"]=report["matching_case_count"]!=report["case_count"]
    report["reason_counts"]={c["reason"]:sum(r["reason"]==c["reason"] for r in report["cases"]) for c in report["cases"]}
    return {"candidate_id":cid,"repeat":repeat,"configuration_sha256":digest(cid),
            "report":report if any(a["status"]=="completed" for a in attempts) else None,"attempts":attempts}


def ledger(plan,captures):
    return {"format":"openworkgraph.procurement-trial-captures.v1","plan_sha256":plan["plan_sha256"],"captures":captures}


def test_plan_balanced_rotation_and_exact_freeze(trial):
    packet,_,design,plan=trial
    assert plan==freeze(packet,design)
    assert len(plan["slots"])==12 and len({r["slot_id"] for r in plan["slots"]})==12
    for cid in ("baseline","cheap"):
        for case in ("case_1","case_2","case_3"):
            assert sorted(r["position"] for r in plan["slots"] if r["case_id"]==case and r["candidate_id"]==cid)==[1,2]
    changed=deepcopy(plan);changed["slots"][0]["position"]=99
    with pytest.raises(ValueError,match="changed"):review(packet,changed,ledger(changed,[]))
    changed=deepcopy(design);changed["confirmed"]=False
    with pytest.raises(ValueError):freeze(packet,changed)


def test_all_enrolled_denominator_includes_timeouts_and_missing_repeats(trial):
    packet,req,_,plan=trial
    captures=[capture(req,"baseline",1),capture(req,"baseline",2),
              capture(req,"cheap",1,{"case_2":"timeout","case_3":"withdrawn"},{"case_2":(7,500),"case_3":(None,None)})]
    result=review(packet,plan,ledger(plan,captures))
    cheap=result["candidates"][1]
    assert cheap["scheduled_attempts"]==6 and cheap["recorded_attempts"]==3
    assert cheap["status_counts"]=={"completed":1,"timeout":1,"transport_failed":0,
                                  "invalid_output":0,"withdrawn":1,"not_recorded":3}
    assert cheap["match_bounds"]=={"lower":1,"upper":5,"unknown":4,"total":6}
    assert cheap["non_delivery_bounds"]=={"lower":1,"upper":5,"unknown":4,"total":6}
    assert cheap["critical_bounds"]["lower"]==0  # null-plan placeholder not a returned critical plan
    assert cheap["cost"]["lower_bound_microunits"]==12
    assert cheap["cost"]["total_microunits"] is None
    assert cheap["latency"]["p95_lower_ms"]==500 and cheap["latency"]["p95_upper_ms"] is None
    assert cheap["gates"]["recorded_coverage"]=="fail"
    assert result["paired_comparisons"][0]["match_rate_difference_bounds"]=={"lower":-0.5,"upper":1/6}
    assert result["pareto"]["candidate_ids"]==["baseline"]
    assert not result["automatic_selection_performed"] and not result["execution_authorized"]


def test_complete_repeated_capture_frontier_and_repeat_instability(trial):
    packet,req,_,plan=trial
    captures=[capture(req,cid,r) for cid in ("baseline","cheap") for r in (1,2)]
    result=review(packet,plan,ledger(plan,captures))
    assert result["pareto"]["candidate_ids"]==["cheap"]
    assert result["candidates"][1]["match_bounds"]["lower"]==6
    assert result["candidates"][1]["cost"]["total_microunits"]==30
    assert result["candidates"][1]["latency"]["p95_ms"]==110
    assert result["paired_comparisons"][0]["match_rate_difference_bounds"]=={"lower":2/6,"upper":2/6}
    changed=capture(req,"cheap",2,{"case_1":"transport_failed"})
    captures[-1]=changed
    result=review(packet,plan,ledger(plan,captures))
    assert result["candidates"][1]["per_case"][0]["observed_instability"]
    assert result["candidates"][1]["gates"]["non_delivery_rate"]=="fail"


@pytest.mark.parametrize("mutation",["duplicate","extra_repeat","wrong_configuration","wrong_case","wrong_suite",
                                     "scored_missing","missing_report","duplicate_report","wrong_plan"])
def test_reject_incompatible_or_cherry_picked_capture(trial,mutation):
    packet,req,_,plan=trial
    a=capture(req,"baseline",1);b=capture(req,"cheap",1)
    value=ledger(plan,[a,b])
    if mutation=="duplicate":value["captures"].append(deepcopy(a))
    elif mutation=="extra_repeat":b["repeat"]=3
    elif mutation=="wrong_configuration":b["configuration_sha256"]="a"*64
    elif mutation=="wrong_case":b["attempts"][0]["case_id"]="case_999"
    elif mutation=="wrong_suite":b["report"]["suite_sha256"]="f"*64
    elif mutation=="scored_missing":b["attempts"].pop(0)
    elif mutation=="missing_report":b["report"]=None
    elif mutation=="duplicate_report":b["report"]=deepcopy(a["report"])
    elif mutation=="wrong_plan":value["plan_sha256"]="f"*64
    with pytest.raises(ValueError):review(packet,plan,value)


def test_missing_cost_and_latency_bounds_not_false_pass(trial):
    packet,req,design,_=trial
    design["gates"].update(maximum_total_cost_microunits=8,maximum_p95_elapsed_ms=200)
    plan=freeze(packet,design)
    c=capture(req,"cheap",1,{"case_2":"timeout","case_3":"not_recorded"},{"case_2":(7,500)})
    result=review(packet,plan,ledger(plan,[c]))
    assert result["candidates"][1]["gates"]["total_cost"]=="fail"
    assert result["candidates"][1]["gates"]["p95_latency"]=="fail"
    assert result["candidates"][0]["gates"]["total_cost"]=="unknown"
    assert _latency([10]*19+[None])["p95_upper_ms"]==10
    assert _latency([10]*18+[None,None])["p95_upper_ms"] is None


def test_cli_round_trip_and_exclusive_creation(trial,tmp_path):
    packet,req,design,plan=trial
    root=Path(__file__).resolve().parents[1]
    p=tmp_path/"process.json";d=tmp_path/"design.json";l=tmp_path/"ledger.json"
    f=tmp_path/"plan.json";o=tmp_path/"review.json"
    p.write_text(json.dumps(packet),encoding="utf-8");d.write_text(json.dumps(design),encoding="utf-8")
    l.write_text(json.dumps(ledger(plan,[capture(req,"baseline",1)])),encoding="utf-8")
    base=[sys.executable,"-m","evals.agent_procurement_trials"]
    def call(args):
        return subprocess.run(base+args,cwd=root,capture_output=True,text=True,timeout=30)
    args=["freeze","--process-export",str(p),"--design",str(d),"--out",str(f)]
    assert call(args).returncode==0 and json.loads(f.read_text())==plan
    assert call(args).returncode==2
    assert call(["review","--process-export",str(p),"--plan",str(f),"--captures",str(l),"--out",str(o)]).returncode==0
    assert json.loads(o.read_text())["scheduled_attempts"]==12
